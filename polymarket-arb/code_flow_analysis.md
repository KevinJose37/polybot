# Polymarket-Arb Code Flow Analysis & Health Report

This document rigorously analyzes the code flow and function interactions across the `polymarket-arb` bot, specifically focusing on the execution lifecycle mapped through `papertrade.py`, `live.py`, and the dashboard in `terminal.py`.

It evaluates the execution pipeline, strategies, risk logic, and provides a function-by-function assessment for the LLM maintenance team.

## Architecture & Code Flow Lifecycle

1. **Initialization (`run_live_trading` / `run_paper_trading`)**:
   - Both CLI entry points instantiate all core singletons: `PolymarketRESTClient`, `PolymarketWSClient`, `MarketDiscoveryService`, `PositionManager`, `FillManager`, `RiskEngine`, `TradingStats`, and `ForensicLogger`.
   - The topology is built by discovering valid markets (`MarketDiscoveryService`). 

2. **State Hydration**:
   - Fee rates are fetched asynchronously via REST for all tokens.
   - Orderbooks (`LocalOrderBook`) are initialized via REST API snapshots.

3. **Real-time Pipeline (WebSocket Loop)**:
   - The websocket feed (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) delivers `book` (snapshots) and `price_change` (deltas).
   - `ws_callback` applies these events to the internal `LocalOrderBook` instances.

4. **Arbitrage Scanning (`ArbitrageScanner.scan`)**:
   - Synchronously invoked immediately after any WebSocket update.
   - Iterates pre-built target topology sets (`parity_markets`, `monotonicity_pairs`).
   - Identifies Type B (Monotonicity) and Type C (Exhaustive Sets). 
   - Note: Type A was correctly subsumed by Type C to prevent double-execution inefficiencies.

5. **Execution Orchestration (`ExecutorProtocol`)**:
   - `execute_opportunity` is fired as a background asyncio task.
   - **Atomic Risk Check**: Calculates global notional exposure across all legs and atomically reserves capacity in `RiskEngine`.
   - **Leg Placement**: Validates each leg against portfolio and per-asset exposure limits (`validate_order`), then fires `place_order`.
   - Fills update the `PositionManager`, and forensic telemetry is logged.

6. **Market Resolution & Maintenance (`market_discovery_loop`)**:
   - Polls every 60s for new markets based on interval logic.
   - Settles resolved markets by interpreting `0.0` or `1.0` parity states from the last known best bid.

7. **Observability (`TerminalDashboard.update`)**:
   - Renders a rich terminal UI reflecting current equity, position costs, and active opportunities.
   - Safely incorporates the parity-aware valuation model from `PositionManager` to prevent massive PnL swings before market settlement.

---

## Subsystem Health & Function Assessment

### 🟢 Functions Working Well (Solid Design)

*These functions are structurally sound, well-tested, and execute their designated intent safely.*

- **`ArbitrageScanner.scan`**: Very efficient. Short-circuits effectively on stale books or missing depth. Heartbeat logging prevents massive log spam.
- **`PositionManager.add_fill`**: Elegantly handles crossing zero (flipping positions from LONG to SHORT) and separates realized PnL correctly.
- **`PositionManager.update_all_mtm`**: Exceptional logic for valuing parity pairs (guaranteeing $1.00 on matched inventory) while gracefully degrading to mid-price for unhedged legs.
- **`PaperExecutor.execute_opportunity` & `LiveExecutor.execute_opportunity`**: The atomic `reserve_exposure` implementation perfectly mitigates concurrent multi-leg over-allocation.
- **`RiskEngine.validate_order`**: Extremely robust. Covers stale feeds, hard kill-switch triggers, max drawdown limits, and granular per-asset limits. Rate-limiting the logging (`_should_warn`) is a great touch.
- **`MarketDiscoveryService.discover_markets`**: Safely tracks `_known_ids` to suppress duplicate discovery spam while successfully filtering for strictly valid windows.

### 🟡 Functions That Can Be Improved (Technical Debt / Optimizations)

*These functions work, but exhibit technical debt, nested complexity, or suboptimal performance.*

