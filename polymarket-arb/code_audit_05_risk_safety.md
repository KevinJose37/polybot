# Audit 05 — Risk & Safety (COMPLETED)

> [!NOTE]
> All risk management and safety limit issues identified in this audit have been fully addressed and tested.

## Function-by-Function Assessment

### `bot/risk/engine.py`

---

#### `RiskEngine.__init__(settings, position_manager)`
| Attribute | Value |
|---|---|
| **File** | `bot/risk/engine.py:28` |
| **Status** | ✅ Working Well |
| **Why** | Loads kill switch from disk on startup. Initializes rate-limiter state. Sets `inflight_exposure = 0.0`. |

---

#### `RiskEngine.validate_order(token_id, size, price, orderbooks, check_portfolio)`
| Attribute | Value |
|---|---|
| **File** | `bot/risk/engine.py:113` |
| **Purpose** | Multi-layer order validation: kill switch → drawdown → stale feed → per-asset → portfolio |
| **Status** | ✅ Working Well |
| **Why** | Five-layer defense-in-depth. Kill switch raises exception (not just returns False) — ensures callers cannot accidentally proceed. Daily drawdown auto-activates kill switch. Per-asset exposure uses actual price for notional. Stale feed uses orderbook age. Rate-limited warnings prevent log flooding. |
| **Evidence** | L132-133: kill switch raises `RiskKillSwitchTriggered`. L136-139: drawdown check activates kill switch AND raises. L142-146: stale feed returns False. L150-163: per-asset check. L166-176: portfolio check (conditional on `check_portfolio`). |
| **Risk** | Low |

---

#### `RiskEngine.reserve_exposure(amount)` / `release_exposure(amount)`
| Attribute | Value |
|---|---|
| **File** | `bot/risk/engine.py:42-59` |
| **Purpose** | Atomic inflight exposure reservation for multi-leg trades |
| **Status** | ✅ Working Well |
| **Why** | `reserve_exposure` checks `get_total_exposure() + inflight + amount > max_portfolio`. `release_exposure` uses `max(0.0, ...)` to prevent negative inflight. Single-threaded asyncio makes this race-condition-free. |
| **Risk** | Low |

---

#### `RiskEngine.activate_kill_switch(reason)` / `clear_kill_switch()`
| Attribute | Value |
|---|---|
| **File** | `bot/risk/engine.py:90-102` |
| **Purpose** | Persist kill switch to disk for crash recovery |
| **Status** | ✅ Working Well |
| **Why** | JSON file persistence survives process restarts. `clear_kill_switch()` requires explicit operator action. |

---

#### `RiskEngine.get_total_exposure()`
| Attribute | Value |
|---|---|
| **File** | `bot/risk/engine.py:36` |
| **Purpose** | Sum of all position notionals |
| **Status** | ⚠️ Needs Improvement |
| **Why** | Uses `abs(p.size) * (p.avg_price if p.avg_price > 0 else 0.5)` — the 0.5 fallback for zero avg_price is a rough estimate. When a position has just been opened, `avg_price` should always be set. The only way `avg_price == 0` is after settlement (when `size == 0`), in which case the position contributes 0 to exposure anyway. The fallback is therefore effectively dead code but could mask bugs. |
| **Risk** | Low |
| **Recommended Action** | Assert `avg_price > 0 when size != 0` instead of using a fallback. |

---

#### Per-Asset Exposure Calculation
| Attribute | Value |
|---|---|
| **File** | `bot/risk/engine.py:148-163` |
| **Status** | ⚠️ Needs Improvement |
| **Why** | `current_exposure = abs(pos.size) * (pos.avg_price if pos.avg_price > 0 else 0.5)` computes exposure at cost basis. For a position bought at $0.45 with current mid at $0.90, the risk is closer to $0.90 × size (market value), not $0.45 × size (cost). Using cost basis underestimates risk for appreciated positions and overestimates for depreciated ones. |
| **Evidence** | L152: uses `pos.avg_price` (cost basis), not current mid price. |
| **Risk** | Medium. Could allow overexposure to a single asset that has appreciated significantly since entry. For parity arb (which goes flat quickly), this is minor. For Type-B trades held through settlement, the risk window is longer. |
| **Recommended Action** | Pass current mid price to `validate_order()` and use `max(avg_price, mid_price)` for conservative exposure estimation. |

