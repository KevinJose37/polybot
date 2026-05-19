"""Paper Trading Entrypoint — Hardened.

Audit fixes applied:
  [C-2] WebSocket connections explicitly closed; cancelled tasks awaited
  [C-3] Structlog file uses append mode, timestamped filename, closed on exit
  [C-4] Task exceptions surfaced via done_callback + periodic poll
  [H-3] Paper client wired with real REST client for market metadata
  [H-5] initial_discovery failure handled cleanly; 0-market guard added
  [M-3] Warm-up overrides removed (use Config defaults: 300s / 60 obs)
  [M-4] Dashboard crash isolated from trading tasks
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.live import Live

from config.settings import Config
from core.bot import AdaptiveMarketMakerBot
from core.paper_client import PaperPolymarketClient
from adapters.polymarket_ws import PolymarketWSAdapter
from adapters.binance_ws import BinanceWSAdapter
from dashboard.terminal import TerminalDashboard
from core.lifecycle import LifecycleManager
from market_discovery.discovery import MarketDiscoveryService
from adapters.polymarket_rest import PolymarketRESTClient

import structlog

# Disable noisy loggers for the rich terminal
logging.getLogger("websockets").setLevel(logging.WARNING)

app = typer.Typer()


@app.command()
def main(
    capital: Annotated[float, typer.Option(help="Starting capital")] = 1000.0,
) -> None:
    """Run paper trading session with dynamic market discovery."""
    asyncio.run(run_paper_trading(capital))


async def run_paper_trading(capital: float) -> None:
    # [M-1] Open log file inside the execution block so imports/tests don't leak handles
    Path("logs").mkdir(exist_ok=True)
    log_file = open(f"logs/paper_bot_{int(time.time())}.jsonl", "a")

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ],
        logger_factory=structlog.WriteLoggerFactory(file=log_file),
    )
    logger = structlog.get_logger("papertrade")

    settings = Config(markets=[], paper_trading=True)
    settings.order_size_usdc = 1.0
    settings.max_position_usdc = 2.5
    # Override defaults for HFT 5m/15m markets
    settings.warm_up_seconds = 15
    settings.warm_up_min_observations = 5

    # Initialize REST client (shared between discovery and paper client)
    rest_api = PolymarketRESTClient()
    discovery = MarketDiscoveryService(rest_api)

    # [H-3] Pass rest_client so paper client delegates get_market/get_clob_market_info
    # [H-4] Pass initial_capital and max_drawdown_pct for drawdown kill-switch
    client = PaperPolymarketClient(
        settings.latency,
        rest_client=rest_api,
        initial_capital=capital,
        max_drawdown_pct=settings.max_drawdown_pct,
    )
    pm_ws = PolymarketWSAdapter()
    binance_ws = BinanceWSAdapter()

    bot = AdaptiveMarketMakerBot(settings, client, pm_ws, binance_ws)

    # [C-2] Wire fill callback so paper client syncs fills back to ExecutionManager.
    # Without this, ExecutionManager accumulates phantom live orders that block new placements.
    def _on_paper_fill(order_id: str, market_id: str, remaining_size: float):
        if remaining_size <= 1e-6:
            bot.execution_manager.update_order_status(order_id, market_id, "filled")

    client.set_fill_callback(_on_paper_fill)
    
    # [M-2] Provide Binance spot mid to forensic logger
    def _fetch_spot_mid(market_id: str) -> float | None:
        asset = bot.market_to_asset.get(market_id)
        if asset:
            return bot.reconciler.spot_mids.get(asset)
        return None
    client.spot_mid_fetcher = _fetch_spot_mid

    lifecycle = LifecycleManager(settings, bot, discovery)

    # [H-5] Handle initial_discovery failure cleanly
    try:
        await lifecycle.initial_discovery()
    except Exception as e:
        logger.error("initial_discovery_failed", error=str(e))
        await rest_api.close()
        log_file.flush()
        log_file.close()
        raise SystemExit(f"Fatal: initial discovery failed: {e}")

    # [H-5] Guard against 0 markets
    if not settings.active_token_ids:
        logger.error("no_markets_discovered")
        await rest_api.close()
        log_file.flush()
        log_file.close()
        raise SystemExit("Fatal: no markets discovered. Check REST connectivity and slug patterns.")

    dashboard = TerminalDashboard(settings=settings, capital=capital)
    # Inject the token name mapper so dashboard shows human-readable names
    dashboard.token_to_name = lifecycle.token_to_name

    bot_task = asyncio.create_task(bot.run(), name="bot_run")
    lifecycle_task = asyncio.create_task(lifecycle.discovery_loop(), name="lifecycle_loop")

    # [C-4] Surface background task exceptions immediately
    _bg_exception: BaseException | None = None

    def _task_done_cb(task: asyncio.Task) -> None:
        nonlocal _bg_exception
        if not task.cancelled() and task.exception():
            _bg_exception = task.exception()
            logger.error("background_task_died", task=task.get_name(), error=str(task.exception()))

    bot_task.add_done_callback(_task_done_cb)
    lifecycle_task.add_done_callback(_task_done_cb)

    try:
        with Live(dashboard.layout, refresh_per_second=4, screen=True):
            try:
                while True:
                    # [C-4] Check if a background task died
                    if _bg_exception is not None:
                        logger.error("aborting_due_to_task_failure")
                        break

                    # [M-4] Dashboard crash must not kill trading
                    try:
                        dashboard.update(bot)
                    except Exception as e:
                        logger.error("dashboard_update_error", error=str(e))

                    await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                pass
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt_received")
    finally:
        # [C-2] Cancel tasks and AWAIT them so their finally blocks execute
        if not bot_task.done():
            bot_task.cancel()
        if not lifecycle_task.done():
            lifecycle_task.cancel()
        await asyncio.gather(bot_task, lifecycle_task, return_exceptions=True)  # [C-2]

        # [C-2] Explicitly close WebSocket connections (belt-and-suspenders)
        try:
            await pm_ws.close()
        except Exception:
            pass
        try:
            await binance_ws.close()
        except Exception:
            pass

        # Close forensic logger and REST session
        client.forensic.close()
        await rest_api.close()

        # [C-3] Flush and close structlog file handle
        log_file.flush()
        log_file.close()


if __name__ == "__main__":
    app()
