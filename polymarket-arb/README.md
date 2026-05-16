# Polymarket Arbitrage Bot (`polymarket-arb`)

## System Overview

`polymarket-arb` is a high-frequency, combinatorial arbitrage trading bot designed for [Polymarket](https://polymarket.com). It targets short-term cryptocurrency prediction markets — specifically **5-minute** and **15-minute** resolution up/down markets for **BTC, ETH, SOL, and XRP**.

The bot is built in **Python 3.12+** and utilizes an asynchronous, event-driven architecture powered by `asyncio`. It maintains local L2 orderbooks in real-time via WebSockets and executes zero-risk or mathematically constrained combinatorial arbitrage strategies. It features a high-fidelity paper trading execution engine that models latency, L2 depth/slippage, and execution friction to eliminate optimistic bias before live deployment.

---

## Table of Contents

- [Repository Structure](#repository-structure)
- [Arbitrage Strategies](#arbitrage-strategies)
- [Fee Model](#fee-model)
- [Execution & Data Pipeline](#execution--data-pipeline)
- [Risk Management & Guardrails](#risk-management--guardrails)
- [Configuration System](#configuration-system)
- [Parameter Reference](#parameter-reference)
  - [Top-Level Settings](#1-top-level-settings)
  - [Trading Parameters](#2-trading-parameters-tradingsettings)
  - [Network Parameters](#3-network-parameters-networksettings)
  - [Execution Parameters](#4-execution-parameters-executionsettings)
  - [Paper Trading Parameters](#5-paper-trading-parameters-papertradingsettings)
  - [Risk Parameters](#6-risk-parameters-risksettings)
  - [API Parameters](#7-api-parameters-apisettings)
  - [Monitoring Parameters](#8-monitoring-parameters-monitoringsettings)
- [CLI Arguments](#cli-arguments)
- [Quickstart](#quickstart)
- [Testing & Linting](#testing--linting)

---

## Repository Structure

```
polymarket-arb/
├── bot/
│   ├── api/              # REST + WebSocket client interfaces, data schemas
│   ├── arbitrage/        # Core detectors: parity, monotonicity, exhaustive sets
│   │   ├── scanner.py    # Orchestrator — polls orderbooks, invokes detectors
│   │   ├── parity.py     # Type A detector (Yes/No spread)
│   │   ├── monotonicity.py   # Type B detector (cross-timeframe)
│   │   └── exhaustive_sets.py # Type C detector (exhaustive parity)
│   ├── cli/
│   │   ├── papertrade.py # Entry point for paper trading
│   │   └── live.py       # Entry point for live trading
│   ├── dashboard/        # Rich terminal UI for real-time observability
│   ├── execution/        # Position manager, fill manager, live engine
│   ├── market_discovery/ # REST-based market discovery + topology graph
│   ├── monitoring/       # HTTP health + Prometheus /metrics endpoint
│   ├── orderbook/        # L2 local orderbook with snapshot/delta management
│   ├── paper_trading/    # Simulated executor: latency, slippage, fill models
│   ├── persistence/      # Async SQLAlchemy repos (PostgreSQL/SQLite)
│   ├── risk/             # Risk engine: kill switch, drawdown, exposure caps
│   ├── utils/            # Deterministic clocks, Kelly math, logging
│   ├── constants.py      # Target assets, directions, timeframes
│   └── settings.py       # Pydantic-settings configuration hierarchy
├── config/
│   └── default.toml      # Default parameter overrides
├── logs/                 # Rotating file logs (paper_trading.log)
├── tests/                # pytest + pytest-asyncio test suite
├── .env.example          # Environment variable template
├── Makefile              # Build/run shortcuts
└── pyproject.toml        # Project metadata & dependencies
```

---

## Arbitrage Strategies

### Type C — Exhaustive Set Parity *(primary strategy)*

For a binary market (UP/DOWN), the constraint `P(UP) + P(DOWN) = 1.0` must hold. The detector checks two directions:

- **BUY parity**: `edge = 1.0 − (up_ask + buy_fee + slippage) − (down_ask + buy_fee + slippage)` — if both asks sum to less than $1.00 after fees, buy both sides for guaranteed payout.
- **SELL parity**: `edge = (up_bid − slippage) + (down_bid − slippage) − 1.0` — if both bids sum to more than $1.00, sell both sides. **Sell orders have no taker fees.**

The higher-edge direction is selected.

> **Note:** Type A (simple Yes/No parity) is subsumed by Type C. The scanner runs only Type C to prevent double-execution on the same BUY-side dislocation.

### Type B — Cross-Timeframe Monotonicity

Exploits pricing inconsistencies between 5m and 15m markets for the same asset. If the 5m YES bid exceeds the 15m YES ask (after fees/slippage), this violates the monotonicity constraint (shorter timeframe ≤ longer timeframe probability). The bot sells the overpriced 5m leg (fee-free) and buys the underpriced 15m leg (pays taker fee).

`edge = (bid_5m − slippage) − (ask_15m + buy_fee + slippage)`

---

## Fee Model

The bot implements **Polymarket's real fee formula** ([docs](https://help.polymarket.com/en/articles/13364478-trading-fees)):

```
fee = C × p × feeRate × (p × (1 − p))^exponent
```

Where:
- **C** = number of shares traded
- **p** = trade price
- **feeRate** = rate fetched from `/fee-rate` API (e.g., `0.03`)
- **exponent** = `1`

**Key rules:**
- **Sell orders are NOT subject to taker fees.** This is a significant advantage for sell-side arbitrage.
- Fees are rounded to 4 decimal places; minimum is `0.0001 pUSD`. Smaller fees round to zero.
- Peak effective fee is **~1.80%** at the 50/50 price point (`p = 0.50`).
- Fees approach zero at the extremes (`p` near `0.01` or `0.99`).
- Geopolitical & World Events markets are **fee-free**.

### Dynamic Per-Token Fee Rates

The bot fetches fee rates dynamically from Polymarket's API at startup and during market discovery:

```
GET https://clob.polymarket.com/fee-rate?token_id={token_id}
```

Each token gets its own fee rate stored in a `fee_rates: dict[str, float]` shared across scanner and executors. If the API call fails, the bot falls back to the configured `polymarket_fee` from settings.

---

## Execution & Data Pipeline

1. **Discovery** — `MarketDiscoveryService` polls the REST API every 60s for markets matching `{asset}-updown-{timeframe}-{timestamp}` patterns. Builds a `MarketTopology` graph of parity and monotonicity relationships.
2. **Fee Loading** — For each discovered token, fetches the fee rate from `/fee-rate`. Stores per-token rates in a shared dict.
3. **Ingestion** — `PolymarketWSClient` subscribes to token IDs, receiving `book` snapshots and `price_change` deltas. These are applied to `LocalOrderBook` instances atomically.
4. **Scanning** — `ArbitrageScanner` evaluates all orderbooks on every WebSocket message using per-token fee rates, emitting `ArbOpportunity` objects when profitability exceeds `min_edge`.
5. **Sizing** — Order size is computed via fractional Kelly criterion: `size = min(max_depth, kelly_fraction × multiplier × capital)`.
6. **Execution** — Opportunities pass through the `RiskEngine`, then execute via `PaperExecutor` (simulated) or `LiveExecutor` (real orders signed and placed via CLOB API).
7. **Observability** — `TerminalDashboard` renders live KPIs via Rich. Verbose logs go to `logs/paper_trading.log` via `structlog` + `RotatingFileHandler`.

---

## Risk Management & Guardrails

| Guard | Behavior |
|---|---|
| **Kill Switch** | File-based (`.kill_switch`). If present, halts ALL order execution immediately. Persists across restarts. |
| **Daily Drawdown** | If `realized + unrealized PnL < −max_daily_drawdown`, the kill switch is auto-activated. |
| **Per-Asset Exposure** | Rejects orders that would push a single token's notional above `max_exposure_per_asset`. |
| **Portfolio Exposure** | Rejects orders that would push total portfolio notional above `max_portfolio_exposure`. |
| **Stale Feed Breaker** | Rejects orders if the underlying orderbook hasn't received an update within `stale_feed_threshold_ms`. |
| **Dedup Window** | Prevents re-execution of the same opportunity ID within `opportunity_dedup_window_s`. |
| **Order TTL** | Cancels inflight orders exceeding `order_timeout_s`. |

---

## Configuration System

Settings are loaded via `pydantic-settings` with the following **priority order** (highest wins):

1. **Environment variables** (`.env` file or shell)
2. **TOML config** (`config/default.toml`)
3. **Code defaults** (`bot/settings.py`)

All parameters live in `bot/settings.py` and are grouped into sub-models. TOML sections map 1:1 to setting groups (e.g., `[trading]` → `TradingSettings`).

---

## Parameter Reference

Each parameter is documented with its **default value**, **TOML-tuned value** (from `config/default.toml`), **which mode it applies to**, and **what happens when you change it**.

Legend:
- 🟢 **Paper** = affects paper trading mode
- 🔴 **Live** = affects live trading mode
- 🟡 **Both** = affects both modes

---

### 1. Top-Level Settings

Set via **environment variables** (`.env` file) or shell. These are NOT in the TOML.

| Parameter | Env Var | Default | Mode | Description |
|---|---|---|---|---|
| `environment` | `ENVIRONMENT` | `"local"` | 🟡 Both | Runtime environment label. Informational only. |
| `polymarket_api_key` | `POLYMARKET_API_KEY` | `""` | 🔴 Live | API key for Polymarket CLOB authentication. **Required for live trading.** |
| `polymarket_api_secret` | `POLYMARKET_API_SECRET` | `""` | 🔴 Live | API secret for CLOB authentication. |
| `polymarket_api_passphrase` | `POLYMARKET_API_PASSPHRASE` | `""` | 🔴 Live | API passphrase for CLOB authentication. |
| `database_url` | `DATABASE_URL` | `"sqlite+aiosqlite:///:memory:"` | 🟡 Both | Async SQLAlchemy connection string. Default uses in-memory SQLite (data lost on restart). |
| `redis_url` | `REDIS_URL` | `"redis://localhost:6379/0"` | 🟡 Both | Redis connection string for caching. |
| `starting_capital` | `STARTING_CAPITAL` | `1000.0` | 🟡 Both | Initial capital in USD. **In live mode, this is automatically overridden by your real Polymarket account balance** fetched from `GET /balance-allowance`. In paper mode, use the `--capital` CLI flag or `.env` to set it. |

---

### 2. Trading Parameters (`TradingSettings`)

**TOML section:** `[trading]`  
**Mode:** 🟡 Both

| Parameter | Code Default | TOML Value | Description |
|---|---|---|---|
| `polymarket_fee` | `0.02` | `0.03` | Fallback fee rate used when the `/fee-rate` API call fails. In normal operation, the bot dynamically fetches per-token fee rates from Polymarket. This value is only used as a safety net. |
| `slippage_est` | `0.005` (0.5%) | `0.005` (0.5%) | Additive slippage budget per leg in the edge calculation. Added to ask price (buys) and subtracted from bid price (sells) when computing theoretical edge. |
| `min_edge` | `0.01` (1%) | `0.015` (1.5%) | Minimum edge threshold for an opportunity to be tradeable. |
| `min_notional` | `10.0` | `1.0` | Minimum order size in USD. Polymarket's own minimum is $1.00. |
| `kelly_fraction_multiplier` | `0.25` | `0.35` | Fraction of the full Kelly bet to use. `1.0` = full Kelly (aggressive), `0.25` = quarter Kelly (conservative). |

#### Tuning Effects

**`polymarket_fee`** — This is now a **fallback only**. The bot fetches real per-token fee rates dynamically from Polymarket's API (`GET /fee-rate?token_id=...`). If the API is unreachable, this value is used. Polymarket's current `feeRate` parameter is `0.03`, which produces a peak effective fee of ~1.80% at the 50/50 price point.

**`slippage_est`** — Acts as a safety buffer in the detector. **Increasing it** reduces detected opportunities but protects against adverse fills. **Decreasing it** increases detected opportunities but risks entering trades where execution slippage erodes the edge. In paper mode, the actual fill uses VWAP from L2 depth (slippage is already captured in the book walk), so this is purely a gate.

**`min_edge`** — The profitability floor. **Increasing it** (e.g., to `0.03`) means only wide dislocations are traded. **Decreasing it** (e.g., to `0.005`) captures more opportunities but many may be net-negative after fees.

**`min_notional`** — **Increasing it** filters out small opportunities. **Decreasing it** (e.g., to `0.5`) allows micro-sized trades. Polymarket requires a minimum of $1.00 per order.

**`kelly_fraction_multiplier`** — At `0.25`, the bot risks ~25% of what full Kelly suggests. At `0.35`, moderately conservative. **Increasing toward `1.0`** dramatically increases position sizes and drawdown risk. Never exceed `1.0`.

---

### 3. Network Parameters (`NetworkSettings`)

**TOML section:** `[network]`  
**Mode:** 🟡 Both

| Parameter | Code Default | TOML Value | Description |
|---|---|---|---|
| `websocket_reconnect_min_backoff` | `1.0` | `1.0` | Minimum seconds before reconnecting after a WebSocket disconnect. |
| `websocket_reconnect_max_backoff` | `60.0` | `60.0` | Maximum backoff ceiling in seconds. |
| `stale_feed_threshold_ms` | `5000` | `60000` | Milliseconds after which an orderbook with no updates is marked "stale". |
| `stale_silence_window_s` | `30.0` | `30.0` | Seconds of total WebSocket silence before triggering a reconnect. |
| `exchange_address` | `"0x4bFb41d5B..."` | *(not in TOML)* | 🔴 Live only. Polymarket exchange contract address. |
| `chain_id` | `137` | *(not in TOML)* | 🔴 Live only. Polygon chain ID. |

#### Tuning Effects

**`stale_feed_threshold_ms`** — The code default of `5000ms` is aggressive. The TOML overrides to `60000ms` (60s) because many Polymarket markets have low activity. **Decreasing it** makes the bot more paranoid about data freshness — may cause excessive rejections in quiet markets. **Increasing it** allows trading on older data.

---

### 4. Execution Parameters (`ExecutionSettings`)

**TOML section:** `[execution]`  
**Mode:** 🟡 Both

| Parameter | Code Default | TOML Value | Description |
|---|---|---|---|
| `order_timeout_s` | `30.0` | `30.0` | Maximum time an inflight order can remain pending before force-cancel. |
| `opportunity_dedup_window_s` | `60.0` | `60.0` | Time window during which the same opportunity ID is rejected as duplicate. |

---

### 5. Paper Trading Parameters (`PaperTradingSettings`)

**TOML section:** `[paper_trading]`  
**Mode:** 🟢 Paper only

| Parameter | Code Default | TOML Value | Description |
|---|---|---|---|
| `mean_latency_ms` | `120.0` | `120.0` | Mean of the Gaussian latency distribution injected before each simulated fill. |
| `std_latency_ms` | `30.0` | `30.0` | Standard deviation of the latency distribution. |

#### Tuning Effects

**`mean_latency_ms`** — **Increasing it** (e.g., to `300ms`) models a slower connection. **Decreasing it** (e.g., to `50ms`) simulates a fast connection — more fills succeed but may be unrealistically optimistic.

**`std_latency_ms`** — Controls latency variance. The actual delay is `max(1ms, gauss(mean, std))`.

---

### 6. Risk Parameters (`RiskSettings`)

**TOML section:** `[risk]`  
**Mode:** 🟡 Both

| Parameter | Code Default | TOML Value | Description |
|---|---|---|---|
| `max_daily_drawdown` | `50.0` | `10.0` | Maximum allowable drawdown in USD before kill switch auto-activates. |
| `max_exposure_per_asset` | `200.0` | `10.0` | Maximum notional exposure on any single token. |
| `max_portfolio_exposure` | `500.0` | `25.0` | Maximum total portfolio notional across all positions. |
| `kill_switch_file` | `".kill_switch"` | *(not in TOML)* | Path to the kill switch file. |

#### Tuning Effects (current TOML is tuned for $30 paper trading)

**`max_daily_drawdown = 10.0`** — ~33% of $30 capital. Tight enough to protect but allows normal variance. When triggered, requires manual clearing.

**`max_exposure_per_asset = 10.0`** — Max $10 on any single token (~33% of capital). Prevents concentration risk.

**`max_portfolio_exposure = 25.0`** — Keeps $5 in reserve at all times for new opportunities.

---

### 7. API Parameters (`ApiSettings`)

**Mode:** 🔴 Live only

| Parameter | Description |
|---|---|
| `private_key` | EVM private key (`SecretStr`) for EIP-712 order signing. |
| `host_address` | Your Ethereum address (maker/signer) for the CLOB API. |

> **⚠️ Security:** Set via environment variables, never commit to source control.

---

### 8. Monitoring Parameters (`MonitoringSettings`)

**TOML section:** `[monitoring]`  
**Mode:** 🟡 Both

| Parameter | Code Default | TOML Value | Description |
|---|---|---|---|
| `health_port` | `8080` | `8080` | TCP port for the HTTP health/metrics server. |

#### Endpoints

- **`GET /health`** — Returns JSON: `{ status, uptime_s, ws_connected, books_active, books_stale, kill_switch, last_fill_ts }`.
- **`GET /metrics`** — Returns Prometheus text format: `polybot_fills_total`, `polybot_pnl_net`, `polybot_volume_usd`, etc.

---

## CLI Arguments

### Paper Trading

```bash
python -m bot.cli.papertrade [OPTIONS]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--capital` | `float` | `1000.0` | Starting capital for the paper session. Overrides `STARTING_CAPITAL`. |
| `--reset` | `bool` | `false` | Reset the paper trading database before starting. |

### Live Trading

```bash
python -m bot.cli.live
```

No CLI flags. Live mode automatically:
1. Fetches your real account balance from Polymarket's `/balance-allowance` endpoint
2. Fetches per-token fee rates from `/fee-rate` during market discovery
3. Loads all other configuration from `.env` and `config/default.toml`

---

## Quickstart

### Prerequisites

- Python ≥ 3.12
- `make` (optional, for convenience targets)

### Setup

```bash
make setup
cp .env.example .env
# Edit .env with your credentials and database connection strings
```

### Running Paper Trading

```bash
make papertrade
# Or with custom capital:
python -m bot.cli.papertrade --capital 30
```

### Running Live Trading

```bash
python -m bot.cli.live
```

> **⚠️ WARNING:** Live mode places real orders with real money. Ensure API credentials are configured, risk parameters are reviewed, and you have tested thoroughly in paper mode first.

---

## Testing & Linting

```bash
make test      # pytest with async support
make lint      # ruff check + ruff format + mypy
```

---

## Summary of TOML vs Code Defaults

The `config/default.toml` is currently tuned for **$30 paper trading**:

| Parameter | Code Default | TOML Override | Rationale |
|---|---|---|---|
| `polymarket_fee` | `0.02` | `0.03` | Matches Polymarket's current `feeRate` parameter (fallback only) |
| `min_edge` | `0.01` | `0.015` | Slightly more conservative to account for execution friction |
| `min_notional` | `10.0` | `1.0` | Matches Polymarket's minimum order size |
| `kelly_fraction_multiplier` | `0.25` | `0.35` | Moderately conservative sizing |
| `stale_feed_threshold_ms` | `5000` | `60000` | Prevents excessive stale rejections in low-activity markets |
| `max_daily_drawdown` | `50.0` | `10.0` | ~33% of $30 capital |
| `max_exposure_per_asset` | `200.0` | `10.0` | Max $10 per token |
| `max_portfolio_exposure` | `500.0` | `25.0` | Keeps $5 in reserve |
