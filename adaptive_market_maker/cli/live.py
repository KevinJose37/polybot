"""Live Trading Entrypoint."""

import asyncio
import logging
from typing import Annotated

import typer
from rich.live import Live

from config.settings import Config
from core.bot import AdaptiveMarketMakerBot
from core.interfaces import PolymarketClientProtocol
from adapters.polymarket_ws import PolymarketWSAdapter
from adapters.binance_ws import BinanceWSAdapter
from dashboard.terminal import TerminalDashboard

# Disable noisy loggers for the rich terminal
logging.getLogger("websockets").setLevel(logging.WARNING)

app = typer.Typer()

class DummyLiveClient(PolymarketClientProtocol):
    """Stub for real polymarket api client in Stage 8"""
    def __init__(self):
        self._inventory = {}
    async def fetch_inventory(self, market_id: str) -> float: return 0.0
    def get_inventory(self, market_id: str) -> float: return 0.0
    async def place_order(self, market_id: str, side: str, price: float, size: float) -> str: return "stub"
    async def cancel_order(self, order_id: str, market_id: str) -> bool: return True

@app.command()
def main(
    capital: Annotated[float, typer.Option(help="Starting capital")] = 1000.0,
    markets: Annotated[list[str], typer.Option(help="Markets to quote")] = ["BTC-24H"]
) -> None:
    """Run live trading session."""
    asyncio.run(run_live_trading(capital, markets))

async def run_live_trading(capital: float, markets: list[str]) -> None:
    settings = Config(markets=markets, paper_trading=False)
    
    client = DummyLiveClient()
    pm_ws = PolymarketWSAdapter()
    binance_ws = BinanceWSAdapter()
    
    bot = AdaptiveMarketMakerBot(settings, client, pm_ws, binance_ws)
    dashboard = TerminalDashboard(settings=settings, capital=capital)
    
    bot_task = asyncio.create_task(bot.run())
    
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

if __name__ == "__main__":
    app()
