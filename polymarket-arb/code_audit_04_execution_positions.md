# Audit 04 — Execution & Position Management (COMPLETED)

> [!NOTE]
> All execution and position management issues identified in this audit have been fully addressed and tested.

## Function-by-Function Assessment

### `bot/execution/position_manager.py`

---

#### `Position` dataclass
| Attribute | Value |
|---|---|
| **File** | `bot/execution/position_manager.py:12` |
| **Status** | ✅ Working Well |
| **Why** | Clean, minimal dataclass. Tracks size, avg_price, realized_pnl per market. |

---

#### `PositionManager.__init__`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/position_manager.py:25` |
| **Status** | ✅ Working Well |
| **Why** | Initializes position dict, PnL accumulators, resolved positions list, and parity pair mapping. |

---

#### `PositionManager.add_fill(market_id, side, price, size, fee)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/position_manager.py:58` |
| **Purpose** | Update position with a new fill, computing realized PnL on close |
| **Status** | 🔴 Immediate Fix Required |
| **Why** | **Division by zero** when a position is exactly closed and then reopened in the opposite direction in the same fill. At L101: `pos.avg_price = ((pos.avg_price * abs(pos.size)) + (price * abs(fill_qty))) / abs(new_size)` — if `new_size == 0` (exact close, no flip), this line is reached after `pos.size += fill_qty` sets `pos.size = 0`, but the branch at L92 (`abs(pos.size) < 1e-6`) only triggers **after** this computation because `pos.size` has already been updated at L90. **Wait — let me re-trace:** |
| **Evidence** | L74: enters close branch. L76: `close_qty = min(abs(pos.size), abs(fill_qty))`. If exact close: `close_qty = abs(pos.size) = abs(fill_qty)`. L89: `remaining_qty = fill_qty + close_qty` (if fill_qty < 0) = `-close_qty + close_qty = 0`. L90: `pos.size += fill_qty` → `pos.size = 0`. L92: `abs(pos.size) < 1e-6` → True → enters L93-94, sets `pos.size = 0`, `pos.avg_price = 0`. **Safe for exact close.** |
| | **But:** if `abs(fill_qty) > abs(pos.size)` (position flip): L76: `close_qty = abs(pos.size)` (smaller). L89: `remaining_qty != 0`. L90: `pos.size += fill_qty` → nonzero. L92: `abs(pos.size) >= 1e-6` → False → enters L95-97: `pos.avg_price = price`. **This works correctly for flips.** |
| | **Re-assessment: the division-by-zero at L101 is in the `else` branch (increasing position), which is mutually exclusive with the close branch.** The `else` at L98 only triggers when `pos.size` and `fill_qty` have the same sign. L101: `new_size = pos.size + fill_qty` — both same sign, so `abs(new_size) > 0`. **No division by zero in practice.** |
| **Revised Status** | ⚠️ Needs Improvement |
| **Revised Why** | The code is functionally correct but the control flow is confusing and fragile. The `remaining_qty` variable at L89 is computed but only used implicitly (via `pos.size` having already been updated). If future edits change the order of operations, a division-by-zero could be introduced. |
| **Risk** | Low (current code is correct), Medium (fragile to refactoring) |
| **Recommended Action** | Refactor into explicit close-only / flip / increase branches with clear comments. Add an assertion `assert abs(new_size) > 0` before L101. |

---

#### `PositionManager.get_available_capital(starting_capital)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/position_manager.py:44` |
| **Purpose** | Calculate cash available for new trades |
| **Status** | ✅ Working Well |
| **Why** | `starting_capital + total_realized_pnl - pos_cost` correctly computes available cash. `pos_cost` uses `abs(size * avg_price)` — correct for both long and short positions. |
| **Risk** | Low |

---

#### `PositionManager.update_all_mtm(mid_prices)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/position_manager.py:171` |
| **Purpose** | Recompute total unrealized PnL with parity-aware valuation |
| **Status** | ✅ Working Well |
| **Why** | Correctly handles three cases: (1) both long → matched at $1.00 + excess at mid, (2) both short → matched liability at $1.00 + excess at mid, (3) non-parity → standard mid valuation. Uses `valued_tokens` set to prevent double-counting. |
| **Evidence** | L189-210: Long parity path. L213-236: Short parity path. L238-242: Standard path. L208-209/234-235: `valued_tokens` prevents re-processing complement. |
| **Risk** | Low |
| **Recommended Action** | None |

---

