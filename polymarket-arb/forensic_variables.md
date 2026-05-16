# Forensic Analysis Variables — Polymarket Arbitrage Bot (`polymarket-arb`)

> Reference document for trade reconstruction, latency/slippage analysis, legging risk detection, and strategy optimization. Designed for LLM context parsing and logging standardization.

---

## Current vs. Recommended State

| Category | Current Variables | Recommended Additions |
|---|---|---|
| **Identification** | ✅ `opp_id`, `strategy_type`, `asset`, `timestamp` | — |
| **Legs Definition** | ✅ `token_ids`, `sides` (BUY/SELL) | 🔴 `leg_roles` (e.g., 5m_leg, 15m_leg, UP_leg, DOWN_leg) |
| **Theoretical Edge** | ✅ `theoretical_edge_pct`, `expected_profit` | 🔴 `fee_rate_used`, `slippage_est_used`, `marginal_roi` |
| **L2 Context** | ✅ bid/ask prices at detection | 🔴 `depth_at_price`, `time_since_last_update_ms`, `l2_imbalance` |
| **Execution** | ✅ `fill_price`, `filled_size` | 🔴 `requested_price`, `fill_latency_ms`, `legging_gap_ms`, `order_status` |
| **Capital & Sizing**| ✅ `notional_size`, `kelly_fraction` | 🟡 `bottleneck_leg_depth`, `portfolio_exposure_pct` |
| **Outcome / PnL** | ✅ `realized_pnl`, `status` | 🔴 `actual_vs_expected_slippage`, `unhedged_exposure_usd`, `duration_held_s` |
| **Skipped Opps** | ❌ (not systematically logged) | 🔴 `SKIPPED` complete opportunity state and rejection reason |
| **L2 Snapshots** | ❌ (not systematically logged) | 🟡 `pre_trade` and `post_trade` orderbook snapshots |

---

## Complete Recommended JSON Structure

### 1. Executed Arbitrage Opportunity (Complete Lifecycle)

This structure is designed to capture the exact state of the world when the arbitrage scanner detected the opportunity, how the execution engine handled it, and the final settlement state.

```json
{
  "opp_id": "ARB-1778903407-C-BTC",
  
  // ─── IDENTIFICATION ────────────────────────────────────────────────────────
  "strategy_type": "TYPE_C_PARITY",      // "TYPE_C_PARITY" | "TYPE_B_MONOTONICITY"
  "asset": "BTC",
  "timeframe": "5m",                     // Or "cross" for Type B
  "detection_time_ms": 1778903407797,
  
  // ─── THEORETICAL EDGE & SIZING ───────────────────────────────────────────
  "theoretical": {
    "edge_pct": 0.021,                   // 2.1% theoretical edge
    "expected_profit_usd": 1.05,
    "fee_rate_used": 0.03,               // Fetched fee rate applied
    "slippage_est_used": 0.005,          // Configured slippage buffer
    "kelly_fraction": 0.35,              // Kelly multiplier applied
    "target_notional_usd": 50.0,
    "bottleneck_leg": "DOWN_BUY"         // Which leg's depth limited the sizing
  },
  
  // ─── LEGS DEFINITION & EXECUTION ─────────────────────────────────────────
  // Note: For Type C parity, there are usually 2 legs. For Type B, 2 legs.
  "legs": [
    {
      "leg_id": "leg_1",
      "token_id": "9876543210...",
      "role": "UP_BUY",                  // Direction and Action
      
      // L2 Context at Detection
      "l2_context": {
        "top_ask": 0.450,
        "ask_depth": 300.5,
        "time_since_last_ws_update_ms": 120  // How fresh was this data?
      },
      
      // Execution Results
      "execution": {
        "requested_price": 0.455,        // top_ask + slippage_est
        "requested_size": 111.11,        // shares
        "fill_price": 0.452,
        "filled_size": 111.11,
        "fill_latency_ms": 145,          // Time from API request to fill confirmation
        "status": "FILLED",              // "FILLED" | "PARTIAL" | "FAILED"
        "fee_paid_usd": 0.37
      }
    },
    {
      "leg_id": "leg_2",
      "token_id": "1234567890...",
      "role": "DOWN_BUY",
      
      "l2_context": {
        "top_ask": 0.520,
        "ask_depth": 50.0,               // This depth constrained the whole trade
        "time_since_last_ws_update_ms": 45
      },
      
      "execution": {
        "requested_price": 0.525,
        "requested_size": 96.15,
        "fill_price": 0.525,
        "filled_size": 96.15,
        "fill_latency_ms": 180,
        "status": "FILLED",
        "fee_paid_usd": 0.45
      }
    }
  ],
  
  // ─── EXECUTION RISKS (Aggregated) ────────────────────────────────────────
  "execution_metrics": {
    "legging_gap_ms": 35,                // Delta between leg 1 and leg 2 fills
    "unhedged_exposure_usd": 0.0,        // If partial fills occurred, what's the naked risk?
    "actual_vs_theoretical_slippage": -0.003 // Negative means we got better prices than expected
  },
  
  // ─── SETTLEMENT & OUTCOME ────────────────────────────────────────────────
  "outcome": {
    "status": "SETTLED",                 // "SETTLED" | "OPEN" | "UNHEDGED_LIQUIDATED"
    "realized_pnl_usd": 1.18,            // Final PnL after all fees and payouts
    "duration_held_s": 145,              // How long till the market resolved (or we exited)
    "settlement_time_ms": 1778903552797
  }
}
```

