"""Live trading CLI entry point."""
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
from bot.execution.live_engine import LiveExecutor
from bot.risk.engine import RiskEngine
from bot.arbitrage.scanner import ArbitrageScanner
from bot.dashboard.terminal import TerminalDashboard
from bot.paper_trading.stats import TradingStats
from bot.monitoring.health import HealthServer
from bot.monitoring.forensic import ForensicLogger
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
        log_dir / "live_trading.log",
        maxBytes=50 * 1024 * 1024,
        backupCount=1,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.setLevel(logging.DEBUG)


@app.command()
def main() -> None:
    """Run live trading session."""
    _setup_file_logging()
    asyncio.run(run_live_trading())

async def run_live_trading() -> None:
    settings = Settings.load()
    
    # Initialize components — pass API credentials for authenticated endpoints
    rest_api = PolymarketRESTClient(
        api_key=settings.polymarket_api_key,
        api_secret=settings.polymarket_api_secret,
        api_passphrase=settings.polymarket_api_passphrase,
    )
    ws_client = PolymarketWSClient()
    discovery = MarketDiscoveryService(rest_api)
    
    # Fetch real account balance from Polymarket
    real_balance = await rest_api.get_balance_allowance()
    if real_balance is not None:
        settings.starting_capital = real_balance
        logger.info("live_balance_fetched", balance=f"${real_balance:,.2f}")
    else:
        logger.warning("live_balance_fetch_failed", fallback=f"${settings.starting_capital:,.2f}")
    
    position_manager = PositionManager()
    fill_manager = FillManager()
    risk_engine = RiskEngine(settings, position_manager)
    fee_rates: dict[str, float] = {}
    stats = TradingStats()
    forensic = ForensicLogger()
    
    dashboard = TerminalDashboard(mode="live", capital=settings.starting_capital)
    
    db = DatabaseManager(settings.database_url)
    await db.init_db()

    orderbooks: dict[str, LocalOrderBook] = {}

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
    
    # 2. Fetch per-token fee rates from Polymarket API
    async def fetch_fee_rate(tid: str):
        rate = await rest_api.get_fee_rate(tid)
        fee_rates[tid] = rate if rate is not None else settings.trading.polymarket_fee
    
    # 3. Initialize orderbooks with REST snapshots
    async def init_book(tid: str):
        book = LocalOrderBook(tid, stale_threshold_ms=settings.network.stale_feed_threshold_ms)
        orderbooks[tid] = book
        snapshot = await rest_api.get_orderbook(tid)
        await book.apply_snapshot(snapshot)

    init_tasks = [init_book(t.token_id) for m in markets for t in m.tokens]
    if init_tasks:
        await asyncio.gather(*init_tasks)
    
    fee_tasks = [fetch_fee_rate(t.token_id) for m in markets for t in m.tokens]
    if fee_tasks:
        await asyncio.gather(*fee_tasks)
    logger.info("fee_rates_loaded", count=len(fee_rates))
    
    # 4. Register parity pairs so position manager values YES+NO at $1.00
    for market in markets:
        if len(market.tokens) == 2:
            position_manager.register_parity_pair(market.tokens[0].token_id, market.tokens[1].token_id)
    
    scanner = ArbitrageScanner(settings, topology, fee_rates=fee_rates)

    # Create executor AFTER orderbooks so they can be passed by reference
    executor = LiveExecutor(
        settings, risk_engine, fill_manager, rest_api, 
        orderbooks=orderbooks, position_manager=position_manager, stats=stats,
        fee_rates=fee_rates, forensic=forensic,
    )
            
    recent_opportunities: list[dict] = []
    
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

                    ts = int(msg.get("timestamp", 0)) or 0
                    if book.state == BookState.PENDING:
                        continue
                    await book.apply_delta(bids, asks, sequence=ts)

            elif event_type in ("last_trade_price", "best_bid_ask", "tick_size_change"):
                pass
                
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
                                    mode="live"
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
            
            asyncio.create_task(execute_and_persist(opp, opp_data))
            
    ws_client.set_callback(ws_callback)
    token_ids = list(orderbooks.keys())
    ws_client.subscribe(token_ids)
    
    ws_task = asyncio.create_task(ws_client.connect_and_run())
    
    async def market_discovery_loop():
        nonlocal markets, topology, token_ids
        while True:
            await asyncio.sleep(60)
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
                    
                    async def fetch_new_fee_rate(tid: str):
                        rate = await rest_api.get_fee_rate(tid)
                        fee_rates[tid] = rate if rate is not None else settings.trading.polymarket_fee
                        
                    await asyncio.gather(*(init_new_book(tid) for tid in tokens_to_sub))
                    await asyncio.gather(*(fetch_new_fee_rate(tid) for tid in tokens_to_sub))
                    ws_client.subscribe(list(tokens_to_sub))
                    
                # Update references safely
                markets = new_markets
                topology = new_topology
                scanner.topology = new_topology
                token_ids = new_token_ids
                
                # Register parity pairs for new markets
                for m in new_markets:
                    if len(m.tokens) == 2:
                        position_manager.register_parity_pair(m.tokens[0].token_id, m.tokens[1].token_id)
                
                # Market resolution check
                active_token_ids = {t.token_id for m in new_markets for t in m.tokens}
                resolved_tokens = [mid for mid in list(orderbooks.keys()) if mid not in active_token_ids]
                
                for mid in resolved_tokens:
                    logger.info("market_resolved", market_id=mid[:12])
                    for oid, data in list(fill_manager.inflight_orders.items()):
                        if data.get("market") == mid:
                            await executor.cancel_order(oid)
                            fill_manager.remove_inflight_order(oid)
                    
                    # Determine resolution price for parity pairs
                    complement_id = position_manager.parity_pairs.get(mid)
                    book = orderbooks.get(mid)
                    
                    if complement_id and complement_id in resolved_tokens:
                        if book and book.bids:
                            last_bid = max(book.bids.keys())
                            settle_price = 1.0 if last_bid > 0.5 else 0.0
                        else:
                            settle_price = 0.5
                    else:
                        settle_price = 0.5
                    
                    position_manager.settle_market(mid, settle_price=settle_price)
                    
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
                # Update mark-to-market with current mid prices
                mid_prices = {}
                for tid, book in orderbooks.items():
                    bid = book.best_bid()
                    ask = book.best_ask()
                    if bid is not None and ask is not None:
                        mid_prices[tid] = (bid + ask) / 2.0
                    elif bid is not None:
                        mid_prices[tid] = bid
                    elif ask is not None:
                        mid_prices[tid] = ask
                position_manager.update_all_mtm(mid_prices)

                health = {
                    "WS": ws_client._running,
                    "RISK": not risk_engine.kill_switch_active
                }
                dashboard.update(position_manager, markets, orderbooks, recent_opportunities, health, stats=stats)
                
                # Check for silent WebSocket drops
                try:
                    await ws_client.check_stale(silence_window_ms=30000)
                except Exception:
                    logger.warning("ws_reconnecting_stale_feed")
                    if ws_client._ws:
                        await ws_client._ws.close()
                
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        finally:
            forensic.close()
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