#### `PositionManager.settle_market(market_id, settle_price)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/position_manager.py:246` |
| **Purpose** | Settle a position at a given price, realize PnL, and record resolution |
| **Status** | ⚠️ Needs Improvement |
| **Why** | The `resolved_positions` list is capped at 100 entries (L278: `pop(0)`). For long-running sessions with many markets resolving, this silently drops old resolution records. The dashboard's "Last Positions Resolved" panel reads from this list — data loss is invisible. |
| **Evidence** | L278-279: `if len(self.resolved_positions) > 100: self.resolved_positions.pop(0)` |
| **Risk** | Low (display-only impact, not financial). |
| **Recommended Action** | Use a `collections.deque(maxlen=100)` for O(1) eviction, or persist to DB. |

---

#### `PositionManager.get_pair_unrealized_pnl(token_a, token_b, mid_prices)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/position_manager.py:139` |
| **Purpose** | Calculate parity-aware unrealized PnL for a specific pair |
| **Status** | ✅ Working Well |
| **Why** | Correctly values matched long+long pairs at $1.00, falls through to standard mid-price for non-parity. Used by dashboard for per-market PnL display. |

---

#### `PositionManager.get_market_unrealized_pnl(market_id, mid_prices)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/position_manager.py:117` |
| **Purpose** | Per-token unrealized PnL with parity awareness |
| **Status** | ⚠️ Needs Improvement — Incomplete |
| **Why** | The parity detection branch at L124-131 has a `pass` statement — it detects that a complement exists but does **nothing** with it. Falls through to standard mid-price valuation at L136-137. The parity-aware logic is in `get_pair_unrealized_pnl()` instead, but this function exists and could mislead callers into thinking it provides parity-aware results. |
| **Evidence** | L131: `pass` — no parity valuation implemented. |
| **Risk** | Low (function is not called in production paths — dashboard uses `get_pair_unrealized_pnl()` instead). |
| **Recommended Action** | Either implement parity valuation or remove this method and direct callers to `get_pair_unrealized_pnl()`. |

---

### `bot/execution/fill_manager.py`

---

#### `FillManager.check_and_mark(opportunity_id)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/fill_manager.py:37` |
| **Purpose** | Atomic dedup: check if already executed, mark if not |
| **Status** | ✅ Working Well |
| **Why** | Single-threaded async loop makes this safe without locks. Window-based expiry prevents unbounded memory growth. |

---

#### `FillManager.check_expired_orders(timeout_s)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/fill_manager.py:56` |
| **Purpose** | Find orders exceeding TTL for cancellation |
| **Status** | ⚠️ Needs Improvement |
| **Why** | Iterates `self.inflight_orders` dict while the `order_ttl_loop()` in the CLI removes entries. In asyncio single-threaded mode this is safe, but if the dict is mutated between `check_expired_orders()` return and the loop's `remove_inflight_order()` call, an order could be missed. More importantly, the `PaperExecutor` **never adds inflight orders** — the entire TTL mechanism is a no-op in paper trading. |
| **Evidence** | `paper_trading/engine.py` — no calls to `fill_manager.add_inflight_order()`. `live_engine.py:181` — calls `add_inflight_order()` then immediately removes at L198 after API response. Orders are only "inflight" during the `await self.api_client.place_order()` call, which is typically <1s. The 30s TTL is unlikely to ever trigger. |
| **Risk** | Low. The mechanism is not harmful, just not exercised in paper trading and likely ineffective in live trading given the synchronous request pattern. |
| **Recommended Action** | Document that inflight tracking is only meaningful for async order submission (not the current synchronous pattern). |

---

### `bot/paper_trading/engine.py`

---

#### `PaperExecutor.execute_opportunity(opportunity)`
| Attribute | Value |
|---|---|
| **File** | `bot/paper_trading/engine.py:47` |
| **Purpose** | Execute a full arbitrage opportunity in paper mode |
| **Status** | ✅ Working Well |
| **Why** | Follows the same structure as LiveExecutor: dedup → atomic reservation → per-leg risk validation → matched sizing → fill → stats. Kill switch activates on leg imbalance. Forensic logging captures full execution context. |
| **Evidence** | L49-51: dedup. L56-59: atomic reservation. L62-71: pre-validation. L92-93: matched sizing for parity. L130-137: kill switch on imbalance. |
| **Risk** | Low |

---

#### `PaperExecutor.place_order(order, opp, check_portfolio)`
| Attribute | Value |
|---|---|
| **File** | `bot/paper_trading/engine.py:155` |
| **Purpose** | Simulate a single order fill |
| **Status** | ✅ Working Well |
| **Why** | Correct flow: risk check → latency injection → VWAP fill simulation → fee calculation → position update → stats recording. |
| **Evidence** | L166-169: latency injection. L178-180: VWAP fill. L183-191: fee + position update. |
| **Risk** | Low |

