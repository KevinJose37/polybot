# Audit 03 — Strategy & Detection Logic (COMPLETED)

> [!NOTE]
> All strategy logic and detection math issues identified in this audit have been fully addressed and tested.

## Function-by-Function Assessment

### `bot/utils/math.py`

---

#### `polymarket_taker_fee(price, size, fee_rate, side)`
| Attribute | Value |
|---|---|
| **File** | `bot/utils/math.py:20` |
| **Purpose** | Calculate total taker fee for a trade using Polymarket's documented formula |
| **Status** | ✅ Working Well |
| **Why** | Formula `C × p × feeRate × (p × (1-p))` matches Polymarket docs. SELL returns 0. Boundary guards for price ≤ 0 or ≥ 1.0. Minimum fee floor at 0.0001. |
| **Evidence** | L28-35: `if side == "SELL": return 0.0` is correct per platform rules. L32: `raw_fee = size * price * fee_rate * (price * (1.0 - price))` matches documented formula. |
| **Risk** | Low. Formula is correct. |
| **Recommended Action** | None |

---

#### `fee_per_share(price, fee_rate, side)`
| Attribute | Value |
|---|---|
| **File** | `bot/utils/math.py:38` |
| **Purpose** | Per-share fee component for edge calculations in detectors |
| **Status** | ✅ Working Well |
| **Why** | Correctly derives the per-unit fee by dividing out `size` from the full formula. Used consistently across all three detectors. |
| **Evidence** | L48: `return price * fee_rate * (price * (1.0 - price))` — mathematically equivalent to `polymarket_taker_fee / size`. |
| **Risk** | Low |
| **Recommended Action** | None |

---

#### `calculate_kelly_fraction(p, b)`
| Attribute | Value |
|---|---|
| **File** | `bot/utils/math.py:61` |
| **Purpose** | Compute raw Kelly fraction: `f = (pb - q) / b` |
| **Status** | ✅ Working Well |
| **Why** | Standard Kelly formula. Boundary handling is correct: `p <= 0` → 0, `p >= 1` → 1 (certainty), `b <= 0` → 0. |
| **Evidence** | L71-73: `q = 1.0 - p; f = (p * b - q) / b; return max(0.0, f)` |
| **Risk** | Low |
| **Recommended Action** | None |

---

#### `calculate_fractional_kelly(p, b, multiplier)`
| Attribute | Value |
|---|---|
| **File** | `bot/utils/math.py:76` |
| **Purpose** | Apply fractional Kelly multiplier (default 0.25) to Kelly fraction |
| **Status** | ✅ Working Well |
| **Why** | Simple multiplication delegation. |
| **Risk** | Low |
| **Recommended Action** | None |

---

#### `calculate_order_size(p, b, capital, max_size, multiplier)`
| Attribute | Value |
|---|---|
| **File** | `bot/utils/math.py:84` |
| **Purpose** | Compute dollar-denominated order size capped by orderbook depth |
| **Status** | ⚠️ Needs Improvement |
| **Why** | The `max_size` parameter receives **volume in shares** from the orderbook (e.g., 50 shares), while `fractional_kelly * capital` produces **dollars** (e.g., $25). The `min()` compares apples to oranges when price ≠ 1.0. For a 0.45 priced token, 50 shares = $22.50 notional, but `max_size=50` would allow $50 in the min comparison. |
| **Evidence** | In `exhaustive_sets.py:73`: `max_size = min(up_ask_vol, down_ask_vol)` — this is share volume. L96: `return min(max_size, fractional_kelly * capital)` — mixed units. |
| **Risk** | Medium. In practice, the Kelly sizing is conservative enough (0.25-0.35 multiplier) that this rarely causes oversizing. But it **could** produce an order that exceeds available liquidity when `capital` is large and `max_size` (shares) happens to be > Kelly result (dollars). The VWAP fill simulation in paper trading would partially fill, masking the issue. In live trading, the exchange would reject or partially fill. |
| **Recommended Action** | Convert `max_size` to notional (`max_size * price`) before comparing, or convert `fractional_kelly * capital` to shares. |

---

#### `net_cost_buy(price, size, fee_rate)` / `net_revenue_sell(price, size, fee_rate)`
| Attribute | Value |
|---|---|
| **File** | `bot/utils/math.py:51-58` |
| **Purpose** | Convenience functions for total cost/revenue |
| **Status** | ⚠️ Needs Improvement — Dead Code |
| **Why** | Neither function is imported or used anywhere in the codebase. |
| **Risk** | None (dead code) |
| **Recommended Action** | Remove or integrate into fee calculations for consistency. |