---

#### Kill Switch Activation on Leg Imbalance
| Attribute | Value |
|---|---|
| **File** | Triggered in `paper_trading/engine.py:137` and `live_engine.py:137` |
| **Status** | ✅ Working Well |
| **Why** | Correctly activates kill switch when a multi-leg trade has one leg filled and another rejected. This prevents further trading with unhedged directional exposure. |
| **Risk** | Low. Aggressive but correct for safety. |

---

#### Drawdown Calculation Timing
| Attribute | Value |
|---|---|
| **Status** | ⚠️ Needs Improvement |
| **Why** | Drawdown is checked in `validate_order()` using `total_realized_pnl + total_unrealized_pnl`. The unrealized PnL is updated in the main loop every 500ms via `update_all_mtm()`. If mid prices move adversely between MTM updates, the drawdown check uses stale unrealized PnL. In fast-moving markets, this could allow orders that breach the drawdown limit. |
| **Risk** | Low-Medium. The 500ms update interval is short enough that this is unlikely to cause material breaches for the position sizes involved. |
| **Recommended Action** | Consider computing fresh unrealized PnL at order validation time, or accept the 500ms staleness as an acceptable tolerance. |

---

### Kill Switch Integration Audit

| Component | Checks Kill Switch? | Correct? |
|---|---|---|
| `RiskEngine.validate_order()` | ✅ Raises exception | ✅ |
| `PaperExecutor.execute_opportunity()` | ✅ Via `validate_order()` | ✅ |
| `PaperExecutor.place_order()` | ✅ Via `validate_order()` | ✅ |
| `LiveExecutor.execute_opportunity()` | ✅ Via `validate_order()` | ✅ |
| `LiveExecutor.place_order()` | ✅ Via `validate_order()` | ✅ |
| `HealthServer._handle_health()` | ✅ Reports status | ✅ |
| `TerminalDashboard.update()` | ✅ Displays health status | ✅ |
| `order_ttl_loop()` | ❌ Does not check | ⚠️ TTL loop continues cancelling orders even after kill switch — benign |
| `discovery_loop()` | ❌ Does not check | ⚠️ Continues discovering markets — benign |

---

### Stale Feed Circuit Breaker Audit

| Component | Stale Check? | Correct? |
|---|---|---|
| `RiskEngine.validate_order()` | ✅ Per-token stale check | ✅ |
| `ArbitrageScanner.scan()` | ✅ Skips stale books | ✅ |
| `simulate_fill()` | ✅ Rejects stale books | ✅ |
| `LocalOrderBook.best_bid/ask()` | ✅ Returns None if stale | ✅ |
| `LocalOrderBook.bid/ask_depth()` | ✅ Returns [] if stale | ✅ |
| `PolymarketWSClient.check_stale()` | ✅ Raises StaleFeedError | ✅ |
| Main loop | ✅ Catches and triggers reconnect | ✅ |

> [!NOTE]
> The stale feed circuit breaker is comprehensively integrated across the entire pipeline. This is one of the strongest safety features in the codebase.

---

## Categorized Findings — Risk & Safety

### Working Well
- `validate_order()` — five-layer defense-in-depth
- `reserve_exposure()` / `release_exposure()` — atomic inflight tracking
- Kill switch disk persistence — survives restarts
- Kill switch on leg imbalance — prevents unhedged exposure
- Stale feed circuit breaker — comprehensively integrated
- Rate-limited warnings — prevents log flooding

### Needs Improvement
- `get_total_exposure()` — 0.5 fallback for avg_price is dead code but confusing
- Per-asset exposure — uses cost basis instead of market value
- Drawdown check uses potentially stale unrealized PnL (500ms staleness)
- `order_ttl_loop` and `discovery_loop` don't check kill switch (benign)

### Immediate Fix Required
- None in the risk engine itself. The risk framework is the most robust part of the codebase.