---

#### Paper Fill Simulation (`simulate_fill`)
| Attribute | Value |
|---|---|
| **File** | `bot/paper_trading/fills.py:9` |
| **Purpose** | Walk L2 book to compute depth-weighted VWAP |
| **Status** | ✅ Working Well |
| **Why** | Correctly walks ask depth for BUY, bid depth for SELL. Uses 20 levels. Computes VWAP = total_cost / filled_size. Stale book returns `(False, 0, 0)`. Slippage is intentionally NOT double-counted (already budgeted in detector). |
| **Evidence** | L47: Comment explaining no double-count of slippage. L26-28: Correct side mapping to ask/bid depth. |
| **Risk** | Low |

---

### `bot/execution/live_engine.py`

---

#### `LiveExecutor.execute_opportunity(opportunity)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/live_engine.py:48` |
| **Purpose** | Execute a full arbitrage opportunity with real orders |
| **Status** | ⚠️ Needs Improvement |
| **Why** | **Structural parity with PaperExecutor is excellent.** However, two issues: (1) `matched_size` uses `ack.filled_size` which defaults to `leg_size` if `filled_size == 0` (L111). This fallback is dangerous — a zero fill response from the API should be treated as a rejection, not a success. (2) The `finally` block always releases the full `total_notional` even if only some legs filled — this is correct for exposure accounting but means the risk engine's inflight tracking is immediately freed even when positions are open. |
| **Evidence** | L111: `actual_fill_size = ack.filled_size if ack.filled_size > 0 else leg_size` — masks zero-fill API responses as full fills. |
| **Risk** | Medium. Could record phantom fills in live mode if the API returns `status: FILLED` with `filled_size: 0`. |
| **Recommended Action** | Treat `filled_size <= 0` with `status == FILLED` as an anomaly — log a warning and treat as rejection. |

---

#### `LiveExecutor.place_order(order, opp, check_portfolio)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/live_engine.py:155` |
| **Purpose** | Sign and place a single order |
| **Status** | 🔴 Immediate Fix Required |
| **Why** | **Multiple issues:** |
| | 1. **Exception handling masks all failures as "no liquidity"** (L231-233). Network errors, signature failures, API throttling, and JSON parse errors ALL result in `stats.record_no_liquidity()`. This corrupts the "No Liquidity" counter, making it impossible to distinguish liquidity issues from infrastructure failures. |
| | 2. **API response status detection is brittle** (L197): `status = response.get("status", "FILLED")` — defaults to "FILLED" if the `status` key is missing. A malformed response (e.g., rate limit error without status field) would be treated as a successful fill, recording a phantom position. |
| | 3. **Double risk validation** — `validate_order()` is called in both `execute_opportunity()` (L74) and `place_order()` (L158). The second call is redundant when `check_portfolio=False` but still performs stale-feed and per-asset checks that were already validated. |
| **Evidence** | L197: `status = response.get("status", "FILLED")` — dangerous default. L233: `self.stats.record_no_liquidity()` in generic except. |
| **Risk** | High. Phantom fills in live trading. Corrupted statistics. |
| **Recommended Action** | (1) Default status to `"UNKNOWN"` or `"REJECTED"`, not `"FILLED"`. (2) Differentiate exception types in the catch block. (3) Remove redundant risk validation in `place_order()` when called from `execute_opportunity()`. |

---

### `bot/execution/events.py`

---

#### `MarketEventHandler.handle_message(data)`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/events.py:83` |
| **Purpose** | Parse WS messages → update orderbooks → scan → execute |
| **Status** | ✅ Working Well |
| **Why** | Correctly handles both `book` (snapshot) and `price_change` (delta) event types. Warmup gate prevents premature scanning. Scan throttle (200ms) prevents CPU thrashing. Opportunities are executed serially, preventing concurrent execution. |
| **Evidence** | L95-106: book event parsing. L108-138: price_change parsing with stale book recovery. L145-157: warmup + throttle gates. L163: serial execution loop. |
| **Risk** | Low |

---

#### `MarketEventHandler.__init__` — warmup calculation
| Attribute | Value |
|---|---|
| **File** | `bot/execution/events.py:43-52` |
| **Purpose** | Suppress scanning until next 5-minute window boundary |
| **Status** | ⚠️ Needs Improvement |
| **Why** | Uses `time.time()` directly instead of `current_timestamp_ms()`. This bypasses the pluggable clock system, making the warmup period non-deterministic in tests. |
| **Evidence** | L45-48: `import time; now_s = int(time.time()); ...` |
| **Risk** | Low (cosmetic in production, but breaks testability). |
| **Recommended Action** | Use `current_timestamp_ms() // 1000` instead of `time.time()`. |