---

### `bot/arbitrage/opportunity.py`

#### `ArbType`, `ArbLeg`, `ArbOpportunity`
| Attribute | Value |
|---|---|
| **File** | `bot/arbitrage/opportunity.py` |
| **Purpose** | Data models for arbitrage opportunities |
| **Status** | ✅ Working Well |
| **Why** | Clean dataclass design. ArbType enum correctly maps to strategy names. ArbLeg captures all execution parameters. |
| **Risk** | Low |
| **Recommended Action** | None |

---

### `bot/arbitrage/exhaustive_sets.py`

#### `detect_exhaustive_parity(...)`
| Attribute | Value |
|---|---|
| **File** | `bot/arbitrage/exhaustive_sets.py:20` |
| **Purpose** | Detect both BUY and SELL parity dislocations for a YES/NO binary market |
| **Status** | ✅ Working Well |
| **Why** | Correctly computes both BUY edge (`1.0 - sum_of_costs`) and SELL edge (`sum_of_revenues - 1.0`). Picks the better edge. Fee model is correct: BUY pays taker fee, SELL is fee-free. Slippage is additive on both sides. Uses `min()` of volumes for matched sizing. |
| **Evidence** | L50-64: Both BUY and SELL edge calculations are mathematically sound. L66-67: `is_buy = buy_edge > sell_edge; edge = max(buy_edge, sell_edge)` — correct selection. L73: `max_size = min(up_ask_vol, down_ask_vol)` for BUY, L77: `min(up_bid_vol, down_bid_vol)` for SELL — correct pairing. |
| **Risk** | Low. The `p=1.0` assumption for Kelly sizing is correct for parity arb (guaranteed $1.00 payout). |
| **Recommended Action** | None |

---

#### Interaction with Scanner
| Attribute | Value |
|---|---|
| **File** | `bot/arbitrage/scanner.py:95-114` |
| **Status** | ✅ Working Well |
| **Why** | Scanner correctly passes all required parameters including per-token fee rates, volume depths, and the Kelly multiplier from settings. Stale book filtering is applied before detection. |

---

### `bot/arbitrage/parity.py`

#### `detect_parity(...)`
| Attribute | Value |
|---|---|
| **File** | `bot/arbitrage/parity.py:19` |
| **Purpose** | Type-A YES/NO BUY-only parity detector |
| **Status** | ⚠️ Needs Improvement — Dead Code |
| **Why** | This detector is explicitly subsumed by `detect_exhaustive_parity()` (Type-C). The scanner does NOT call `detect_parity()`. It exists only as a historical artifact. |
| **Evidence** | Scanner L89-90: `"Type A is subsumed by Type C (exhaustive checks both BUY and SELL parity)"`. No import of `detect_parity` in scanner. |
| **Risk** | None (unused). However, keeping dead code introduces maintenance confusion. |
| **Recommended Action** | Delete `parity.py` or add a clear deprecation marker. Update `__init__.py` exports. |

---

### `bot/arbitrage/monotonicity.py`

#### `detect_monotonicity(...)`
| Attribute | Value |
|---|---|
| **File** | `bot/arbitrage/monotonicity.py:19` |
| **Purpose** | Detect cross-timeframe monotonicity violation: if 5m YES bid > 15m YES ask, arb exists |
| **Status** | ⚠️ Needs Improvement |
| **Why** | The detector is mathematically correct but has a **conceptual coverage gap**: it only checks `bid_5m > ask_15m` (sell 5m, buy 15m). It does NOT check the reverse: `bid_15m > ask_5m` (sell 15m, buy 5m). In theory, if the 15m market is overpriced relative to 5m, this is also an arb. |
| **Evidence** | L48-50: `receive = bid_5m - sell_fee - slippage; pay = ask_15m + buy_fee + slippage; edge = receive - pay`. Only one direction is checked. |
| **Risk** | Low-Medium. Missed opportunities, not incorrect trades. The current logic is sound for what it does. |
| **Recommended Action** | Consider adding reverse-direction detection. Document the unidirectional assumption. |

---

