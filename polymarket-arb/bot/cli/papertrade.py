"""
Paper trading CLI entry point.
"""
import asyncio
import logging
from pathlib import Path
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
from bot.paper_trading.engine import PaperExecutor
from bot.paper_trading.stats import TradingStats
from bot.risk.engine import RiskEngine
from bot.arbitrage.scanner import ArbitrageScanner
from bot.dashboard.terminal import TerminalDashboard
from bot.monitoring.health import HealthServer
from bot.persistence.postgres import DatabaseManager
from bot.persistence.repositories import TradeRepository
import structlog

logger = structlog.get_logger(__name__)

app = typer.Typer()


def _setup_file_logging() -> None:
    """Redirect all logs to a file so they don't corrupt the Rich dashboard."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_dir / "paper_trading.log",
        maxBytes=50 * 1024 * 1024,  # 50 MB
        backupCount=1,              # keep at most 1 old file
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers.clear()          # remove any default stderr/stdout handlers
    root.addHandler(file_handler)
    root.setLevel(logging.DEBUG)


@app.command()
def main(
    capital: Annotated[float, typer.Option(help="Starting capital for paper trading")] = 1000.0,
    reset: Annotated[bool, typer.Option(help="Reset paper trading database")] = False
) -> None:
    """Run paper trading session."""
    _setup_file_logging()
    asyncio.run(run_paper_trading(capital, reset))

async def run_paper_trading(capital: float, reset: bool) -> None:
    settings = Settings.load()
    settings.starting_capital = capital
    
    # Initialize components
    rest_api = PolymarketRESTClient()
    ws_client = PolymarketWSClient()
    discovery = MarketDiscoveryService(rest_api)
    
    position_manager = PositionManager()
    fill_manager = FillManager()
    risk_engine = RiskEngine(settings, position_manager)
    orderbooks: dict[str, LocalOrderBook] = {}
    stats = TradingStats()
    executor = PaperExecutor(settings, risk_engine, position_manager, fill_manager, orderbooks, stats=stats)
    
    dashboard = TerminalDashboard(mode="paper", capital=capital)
    
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
    
    # Initialize orderbooks
    async def init_book(tid: str):
        book = LocalOrderBook(tid, stale_threshold_ms=settings.network.stale_feed_threshold_ms)
        orderbooks[tid] = book
        snapshot = await rest_api.get_orderbook(tid)
        await book.apply_snapshot(snapshot)

    init_tasks = [init_book(t.token_id) for m in markets for t in m.tokens]
    if init_tasks:
        await asyncio.gather(*init_tasks)
            
    recent_opportunities = []
    
    async def ws_callback(data: dict) -> None:
        """Handle incoming WebSocket messages.
        
        Polymarket WS sends individual JSON objects with an event_type field:
        - "book": full orderbook snapshot with bids/asks arrays
        - "price_change": delta updates via price_changes array
        """
        # Normalize to a list for uniform processing
        if isinstance(data, list):
            messages = data
        elif isinstance(data, dict):
            messages = [data]
        else:
            return

        for msg in messages:
            event_type = msg.get("event_type", "")

            if event_type == "book":
                # Full book snapshot: {event_type, asset_id, bids: [{price, size}], asks: [{price, size}], hash, timestamp}
                token_id = msg.get("asset_id")
                if not token_id:
                    continue
                book = orderbooks.get(token_id)
                if not book:
                    continue

                bids = [(float(b["price"]), float(b["size"])) for b in msg.get("bids", [])]
                asks = [(float(a["price"]), float(a["size"])) for a in msg.get("asks", [])]
                snapshot = OrderBookSnapshot(market_id=token_id, bids=bids, asks=asks)
                await book.apply_snapshot(snapshot)

            elif event_type == "price_change":
                # Delta update: {event_type, market, timestamp, price_changes: [{asset_id, price, size, side, ...}]}
                for change in msg.get("price_changes", []):
                    token_id = change.get("asset_id")
                    if not token_id:
                        continue
                    book = orderbooks.get(token_id)
                    if not book:
                        continue

                    price = float(change["price"])
                    size = float(change["size"])
                    side = change.get("side", "").upper()

                    if side == "BUY":
                        bids = [(price, size)]
                        asks = []
                    elif side == "SELL":
                        bids = []
                        asks = [(price, size)]
                    else:
                        continue

                    # price_change events don't carry a sequence number;
                    # use a monotonically increasing timestamp instead
                    ts = int(msg.get("timestamp", 0)) or 0
                    if book.state == BookState.PENDING:
                        # Wait for a full "book" event before applying deltas
                        continue
                    await book.apply_delta(bids, asks, sequence=ts)

            elif event_type in ("last_trade_price", "best_bid_ask", "tick_size_change"):
                # Informational events we don't need for arb scanning — ignore
                pass
            else:
                # Unknown event type; log at debug level
                logger.debug("ws_unknown_event", event_type=event_type, keys=list(msg.keys()))
                
        # Run Scanner
        opportunities = scanner.scan(orderbooks)
        
        async def execute_and_persist(opp, opp_data):
            acks = await executor.execute_opportunity(opp)
            if not acks:
                opp_data["status"] = "REJECTED"
                opp_data["color"] = "red"
            else:
                all_filled = all(ack.status == "FILLED" for ack in acks)
                any_rejected = any(ack.status == "REJECTED" for ack in acks)
                if all_filled:
                    opp_data["status"] = "FILLED"
                    opp_data["color"] = "green"
                elif any_rejected:
                    opp_data["status"] = "LEG IMBALANCE"
                    opp_data["color"] = "red"
                else:
                    opp_data["status"] = "PARTIAL"
                    opp_data["color"] = "yellow"

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
                                    mode="paper"
                                )
                        break
                except Exception as e:
                    logger.error("persistence_error", error=str(e))

        for opp in opportunities:
            stats.record_opportunity_detected()
            opp_data = {
                "type": opp.type.value,
                "edge": opp.edge,
                "size": opp.size,
                "status": "PENDING",
                "color": "yellow"
            }
            recent_opportunities.append(opp_data)
            if len(recent_opportunities) > 8:
                recent_opportunities.pop(0)
            
            # Execute in background
            asyncio.create_task(execute_and_persist(opp, opp_data))
            
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
                
                # Subscribe to newly discovered tokens
                tokens_to_sub = set(new_token_ids) - set(token_ids)
                if tokens_to_sub:
                    async def init_new_book(tid: str):
                        book = LocalOrderBook(tid, stale_threshold_ms=settings.network.stale_feed_threshold_ms)
                        orderbooks[tid] = book
                        snapshot = await rest_api.get_orderbook(tid)
                        await book.apply_snapshot(snapshot)
                        
                    await asyncio.gather(*(init_new_book(tid) for tid in tokens_to_sub))
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
                                
                        # Settle position and free capital
                        position_manager.settle_market(mid)
                        
                        # Remove from orderbooks to stop processing WS messages
                        if mid in orderbooks:
                            del orderbooks[mid]
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
                # Update dashboard
                health = {"WS": ws_client._running}
                dashboard.update(position_manager, markets, orderbooks, recent_opportunities, health, stats=stats)
                
                # Check for silent WebSocket drops
                try:
                    await ws_client.check_stale(silence_window_ms=30000)
                except Exception as e:
                    logger.warning("ws_reconnecting_due_to_stale_feed")
                    if ws_client._ws:
                        await ws_client._ws.close()
                
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