---

#### Persistence Worker
| Attribute | Value |
|---|---|
| **File** | `bot/execution/events.py:54-81` |
| **Purpose** | Background worker to save trades to DB without blocking WS loop |
| **Status** | ⚠️ Needs Improvement |
| **Why** | Uses `async for session in self.db.get_session()` (L60). The `get_session()` is an async generator that yields one session. Using `async for` technically works but relies on the generator yielding exactly once. If the generator raised before yielding, the `for` body would never execute and the trade would be silently lost with no error logged. Also, the `break` at L73 is necessary to exit the `async for` after one iteration — fragile pattern. |
| **Risk** | Low (works in practice). |
| **Recommended Action** | Use `async with self.db.SessionLocal() as session:` directly instead of the generator pattern. |

---

### `bot/execution/lifecycle.py`

---

#### `LifecycleManager.initial_discovery()`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/lifecycle.py:51` |
| **Purpose** | Bootstrap: discover markets, init orderbooks, fetch fees |
| **Status** | ✅ Working Well |
| **Why** | Concurrent initialization of orderbooks and fee rates. Registers parity pairs. Correctly populates scanner topology. |

---

#### `LifecycleManager.discovery_loop()`
| Attribute | Value |
|---|---|
| **File** | `bot/execution/lifecycle.py:80` |
| **Purpose** | Background loop: poll for new markets, settle resolved ones |
| **Status** | ✅ Working Well |
| **Why** | Absence-counting prevents premature settlement from transient API failures (threshold=2 consecutive cycles). Empty result guard prevents replacing good topology with nothing. Deterministic parity-aware settlement (alphabetic ordering → 1.0/0.0). |
| **Evidence** | L84-85: `ABSENCE_THRESHOLD = 2`. L93-95: empty result guard. L159-170: parity-aware settlement. |
| **Risk** | Low |

---

#### Settlement Price Assignment
| Attribute | Value |
|---|---|
| **File** | `bot/execution/lifecycle.py:153-170` |
| **Status** | ⚠️ Needs Improvement |
| **Why** | When both parity legs resolve simultaneously, settlement is deterministic (alphabetical → 1.0/0.0). **But when they resolve in different discovery cycles** (one absent first), the first leg settles at 0.5 (conservative default), and the second leg may never trigger the `complement in resolved_tokens` branch because the complement was already settled and removed from `self.orderbooks`. This could cause incorrect PnL for asynchronous resolutions. |
| **Evidence** | L167-170: standalone settlement at 0.5. L177: `self.orderbooks.pop(mid, None)` — removes the token, so complement check in next cycle won't find it. |
| **Risk** | Medium. PnL accuracy depends on both legs resolving in the same 60s discovery cycle. For fast-resolving markets (5m/15m), this is usually the case. But network issues or API delays could cause asymmetric resolution. |
| **Recommended Action** | Track settled tokens separately and retroactively adjust PnL when the complement resolves. |

---

## Categorized Findings — Execution & Position Management

### Working Well
- `PositionManager.add_fill()` — correct PnL on close, handles flip
- `PositionManager.update_all_mtm()` — parity-aware with short support
- `PositionManager.get_available_capital()` — correct cash calculation
- `FillManager.check_and_mark()` — atomic dedup
- `PaperExecutor.execute_opportunity()` — full parity with live
- `PaperExecutor.place_order()` — correct simulation pipeline
- `simulate_fill()` — proper VWAP with stale guard
- `MarketEventHandler.handle_message()` — correct parse → scan → execute
- `LifecycleManager.discovery_loop()` — robust with absence counting
- `LifecycleManager.initial_discovery()` — concurrent init

### Needs Improvement
- `PositionManager.add_fill()` — fragile control flow, needs refactoring
- `PositionManager.settle_market()` — `resolved_positions` list capped at 100 with silent eviction
- `PositionManager.get_market_unrealized_pnl()` — incomplete parity path with `pass`
- `FillManager.check_expired_orders()` — no-op in paper, nearly no-op in live
- `LiveExecutor.execute_opportunity()` — `filled_size` fallback masks zero fills
- `MarketEventHandler.__init__` warmup — uses `time.time()` instead of pluggable clock
- Persistence worker — fragile `async for` + `break` pattern
- Settlement price for asynchronous leg resolution — 0.5 default may be incorrect

### Immediate Fix Required
- `LiveExecutor.place_order()` — defaults status to "FILLED" on missing key, catches all exceptions as "no_liquidity"
