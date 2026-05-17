# Risk & Valuation Domain Audit

### 1. Domain Summary
The risk and valuation domain is tasked with enforcing exposure limits, preventing drawdowns, and providing accurate mark-to-market valuations for the portfolio. The system successfully handles basic limits but fails significantly in valuing short parity positions and double-counting inflight exposures, leading to potential silent UI drift and artificial risk rejections.

### 2. Scope & Critical Paths
- `bot/risk/engine.py` (RiskEngine)
- `bot/execution/position_manager.py` (PositionManager)

### 3. Component Assessment

- **Component**: `bot/execution/position_manager.py` / `get_pair_unrealized_pnl`
- **Purpose**: Calculates the unrealized PnL for a token pair, utilizing parity pricing ($1.00 guaranteed) for matched inventory.
- **Status**: `Immediate fix required`
- **Findings & Evidence**: The logic checks `if pos_a.size > 0 and pos_b.size > 0:` to apply parity valuation. If the bot holds *short* parity pairs (e.g., `pos_a.size < 0 and pos_b.size < 0`), it skips the parity block and falls back to valuing them independently at current mid-prices. This contradicts `update_all_mtm`, which correctly handles both long and short parity logic.
- **Risk Assessment**: `TerminalDashboard` delegates to this function for its market-level UI updates. The UI will display wildly inaccurate PnL for markets where short inventory is held, decoupling the dashboard from actual equity reality.
- **Remediation**: Extend the condition in `get_pair_unrealized_pnl` to handle matched short positions exactly as `update_all_mtm` does, recognizing the $1.00 liability of the matched short pair.

- **Component**: `bot/risk/engine.py` / `validate_order`
- **Purpose**: Checks if a proposed order breaches drawdown or exposure limits.
- **Status**: `Needs improvement`
- **Findings & Evidence**: When `check_portfolio=True` (the default), the method compares `total_exposure + self.inflight_exposure + order_notional` against the limit. However, the execution flow first calls `reserve_exposure`, which adds `order_notional` to `self.inflight_exposure`. If `validate_order` is subsequently called with `check_portfolio=True`, the `order_notional` is double-counted.
- **Risk Assessment**: Currently, `LiveExecutor` passes `check_portfolio=False`, preventing the bug. However, any new integration using the default parameter will experience premature, false-positive risk rejections.
- **Remediation**: Remove `order_notional` from the `check_portfolio` conditional block inside `validate_order`, or restructure the reservation pattern to prevent overlapping state.

- **Component**: `bot/risk/engine.py` / `_should_warn`
- **Purpose**: Rate-limits repeated log warnings.
- **Status**: `Needs improvement`
- **Findings & Evidence**: The `_last_warn_ts` dictionary stores timestamps for every token indefinitely. Over weeks of live trading, this dictionary will grow as new tokens are cycled in, creating a slow memory leak.
- **Risk Assessment**: Minor degradation over long-running processes.
- **Remediation**: Implement a periodic cleanup or use an LRU cache for the rate-limiting dictionary.

### 4. Integration Issues
- **PositionManager ↔ Terminal**: The discrepancy between `get_pair_unrealized_pnl` and `update_all_mtm` means the Terminal UI's aggregate PnL will differ mathematically from the sum of the individual market PnL rows.

### 5. Priority-Ranked Remediation Plan
- **P0**: Fix short parity logic in `get_pair_unrealized_pnl`.
- **P2**: Refactor `validate_order` to prevent double-counting.
- **P3**: Add dictionary pruning to `_should_warn`.
