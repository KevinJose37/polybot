"""
Live trading CLI entry point.
"""
import asyncio
import typer
from rich.live import Live
from typing import Annotated

from bot.settings import Settings
from bot.api.polymarket import PolymarketRESTClient
from bot.api.websocket_client import PolymarketWSClient
from bot.market_discovery.discovery import MarketDiscoveryService
from bot.market_discovery.market_relationships import build_topology
from bot.orderbook.local_book import LocalOrderBook
from bot.orderbook.book_state import BookState
from bot.api.schemas import OrderBookSnapshot
from bot.execution.position_manager import PositionManager
from bot.execution.fill_manager import FillManager
from bot.execution.live_engine import LiveExecutor
from bot.risk.engine import RiskEngine
from bot.arbitrage.scanner import ArbitrageScanner
from bot.dashboard.terminal import TerminalDashboard
from bot.paper_trading.stats import TradingStats
from bot.monitoring.health import HealthServer
from bot.persistence.postgres import DatabaseManager
from bot.persistence.repositories import TradeRepository
import structlog

logger = structlog.get_logger(__name__)

app = typer.Typer()

@app.command()
def main() -> None:
    """Run live trading session."""
    asyncio.run(run_live_trading())

async def run_live_trading() -> None:
    settings = Settings.load()
    
    # Initialize components
    rest_api = PolymarketRESTClient()
    ws_client = PolymarketWSClient()
    discovery = MarketDiscoveryService(rest_api)
    
    position_manager = PositionManager()
    fill_manager = FillManager()
    risk_engine = RiskEngine(settings, position_manager)
    stats = TradingStats()
    
    dashboard = TerminalDashboard(mode="live", capital=settings.starting_capital)
    
    db = DatabaseManager(settings.database_url)
    await db.init_db()

    health_server = HealthServer(
        port=settings.monitoring.health_port,
        ws_connected_fn=lambda: getattr(ws_client, '_running', False),
        books_fn=lambda: (
            sum(1 for b in orderbooks.values() if not b.is_stale()),
            sum(1 for b in orderbooks.values() if b.is_stale()),
        ),
        kill_switch_fn=lambda: risk_engine.kill_switch_active,
        stats_fn=lambda: stats,
    )
    health_task = asyncio.create_task(health_server.start())
    
    # 1. Discover markets
    markets = await discovery.discover_markets()
    topology = build_topology(markets)
    
    scanner = ArbitrageScanner(settings, topology)
    orderbooks: dict[str, LocalOrderBook] = {}
    
    # Initialize orderbooks
    for market in markets:
        for token in market.tokens:
            orderbooks[token.token_id] = LocalOrderBook(token.token_id, stale_threshold_ms=settings.network.stale_feed_threshold_ms)

    # Create executor AFTER orderbooks so they can be passed by reference
    executor = LiveExecutor(
        settings, risk_engine, fill_manager, rest_api, 
        orderbooks=orderbooks, position_manager=position_manager, stats=stats
    )
            
    recent_opportunities = []
    
    async def ws_callback(data: dict) -> None:
        """Handle incoming WebSocket messages."""
        if not isinstance(data, list):
            return
            
        for msg in data:
            if "asset_id" not in msg:
                continue
            token_id = msg["asset_id"]
            book = orderbooks.get(token_id)
            if not book:
                continue
                
            bids = [(float(b["price"]), float(b["size"])) for b in msg.get("bids", [])]
            asks = [(float(a["price"]), float(a["size"])) for a in msg.get("asks", [])]
            
            if book.state == BookState.PENDING:
                snapshot = OrderBookSnapshot(market_id=token_id, bids=bids, asks=asks)
                seq = msg.get("sequence")
                await book.apply_snapshot(snapshot, sequence=seq)
            else:
                seq = msg.get("sequence", 0)
                await book.apply_delta(bids, asks, sequence=seq)
                
        # Run Scanner
        opportunities = scanner.scan(orderbooks)
        
        async def execute_and_persist(opp):
            acks = await executor.execute_opportunity(opp)
            if acks:
                try:
                    async for session in db.get_session():
                        repo = TradeRepository(session)
                        for leg, ack in zip(opp.legs, acks):
                            if ack.status == "FILLED":
                                await repo.add_trade(
                                    opp_id=opp.opportunity_id,
                                    order_id=ack.order_id,
                                    market_id=leg.market_id,
                                    side=leg.side,
                                    price=leg.price,
                                    size=leg.size,
                                    mode="live"
                                )
                        break
                except Exception as e:
                    logger.error("persistence_error", error=str(e))

        for opp in opportunities:
            opp_str = f"[{opp.type.value}] edge={opp.edge*100:.2f}% | size=${opp.size:.2f} | LIVE PENDING"
            recent_opportunities.append(opp_str)
            if len(recent_opportunities) > 4:
                recent_opportunities.pop(0)
            
            # Execute in background
            asyncio.create_task(execute_and_persist(opp))
            
    ws_client.set_callback(ws_callback)
    token_ids = list(orderbooks.keys())
    ws_client.subscribe(token_ids)
    
    ws_task = asyncio.create_task(ws_client.connect_and_run())
    
    async def market_discovery_loop():
        nonlocal markets, topology, token_ids
        while True:
            await asyncio.sleep(60) # Re-discover every 60s
            try:
                new_markets = await discovery.discover_markets()
                if not new_markets:
                    continue
                    
                new_topology = build_topology(new_markets)
                new_token_ids = []
                
                for m in new_markets:
                    for t in m.tokens:
                        new_token_ids.append(t.token_id)
                        if t.token_id not in orderbooks:
                            orderbooks[t.token_id] = LocalOrderBook(t.token_id, stale_threshold_ms=settings.network.stale_feed_threshold_ms)
                
                # Subscribe to newly discovered tokens
                tokens_to_sub = set(new_token_ids) - set(token_ids)
                if tokens_to_sub:
                    ws_client.subscribe(list(tokens_to_sub))
                    
                # Update references safely
                markets = new_markets
                topology = new_topology
                scanner.topology = new_topology
                token_ids = new_token_ids
                
                # Market resolution check
                active_token_ids = {t.token_id for m in new_markets for t in m.tokens}
                for mid in list(orderbooks.keys()):
                    if mid not in active_token_ids:
                        logger.warning("market_no_longer_active", market_id=mid)
                        for oid, data in list(fill_manager.inflight_orders.items()):
                            if data.get("market") == mid:
                                await executor.cancel_order(oid)
                                fill_manager.remove_inflight_order(oid)
            except Exception as e:
                logger.error("discovery_loop_error", error=str(e))
                
    discovery_task = asyncio.create_task(market_discovery_loop())

    async def order_ttl_loop():
        """Cancel orders that exceed the configured timeout."""
        while True:
            await asyncio.sleep(5)
            try:
                expired = fill_manager.check_expired_orders(settings.execution.order_timeout_s)
                for order_id in expired:
                    await executor.cancel_order(order_id)
                    fill_manager.remove_inflight_order(order_id)
            except Exception as e:
                logger.error("order_ttl_loop_error", error=str(e))

    ttl_task = asyncio.create_task(order_ttl_loop())
    
    with Live(dashboard.layout, refresh_per_second=2, screen=True):
        try:
            while True:
                health = {
                    "WS": ws_client._running,
                    "RISK": not risk_engine.kill_switch_active
                }
                dashboard.update(position_manager, markets, orderbooks, recent_opportunities, health, stats=stats)
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        finally:
            await ws_client.close()
            await rest_api.close()
            if not ws_task.done():
                ws_task.cancel()
            if not discovery_task.done():
                discovery_task.cancel()
            if not ttl_task.done():
                ttl_task.cancel()
            if not health_task.done():
                health_task.cancel()

if __name__ == "__main__":
    app()
