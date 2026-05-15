# Polymarket Arbitrage Bot (`polymarket-arb`)

## System Overview
`polymarket-arb` is a high-frequency, combinatorial arbitrage trading bot designed for Polymarket. It targets short-term cryptocurrency prediction markets (specifically 5-minute and 15-minute resolution markets for BTC, ETH, SOL, and XRP). 

The bot is built in Python 3.12+ and utilizes an asynchronous, event-driven architecture powered by `asyncio`. It maintains local orderbooks in real-time via WebSockets and executes zero-risk or mathematically constrained combinatorial arbitrage strategies. It features a high-fidelity paper trading execution engine that models latency, L2 depth/slippage, and execution friction to eliminate optimistic bias before live deployment.

## Repository Structure (`/bot`)
The core application logic resides in the `bot/` directory.

- `api/`: Interfaces for Polymarket's REST and WebSocket APIs. Contains data schemas (`schemas.py`) and the `PolymarketWSClient` for consuming L2 depth.
- `arbitrage/`: Core business logic for detecting arbitrage opportunities. 
  - `scanner.py`: Orchestrator that polls local orderbooks and triggers pure detector functions.
  - `parity.py`: Type A arbitrage detector (Yes/No spread).
  - `monotonicity.py`: Type B arbitrage detector (Cross-interval).
  - `exhaustive_sets.py`: Type C arbitrage detector.
- `cli/`: Typer-based command-line interfaces.
  - `papertrade.py`: Entry point for the paper trading simulator.
  - `live.py`: Entry point for production live trading.
- `dashboard/`: Rich-based terminal UI (`terminal.py`) for real-time observability, displaying live PnL, orderbook status, and network health.
- `execution/`: Order lifecycle and state management. Includes `position_manager.py` (tracks inventory/PnL) and `fill_manager.py` (handles order TTL and inflight tracking).
- `market_discovery/`: Fetches and filters active markets matching the target slug patterns (e.g., `{asset}-updown-{timeframe}-{timestamp}`) and builds a `MarketTopology` graph.
- `monitoring/`: Health endpoints (`health.py`) for Prometheus/external monitoring, exposing websocket status and kill-switch state.
- `orderbook/`: L2 orderbook management. `local_book.py` applies snapshots and deltas incrementally to maintain state.
- `paper_trading/`: High-fidelity execution simulator (`engine.py`) with latency modeling and performance statistics tracking (`stats.py`).
- `persistence/`: Async SQLAlchemy repositories for persisting trade histories to PostgreSQL/SQLite.
- `risk/`: The `RiskEngine` (`engine.py`) enforces strict microstructure constraints.
- `utils/`: Helpers for deterministic clocks, logging, and math.

## Arbitrage Strategies
The bot identifies pricing inefficiencies across different market topologies:

1. **Type A (Parity / Zero-Sum)**: Assesses the `YES` and `NO` tokens of a single market. If the sum of the best asks (accounting for fees and slippage) is less than $1.00, it guarantees a risk-free profit by buying both sides.
2. **Type B (Monotonicity / Time-Series)**: Exploits pricing inconsistencies across different timeframes of the same asset (e.g., a 5m market and a 15m market with the same strike price). It enforces the constraint that if the 5m market resolves to YES, the 15m market *must* resolve to YES.
3. **Type C (Exhaustive Sets)**: Scans multiple mutually exclusive outcomes where the sum of probabilities should equal 1 (e.g., price ranges). Buys the entire set if the total cost is below $1.00.

## Risk Management & Guardrails
The `RiskEngine` acts as an absolute blocker before any order execution:
- **Global Kill Switch**: File-based kill switch (`.kill_switch`). If activated, halts all trading.
- **Drawdown Limits**: Halts execution if the daily max drawdown is breached.
- **Exposure Limits**: Enforces strict maximum exposure per asset and portfolio-wide exposure caps to avoid over-leveraging on a single leg.
- **Stale Feed Circuit Breaker**: Discards opportunities if the underlying orderbook data is stale (no updates within `stale_feed_threshold_ms`).

## Execution & Data Pipeline
1. **Discovery**: `MarketDiscoveryService` polls the REST API for relevant markets and builds a topology graph.
2. **Ingestion**: `PolymarketWSClient` subscribes to market IDs, receiving `book` snapshots and `price_change` deltas. It normalizes this data into `LocalOrderBook` instances.
3. **Scanning**: `ArbitrageScanner` evaluates the current state of `LocalOrderBook`s continuously, emitting `ArbOpportunity` objects when profitability exceeds the `min_edge` threshold.
4. **Execution**: Opportunities are passed to the `PaperExecutor` or `LiveEngine`, checked against the `RiskEngine`, and filled based on available L2 volume.
5. **Observability**: `TerminalDashboard` handles the standard output using `Rich`, while verbose/debug logging is piped to rotating file logs (`logs/paper_trading.log`) using `structlog` to prevent terminal UI corruption.

## Configuration & Environment
The bot uses `pydantic-settings` to load hierarchical configurations from `bot/settings.py`. Values can be overridden via `.env` or `config/default.toml`.
- **Trading**: Fees, slippage estimates, minimum edge margins.
- **Network**: Websocket backoff limits, stale feed thresholds.
- **Risk**: Kill switch paths, exposure limits.

## Quickstart

### Prerequisites
- Python >= 3.12
- `make`

### Setup
```bash
make setup
cp .env.example .env
# Edit .env with your PostgreSQL/Redis connection strings if not using local SQLite
```

### Running the Paper Trader
```bash
make papertrade
# Or manually:
# python -m bot.cli.papertrade --capital 1000
```
This launches the rich terminal dashboard, initiates market discovery, and connects to the WebSocket streams to simulate combinatorial arbitrage.

### Testing & Linting
```bash
make lint
make test
```
