# Execution Domain Audit

### 1. Domain Summary
The execution domain (`LiveExecutor`, `PaperExecutor`, `ArbitrageScanner`) is responsible for discovering, validating, and executing arbitrage opportunities against the Polymarket orderbook. While the core concurrency and signing mechanics function correctly, the architecture relies on deeply flawed assumptions regarding inventory constraints and leg unwinding, presenting an extreme financial risk for live trading.

### 2. Scope & Critical Paths
- `bot/execution/live_engine.py` (LiveExecutor)
- `bot/execution/paper_engine.py` (PaperExecutor)
- `bot/arbitrage/scanner.py` (ArbitrageScanner)
- `bot/arbitrage/monotonicity.py`
- `bot/arbitrage/exhaustive_sets.py`

### 3. Component Assessment

- **Component**: `bot/arbitrage/scanner.py` & `bot/arbitrage/monotonicity.py`
- **Purpose**: Detects cross-timeframe arbitrage edges (Type B) and generates execute orders.
- **Status**: `Immediate fix required`
- **Findings & Evidence**: The logic blindly detects edges that require short selling (e.g., selling the 5m token). However, Polymarket does not support naked short selling. The scanner generates `SELL` legs without querying the `PositionManager` to confirm if sufficient inventory exists. 
- **Risk Assessment**: `LiveExecutor` will attempt to place naked `SELL` orders, resulting in immediate API rejections (`Insufficient balance`). If the accompanying `BUY` leg succeeds, the bot will incur severe unhedged directional exposure.
- **Remediation**: Inject `PositionManager` into the detectors. Suppress `SELL` parity and monotonicity signals unless the exact required token inventory is verified in the local state.

- **Component**: `bot/execution/live_engine.py` / `execute_opportunity`
- **Purpose**: Atomically executes all legs of an arbitrage opportunity.
- **Status**: `Immediate fix required`
- **Findings & Evidence**: If a leg imbalance occurs (one leg fills, another fails or is rejected), the system logs a `leg_imbalance` warning and activates the `RiskEngine` kill switch. It explicitly *does not* attempt to cancel or close out the already filled leg.
- **Risk Assessment**: Activating a kill switch halts the bot but leaves the portfolio holding unhedged directional risk in a highly volatile market. This is a severe financial hazard.
- **Remediation**: Implement mandatory hedge unwinding. If a leg fails, immediately issue a market or aggressive limit order to dump the filled leg's inventory.

- **Component**: `bot/utils/math.py` / `fee_per_share`
- **Purpose**: Calculates Polymarket taker fees.
- **Status**: `Working well`
- **Findings & Evidence**: Correctly implements the Polymarket fee curve and accurately returns `0.0` for `SELL` orders, respecting the platform's non-taker fee status for sells.
- **Risk Assessment**: None.
- **Remediation**: None required.

### 4. Integration Issues
- **Scanner ↔ Executor ↔ Risk**: The scanner produces opportunities that the RiskEngine validates based solely on notional exposure limits, failing to validate inventory constraints. The executor blindly fires them. The entire pipeline lacks an "inventory-aware" validation step.

### 5. Priority-Ranked Remediation Plan
- **P0**: Implement unhedged leg unwinding in `execute_opportunity`.
- **P0**: Add inventory requirement checks for `SELL` legs inside `scanner.py` / `monotonicity.py` / `exhaustive_sets.py`.
- **P1**: Add an inventory pre-check to `RiskEngine.validate_order`.
