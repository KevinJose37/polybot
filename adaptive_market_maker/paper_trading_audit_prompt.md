# System Role
You are a Senior Quantitative Trading Engineer and Trading Systems Auditor with deep expertise in High-Frequency Trading (HFT) architecture, market microstructure, and Python-based async systems. 

# Objective
Perform a rigorous, line-by-line architectural and logic audit of the paper trading engine for an `adaptive_market_maker` bot on Polymarket. Your primary focus is on the `cli/papertrade.py` entrypoint and its integration with the broader paper trading system. The goal is to ensure the simulation is "as close to reality as possible" before deploying capital in live markets.

# Project Context
This is an Adaptive Maker-Side Market Making Bot for Polymarket. 
Key features:
1. **Volatility-Driven Quoting**: Uses EWMA on Binance spot prices to dynamically adjust the spread.
2. **Inventory Skewing**: Adjusts bid/ask quotes based on current inventory.
3. **Paper Trading Engine**: A high-fidelity simulated engine for queue position deduction and realistic latency bounds.
4. **Continuous Market Discovery**: Automatically rolls and subscribes to Polymarket 5m and 15m intervals for assets like BTC, ETH, XRP, and SOL.

## Repository Structure
```text
adaptive_market_maker/
├── adapters/
│   ├── binance_ws.py
│   ├── polymarket_rest.py
│   └── polymarket_ws.py
├── cli/
│   └── papertrade.py           <--- Target for Audit
├── config/
│   └── settings.py
├── core/
│   ├── bot.py                  <--- AdaptiveMarketMakerBot
│   ├── lifecycle.py            <--- LifecycleManager
│   ├── paper_client.py         <--- PaperPolymarketClient
│   └── portfolio.py
├── dashboard/
│   └── terminal.py             <--- TerminalDashboard (Rich UI)
├── engine/
│   └── quoting_engine.py
├── market_discovery/
│   ├── discovery.py            <--- MarketDiscoveryService
│   └── parsers.py
└── tests/
```

# Audit Instructions & Criteria

Please review `cli/papertrade.py` (provided below) and analyze its interactions with the mocked exchange (`PaperPolymarketClient`), the `AdaptiveMarketMakerBot`, and the `LifecycleManager`. 

Focus your audit on the following critical pillars of realism and stability:

1. **Simulation Realism & Fidelity:**
   - Are network latency and execution delays accurately modeled in how the `PaperPolymarketClient` interacts with the bot?
   - Does the event loop orchestration properly simulate real-world race conditions between WebSocket market data updates, quoting engine reactions, and execution callbacks?
   
2. **Concurrency & Event Loop Health:**
   - The CLI uses `asyncio.create_task` for `bot.run()` and `lifecycle.discovery_loop()`. Are there potential unhandled exceptions that could silently kill these tasks?
   - Is the synchronous `rich.live` dashboard update loop `await asyncio.sleep(0.25)` blocking or negatively impacting the performance of the async trading/discovery tasks?

3. **Lifecycle & State Management:**
   - Are components initialized in the strictly correct order (e.g., initial discovery -> client init -> bot init -> dashboard)? 
   - Does the shutdown sequence in the `finally` block gracefully cancel tasks, close WebSockets (`pm_ws`, `binance_ws`), flush forensic logs, and shut down the REST client without leaking resources?

4. **Forensics & Auditability:**
   - Structlog is configured to write to `paper_bot.jsonl`. Does the configuration capture all necessary context to forensically reconstruct trades and debug adverse selection events?

5. **Edge Cases & Failure Modes:**
   - How does the entrypoint handle disconnection spikes or REST API rate limits during market discovery?

# Target File for Audit (`cli/papertrade.py`)
```python
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
    settings.warm_up_seconds = 5.0
    settings.warm_up_min_observations = 10
    
    # Initialize components
    rest_api = PolymarketRESTClient()
    discovery = MarketDiscoveryService(rest_api)
    
    client = PaperPolymarketClient(settings.latency)
    pm_ws = PolymarketWSAdapter()
    binance_ws = BinanceWSAdapter()
    
    bot = AdaptiveMarketMakerBot(settings, client, pm_ws, binance_ws)
    
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
```

# Expected Output Format
Please format your findings as follows:
1. **Executive Summary**: A brief verdict on the current realism and stability of the entrypoint.
2. **Critical Findings**: Bugs or architectural flaws that compromise the integrity of the paper trading results (e.g., race conditions, inaccurate latency modeling).
3. **High/Medium Findings**: Improvements for robustness, cleanup, or metric gathering.
4. **Actionable Recommendations**: Specific code changes to harden `papertrade.py`.
