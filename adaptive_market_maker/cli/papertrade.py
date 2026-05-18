"""Paper Trading Entrypoint."""

import asyncio
import logging
from typing import Annotated

import typer
from rich.live import Live

from config.settings import Config
from core.bot import AdaptiveMarketMakerBot
from core.paper_client import PaperPolymarketClient
from adapters.polymarket_ws import PolymarketWSAdapter
from adapters.binance_ws import BinanceWSAdapter
from dashboard.terminal import TerminalDashboard

# Disable noisy loggers for the rich terminal
logging.getLogger("websockets").setLevel(logging.WARNING)
# Disable structlog output to console to avoid breaking the Rich Live display
import structlog
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    logger_factory=structlog.WriteLoggerFactory(file=open("paper_bot.jsonl", "w")),
)

app = typer.Typer()

from core.lifecycle import LifecycleManager
from market_discovery.discovery import MarketDiscoveryService
from adapters.polymarket_rest import PolymarketRESTClient

@app.command()
def main(
    capital: Annotated[float, typer.Option(help="Starting capital")] = 1000.0,
) -> None:
    """Run paper trading session with dynamic market discovery."""
    asyncio.run(run_paper_trading(capital))

async def run_paper_trading(capital: float) -> None:
    settings = Config(markets=[], paper_trading=True)
    settings.order_size_usdc = 10.0
    
    # Initialize components
    rest_api = PolymarketRESTClient()
    discovery = MarketDiscoveryService(rest_api)
    
    client = PaperPolymarketClient(settings.latency)
    pm_ws = PolymarketWSAdapter()
    binance_ws = BinanceWSAdapter()
    
    bot = AdaptiveMarketMakerBot(settings, client, pm_ws, binance_ws)
    bot.signal_engine.warm_up_seconds = 5.0
    bot.signal_engine.warm_up_min_obs = 10
    
    lifecycle = LifecycleManager(settings, bot, discovery)
    await lifecycle.initial_discovery()
    
    dashboard = TerminalDashboard(settings=settings, capital=capital)
    # Inject the token name mapper so dashboard shows human-readable names
    dashboard.token_to_name = lifecycle.token_to_name
    
    bot_task = asyncio.create_task(bot.run())
    lifecycle_task = asyncio.create_task(lifecycle.discovery_loop())
    
    with Live(dashboard.layout, refresh_per_second=4, screen=True):
        try:
            while True:
                dashboard.update(bot)
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            pass
        finally:
            if not bot_task.done():
                bot_task.cancel()
            if not lifecycle_task.done():
                lifecycle_task.cancel()
            client.forensic.close()
            await rest_api.close()

if __name__ == "__main__":
    app()