- **`ws_callback` (in `live.py` and `papertrade.py`)**:
  - *Critique*: Currently implemented as a deeply nested closure inside the main run loops. It handles orderbook synchronization, scanner invocation, and database persistence directly.
  - *Improvement*: Extract into a dedicated `MarketEventHandler` class. Decouple the database persistence task to an asynchronous queue to prevent any potential blocking of the WebSocket ingestion thread.
- **`market_discovery_loop` (in `live.py` and `papertrade.py`)**:
  - *Critique*: Like `ws_callback`, this is a massive closure. The logic to infer resolution outcomes (`settle_price = 1.0 if last_bid > 0.5 else 0.0`) is fragile if the book gets cleared before resolution.
  - *Improvement*: Extract to a `LifecycleManager`. Fetch explicit settlement values from Polymarket's REST API or Gamma events instead of inferring from final orderbook state.
- **`TerminalDashboard.update`**:
  - *Critique*: Computes some derived metrics on the fly (e.g., active position costs, parity PnL logic repeated slightly differently than PositionManager).
  - *Improvement*: Centralize all PnL and equity derivations strictly within `PositionManager` and `TradingStats`, leaving the dashboard to be purely presentation logic.
- **`PolymarketWSClient.connect_and_run`**:
  - *Critique*: Uses `json.loads(message)` directly in the async loop. For very high volume feeds, this synchronous CPU-bound parsing can create event loop lag.
  - *Improvement*: Consider offloading parsing using `orjson` or moving heavy parsing to a process pool if feed frequency increases.

### 🔴 Functions Requiring Immediate Fixes (Critical Risks)

*These functions contain logical flaws, dangerous assumptions, or unhandled exceptions that could crash the bot or cause significant financial loss in a live environment.*

- **`LiveExecutor.place_order` (in `live_engine.py`)**:
  - *Critique*: After calling the REST API, it assigns status using `status = response.get("status", "FILLED")`. If the Polymarket API returns an error response like `{"error": "Internal Server Error"}` or `{"message": "Insufficient balance"}`, the `.get("status")` fails and defaults to `"FILLED"`. This will trick the `PositionManager` into logging a massive ghost position and generating false PnL, completely corrupting risk limits.
  - *Fix*: Default to `"REJECTED"` if `"status"` is missing. Explicitly validate standard success indicators before registering a fill. Check for `error` or `message` keys in the response object.
  - *Urgency*: **CRITICAL** before real capital deployment.

- **`ws_callback` Delta Application (in `live.py` / `papertrade.py`)**:
  - *Critique*: The code blindly applies `await book.apply_delta(bids, asks, sequence=ts)`. If the delta application raises a `ValueError` (e.g., crossing books due to a desync), the exception will bubble up, crashing the entire `run_live_trading` coroutine.
  - *Fix*: Wrap `book.apply_delta` in a `try...except` block. On exception, log the error, flag the book as stale (`book.state = BookState.STALE`), and force a REST snapshot re-sync.
  - *Urgency*: **HIGH**. Without this, random packet loss or out-of-order delivery will instantly crash the bot.

- **`LiveExecutor.execute_opportunity` Leg Imbalance Mitigation**:
  - *Critique*: If `ack.status != "FILLED"` for the second leg, the bot correctly records a "leg imbalance" warning, but it makes no attempt to cancel or hedge the first filled leg.
  - *Fix*: Implement an explicit fallback. If Leg B fails, the bot should immediately attempt to issue a market-taking order to flatten Leg A, or fire an emergency alert to a webhook. Unhedged directional risk breaks the parity strategy.
  - *Urgency*: **HIGH**. Directional exposure on failed execution legs is the primary way arbitrage bots lose capital.

- **`MarketDiscoveryService.discover_markets` Window Boundaries**:
  - *Critique*: The time offset calculation `window_ts = (now_ts + offset) - ((now_ts + offset) % divisor)` assumes exact timing. If clock drift occurs or the API slightly delays publishing the market slug, the bot won't find it.
  - *Fix*: Broaden the search by polling using a prefix rather than computing an exact `window_ts` string, and filter the returned slugs post-hoc using the `parse_market_slug` regex to identify the closest upcoming valid windows.
  - *Urgency*: **MEDIUM**. Can result in missed trades.

---

This document should be utilized by LLM agents as a state-of-the-union for the `polymarket-arb` codebase. Prioritize addressing the items in the **🔴 Immediate Fixes** section before attempting feature additions or strategy enhancements.