#### Kelly Sizing for Type-B
| Attribute | Value |
|---|---|
| **Status** | ⚠️ Needs Improvement |
| **Why** | Type-B uses `p=1.0` for Kelly sizing (L55). Unlike parity (guaranteed $1.00 payout), monotonicity trades have **uncertain settlement**. The 5m market and 15m market are different events — you're betting on a probability relationship, not a guaranteed payout. Using `p=1.0` (certainty) overstates confidence and over-sizes the position. |
| **Evidence** | `monotonicity.py:55`: `p = 1.0` — this is only correct for guaranteed-payout parity arb. |
| **Risk** | Medium. Oversized positions in Type-B trades that have genuine settlement risk. |
| **Recommended Action** | Use an empirically estimated `p` (e.g., historical win rate) or apply a conservative discount (e.g., `p=0.7`). |

---

### `bot/arbitrage/scanner.py`

#### `ArbitrageScanner.__init__`
| Attribute | Value |
|---|---|
| **File** | `bot/arbitrage/scanner.py:22` |
| **Status** | ✅ Working Well |
| **Why** | Takes Settings, topology, fee_rates, and position_manager. Uses available capital (not total capital) for sizing. |

---

#### `ArbitrageScanner.scan(orderbooks)`
| Attribute | Value |
|---|---|
| **File** | `bot/arbitrage/scanner.py:30` |
| **Purpose** | Run all detectors against current orderbook state |
| **Status** | ✅ Working Well |
| **Why** | Correctly iterates parity markets and monotonicity pairs. Skips stale and incomplete books. Applies per-token fee rates with fallback to default. Uses available capital (after positions) for sizing. |
| **Evidence** | L37-39: `capital = self.settings.starting_capital; if self.position_manager: capital = self.position_manager.get_available_capital(capital)`. L64-66: stale check. L71-73: None check for prices. |
| **Risk** | Low |
| **Recommended Action** | None |

---

#### Scanner Capital Source
| Attribute | Value |
|---|---|
| **Status** | ⚠️ Needs Improvement |
| **Why** | Scanner uses `settings.starting_capital` as the base, then adjusts via `position_manager.get_available_capital()`. But `get_available_capital()` uses `starting_capital + total_realized_pnl - pos_cost`. This means the scanner's `capital` parameter to Kelly sizing reflects **available cash**, not **total equity**. This is conservative but means winning strategies can't compound gains into sizing. |
| **Risk** | Low (conservative bias). |
| **Recommended Action** | Document this as an intentional design choice. Consider equity-based sizing as an option. |

---

## Integration Assessment: Scanner → Executor Pipeline

| Step | Correctness |
|---|---|
| Scanner produces `ArbOpportunity` with legs | ✅ Correct |
| EventHandler calls `scanner.scan()` after warmup | ✅ Correct |
| EventHandler iterates opportunities and calls `executor.execute_opportunity()` | ✅ Correct — serial, not fire-and-forget |
| Executor dedup check via FillManager | ✅ Correct |
| Executor atomic exposure reservation | ✅ Correct |
| Executor validates each leg with RiskEngine | ✅ Correct — pre-validation before any execution |
| Executor enforces matched sizing | ✅ Correct for Type-A/C, N/A for Type-B |

> [!NOTE]
> The scanner → executor pipeline is well-integrated. Opportunities flow correctly from detection through risk validation to execution. The serial execution in `EventHandler.handle_message()` prevents concurrent execution of the same opportunity.

---

## Categorized Findings — Strategy & Detection

### Working Well
- `polymarket_taker_fee()` — fee formula matches docs
- `fee_per_share()` — correctly derived per-unit fee
- `calculate_kelly_fraction()` — standard Kelly with correct bounds
- `detect_exhaustive_parity()` — correct BUY+SELL edge detection
- `ArbitrageScanner.scan()` — correct orchestration with stale guards
- `ArbOpportunity` / `ArbLeg` data models — clean, minimal
- Scanner → Executor integration — serial, deduped, risk-validated

### Needs Improvement
- `calculate_order_size()` — mixed units (shares vs dollars) in `min()` comparison
- `detect_parity()` — dead code, should be removed
- `detect_monotonicity()` — unidirectional only, `p=1.0` oversizes
- `net_cost_buy()` / `net_revenue_sell()` — dead code
- Scanner capital source — uses available cash, not equity

### Immediate Fix Required
- None in the detection layer itself. Issues surface in execution and position management.
