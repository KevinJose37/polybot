# Terminal & Lifecycle Domain Audit

### 1. Domain Summary
The lifecycle module handles market discovery, WebSocket subscription, and settlement of resolved markets. The terminal provides real-time observability. While the terminal rendering is highly responsive and informative, the lifecycle settlement logic relies on arbitrary assumptions that corrupt historical PnL reporting in edge cases.

### 2. Scope & Critical Paths
- `bot/execution/lifecycle.py` (LifecycleManager)
- `bot/dashboard/terminal.py` (TerminalDashboard)

### 3. Component Assessment

- **Component**: `bot/execution/lifecycle.py` / `discovery_loop`
- **Purpose**: Detects when markets are removed from active API discovery, determining them as "resolved", and settles local positions.
- **Status**: `Immediate fix required`
- **Findings & Evidence**: When a parity pair resolves, the bot deterministically assigns a payout of `1.0` to the alphabetically first token and `0.0` to the second. For perfectly matched positions (e.g., 100 YES, 100 NO), the total PnL is correct. However, if the bot holds an *unhedged* or *imbalanced* position (e.g., 50 YES, 0 NO), assigning a payout based on alphabetical order instead of the actual Polymarket resolution oracle leads to a completely arbitrary PnL assignment.
- **Risk Assessment**: If an unhedged leg is settled at `1.0` locally but lost in reality, the bot's reported paper equity will permanently diverge from its actual on-chain or platform equity.
- **Remediation**: Query the Polymarket REST API to fetch the actual resolution prices (e.g., via the `/markets/{id}` endpoint) before settling the `PositionManager`. Do not rely on alphabetical heuristics.

- **Component**: `bot/dashboard/terminal.py` / `update`
- **Purpose**: Renders the rich UI dashboard.
- **Status**: `Working well`
- **Findings & Evidence**: Effectively utilizes the `PositionManager` state to render realtime stats. The `warmup_until_ms` logic correctly visually shields the user from startup volatility.
- **Risk Assessment**: The dashboard relies on `ws_client._running` for health checks, which accurately tracks connection state.
- **Remediation**: None required directly here (dependent on fixes in `PositionManager`).

### 4. Integration Issues
- **Lifecycle ↔ API**: The system attempts to infer market resolution purely from absence in the active markets list (`ABSENCE_THRESHOLD = 2`). This is a brittle heuristic. Network blips or pagination changes on the Polymarket API could trigger false positive settlements, artificially dumping active positions.

### 5. Priority-Ranked Remediation Plan
- **P0**: Implement an oracle-based settlement check in `lifecycle.py` (fetch actual settlement prices from the API).
- **P1**: Improve the "resolved" heuristic to look for explicit `closed=true` or `active=false` flags rather than mere absence from discovery pagination.