---

### 2. Skipped Opportunity (SKIPPED)

> **CRITICAL FORENSIC TOOL:** Logging skipped opportunities allows you to measure the exact **opportunity cost** of your risk parameters and guardrails. If you don't log skips, you cannot backtest parameter tuning.

```json
{
  "opp_id": "SKIP-1778903500-B-ETH",
  "type": "SKIPPED",
  
  "strategy_type": "TYPE_B_MONOTONICITY",
  "asset": "ETH",
  
  "theoretical": {
    "edge_pct": 0.018,
    "expected_profit_usd": 2.50
  },
  
  "legs": [
    {
      "token_id": "5m_token...",
      "role": "SELL_5M",
      "l2_context": { "top_bid": 0.80, "time_since_last_ws_update_ms": 8000 } // STALE!
    },
    {
      "token_id": "15m_token...",
      "role": "BUY_15M",
      "l2_context": { "top_ask": 0.75, "time_since_last_ws_update_ms": 150 }
    }
  ],
  
  "skip_reason": {
    "primary_reason": "stale_feed_threshold_exceeded",
    "details": "Leg SELL_5M data is 8000ms old, threshold is 5000ms",
    "filters_passed": ["min_edge", "max_portfolio_exposure", "dedup_window"],
    "filters_failed": ["stale_feed"]
  },
  
  "hypothetical_outcome": {
    // If we had executed, what would have happened? (Calculated post-resolution)
    "would_have_won": true,
    "missed_pnl_usd": 2.15
  }
}
```

---

## Priority of Implementation for `polymarket-arb`

| Priority | Variable / Group | Impact on Arb Bot | Effort |
|---|---|---|---|
| 🔴 **Critical** | `SKIPPED` full records | Measure the cost of `min_edge` and `stale_feed_threshold_ms`. Is the bot missing profitable trades due to paranoid settings? | Medium |
| 🔴 **Critical** | `execution.legging_gap_ms` | Track execution synchronization. A large gap means high risk of adverse selection between leg fills. | Low |
| 🔴 **Critical** | `execution_metrics.unhedged_exposure_usd` | Identifies cases where one leg filled and the other failed/partialed, leaving naked directional risk. | Low |
| 🔴 **Critical** | `legs.l2_context.time_since_last_ws_update_ms` | Diagnose false positives caused by comparing a fresh leg against a stale leg. | Low |
| 🟡 **High** | `theoretical.fee_rate_used` | Verify that the dynamic `/fee-rate` fetching is working and not silently defaulting to high fallbacks. | Low |
| 🟡 **High** | `actual_vs_theoretical_slippage` | Are we systematically crossing the spread at worse prices than the L2 depth implied? | Medium |
| 🟢 **Nice-to-Have** | `hypothetical_outcome` on Skips | Allows offline parameter tuning (e.g., "If I lowered `min_edge` to 0.5%, I would have made $50 more"). | High |

---

## Analytical Questions this Enables for LLMs

If these variables are logged to JSON/JSONL, an LLM (or data analyst) can trivially answer:

1. **"Why did PnL drop yesterday?"**
   *LLM Query:* "Check `actual_vs_theoretical_slippage` and `legging_gap_ms`." (Answers if execution friction increased).
2. **"Is the `stale_feed_threshold_ms` too aggressive?"**
   *LLM Query:* "Filter `SKIPPED` events by `stale_feed_threshold_exceeded` and sum `hypothetical_outcome.missed_pnl_usd`."
3. **"Are we taking on unintended directional risk?"**
   *LLM Query:* "Find all trades where `unhedged_exposure_usd > 0`."
4. **"Which strategy is structurally more profitable?"**
   *LLM Query:* "Group `realized_pnl_usd` by `strategy_type` (Type B vs Type C)."
