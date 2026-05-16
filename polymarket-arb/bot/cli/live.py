"""Live trading CLI entry point."""
import asyncio
import logging
from pathlib import Path
import typer
from rich.live import Live

from bot.settings import Settings
from bot.api.polymarket import PolymarketRESTClient
from bot.api.websocket_client import PolymarketWSClient
from bot.market_discovery.discovery import MarketDiscoveryService
from bot.orderbook.local_book import LocalOrderBook
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
from bot.execution.events import MarketEventHandler
from bot.execution.lifecycle import LifecycleManager
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
    
    scanner = ArbitrageScanner(settings, topology=None, fee_rates=fee_rates)

    executor = LiveExecutor(
        settings, risk_engine, fill_manager, rest_api, 
        orderbooks=orderbooks, position_manager=position_manager, stats=stats,
        fee_rates=fee_rates, forensic=forensic,
    )
            
    recent_opportunities: list[dict] = []
    
    lifecycle = LifecycleManager(
        settings=settings,
        discovery=discovery,
        rest_api=rest_api,
        ws_client=ws_client,
        scanner=scanner,
        position_manager=position_manager,
        fill_manager=fill_manager,
        executor=executor,
        orderbooks=orderbooks,
        fee_rates=fee_rates
    )
    await lifecycle.initial_discovery()
    
    event_handler = MarketEventHandler(
        orderbooks=orderbooks,
        scanner=scanner,
        executor=executor,
        stats=stats,
        recent_opportunities=recent_opportunities,
        db=db,
        mode="live"
    )
    
    ws_client.set_callback(event_handler.handle_message)
    token_ids = list(orderbooks.keys())
    if token_ids:
        ws_client.subscribe(token_ids)
    
    ws_task = asyncio.create_task(ws_client.connect_and_run())
    discovery_task = asyncio.create_task(lifecycle.discovery_loop())

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
                dashboard.update(position_manager, lifecycle.markets, orderbooks, recent_opportunities, health, stats=stats, warmup_until_ms=event_handler._warmup_until_ms)
                
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
            await event_handler.shutdown()
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
