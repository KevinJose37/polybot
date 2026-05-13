# POLYMARKET HFT SYSTEM — STRATEGY 2: BASIC MARKET MAKING
## Internal Architecture & Execution Design Document
### Version 1.0 | Confidential Engineering Document

---

# PREAMBLE

This document is an engineering-grade implementation plan for a Market Making (MM) system targeting Polymarket BTC/ETH/SOL/XRP binary prediction markets. Market making on binary prediction markets has distinct characteristics from market making on spot or futures exchanges. This document treats those distinctions as first-class engineering concerns and does not simplify the associated risks.

---

# A. STRATEGY THESIS AND MARKET MICROSTRUCTURE

## A.1 Why Market Making May Work on Polymarket

A market maker profits from the bid-ask spread: post a bid below fair value, post an ask above fair value, collect spread when both sides fill. The core requirements are: (1) ability to estimate fair value better than or at least as well as incoming order flow, (2) ability to earn spread faster than adverse selection erodes inventory, (3) ability to manage inventory to avoid directional exposure exceeding risk tolerance.

On Polymarket specifically:

**Structural spread opportunity**: Polymarket binary markets consistently trade with spreads of 2–6 cents on a $0.50 mid-price contract. The fair spread for a liquid binary option should be much tighter (sub-cent) if the market maker's fair value estimate is accurate. This gap represents gross spread available for capture.

**Thin competition**: The number of algorithmic market makers on Polymarket is small compared to centralized exchanges. Many resting orders are from retail participants who set and forget. A disciplined automated MM can systematically earn spread against this order flow.

**No need for latency supremacy**: Unlike latency arbitrage, market making is a passive strategy. You do not need to be faster than other participants to fill orders — orders come to you. However, you do need to cancel and reprice fast enough to avoid being picked off by informed flow.

## A.2 Expected Edge Source

The edge comes from:
1. **Spread capture**: Earning the bid-ask spread on round-trip trades (buy at bid, sell at ask).
2. **Inventory management alpha**: By skewing quotes based on inventory, the MM can accumulate inventory at favorable prices and reduce it at less-favorable prices, capturing mean-reversion.
3. **Time-decay capture**: For binary markets, a position at 0.50 probability has maximum uncertainty. As time passes without a decisive move, the position's value decays toward the fair value of an uninformative bet. The MM captures a portion of this decay through spread compression near resolution.

**Critical honest assessment**: Market making on binary markets with significant time-to-expiry is NOT a low-risk strategy. Unlike spot market making where the underlying drifts slowly, binary markets can move from 0.50 to 0.05 or 0.95 within minutes during a strong external price move. Inventory that is not hedged will suffer large mark-to-market losses during these events. The strategy requires:
- Disciplined adverse selection protection
- Aggressive inventory management
- Willingness to suspend quoting during adverse conditions

## A.3 Market Microstructure Characteristics

The same structural characteristics as Section A.3 of Strategy 1 apply, with additional emphasis:

**Order flow composition matters**: Incoming order flow on Polymarket is a mix of:
- Uninformed retail flow: people betting directionally based on news, sentiment, or gut feel. This is the flow the MM wants to trade against. It is expected to be mean-reverting (their entry timing is typically wrong or noise-driven).
- Informed flow: arbitrageurs (like the Strategy 1 bot), sophisticated directional traders, insiders (unfortunately, cannot be excluded). Filling against this flow is adverse selection — you will be on the wrong side of large moves.
- Other market makers: their orders cancel and reprice frequently; you rarely fill against them.

**Toxicity measurement**: The system must estimate the fraction of incoming flow that is informed vs. uninformed. Metrics for toxicity: (a) order flow imbalance — if all fills are on one side consistently, the flow is directional/toxic; (b) VPIN (Volume-synchronized Probability of Informed Trading) — a simplified implementation should track signed order imbalance per unit volume.

**Time-to-expiry dynamics**: The risk profile of a binary market changes fundamentally as expiry approaches:
- Far from expiry (> 30 minutes): spread can be tighter; fair value moves slowly with spot; inventory risk is moderate.
- Near expiry (5–15 minutes): fair value sensitivity to spot price explodes (high gamma); spread must widen significantly; inventory in the wrong direction can suffer severe losses.
- Very near expiry (< 2 minutes): market effectively resolves; should not be quoting.

## A.4 Expected Failure Regimes

1. **Adverse selection cascade**: A large external price move causes the MM to hold significant inventory in the losing direction. Mark-to-market loss exceeds stop threshold. Multiple simultaneous market adverse selections across all four assets simultaneously compound the loss.
2. **Spread compression by competitors**: Another market maker quotes tighter spreads, capturing all the beneficial order flow, leaving the MM with only adverse-selected fills.
3. **Stuck inventory near expiry**: Holding inventory in a market approaching expiry with no opposing flow — the inventory cannot be offloaded without crossing a very wide spread.
4. **Volatility regime change**: Entering a sustained high-volatility period where adverse selection exceeds spread capture.

## A.5 Expected Decay Mechanisms

- Increasing competition from other automated market makers.
- Polymarket liquidity growing (which is good for spread opportunity) but also attracting more sophisticated HFT participants.
- As prediction markets mature, information efficiency improves, reducing spread opportunity.

---

# B. EXACT TRADING LOGIC

## B.1 Fair Value Computation

The MM's fair value is computed identically to Strategy 1's fair value model:
```
fair_value = binary_option_prob(S, K, tau, sigma)
```
where inputs are sourced from the same external feeds (Binance/Bybit composite) and volatility estimator. The key difference: the MM does not need to detect a mispricing; it needs a continuously maintained fair value estimate around which to build symmetric (skewed when inventory warrants) quotes.

Fair value update frequency: every time the external composite mid changes by more than `FV_UPDATE_THRESHOLD_BPS` (e.g., 5bps) OR every `MAX_FV_AGE_MS` (e.g., 500ms), whichever comes first.

## B.2 Bid/Ask Quote Generation

**Base spread calculation**:
```
half_spread_base = max(
    MIN_HALF_SPREAD_BPS,
    SPREAD_VOLATILITY_MULTIPLIER * current_sigma_annualized * sqrt(tau_hours / HOURS_PER_YEAR),
    SPREAD_COMPETITION_FLOOR  (lowest spread observed in recent book)
)
```

Where:
- `MIN_HALF_SPREAD_BPS`: hard minimum below which we never quote (e.g., 30bps on probability scale)
- `SPREAD_VOLATILITY_MULTIPLIER`: calibrated parameter (higher vol → wider spread); start at 2.0
- `SPREAD_COMPETITION_FLOOR`: do not quote a spread so tight that we're the only liquidity; maintain a floor of (best_competitor_spread * 0.9) to ensure we're competitive without being the most aggressive

**Inventory skew adjustment**:
```
inventory_imbalance = current_position / MAX_POSITION_PER_MARKET
// ranges from -1.0 (max short) to +1.0 (max long)

skew_amount = inventory_imbalance * SKEW_SENSITIVITY * half_spread_base
// if long inventory, lower bid and raise ask (discourage more buys, encourage sells)
// if short inventory, raise bid and lower ask (encourage buys, discourage sells)

bid_price = fair_value - half_spread_base - skew_amount
ask_price = fair_value + half_spread_base - skew_amount
```

**Boundary enforcement**:
```
bid_price = max(bid_price, MIN_QUOTE_PRICE, 0.01)   // never below 1 cent
ask_price = min(ask_price, MAX_QUOTE_PRICE, 0.99)   // never above 99 cents
ask_price = max(ask_price, bid_price + MIN_SPREAD_ABS)  // enforce minimum spread
```

## B.3 Time-to-Expiry Spread Adaptation

```
if tau > 3600:          // > 1 hour
    spread_multiplier = 1.0
elif tau > 1800:        // 30 min - 1 hour
    spread_multiplier = 1.2
elif tau > 900:         // 15 - 30 min
    spread_multiplier = 1.5
elif tau > 300:         // 5 - 15 min
    spread_multiplier = 2.5
elif tau > 120:         // 2 - 5 min
    spread_multiplier = 5.0
elif tau <= 120:        // < 2 min: stop quoting
    SUSPEND_QUOTING(market_id)
```

These multipliers are initial values. They MUST be calibrated against live data — the correct multipliers depend on empirical adverse selection rates at each time horizon.

## B.4 Quote Refresh Timing

Quotes are refreshed (repriced) when:
1. Fair value moves more than `REPRICE_THRESHOLD_BPS` from current quote mid (e.g., 10bps)
2. Inventory changes (fill received)
3. Spread of current quote deviates from target by more than `REPRICE_SPREAD_TOLERANCE_BPS`
4. Quote age exceeds `MAX_QUOTE_AGE_MS` (maximum time any quote can be live without refresh; e.g., 5000ms)
5. Market conditions change (volatility spike, feed staleness detected)

Quote refresh implementation: cancel existing quotes, recompute, post new quotes. The cancel-then-post cycle has a latency window during which no liquidity is posted. Minimize this by pre-computing the new quote before canceling the old one.

## B.5 Adverse Selection Protection

**Order flow toxicity detection**:
```
// Compute rolling directional imbalance over last N fills
directional_imbalance = (buy_fills - sell_fills) / (buy_fills + sell_fills)
// ranges -1.0 (all sells, MM is accumulating longs) to +1.0 (all buys, MM accumulating shorts)

if abs(directional_imbalance) > TOXICITY_THRESHOLD (e.g., 0.7):
    ENTER_DEFENSIVE_MODE(market_id)
```

**Defensive mode behaviors**:
- Widen spread by `DEFENSIVE_SPREAD_MULTIPLIER` (e.g., 3x)
- Reduce quote size to `DEFENSIVE_QUOTE_SIZE` (e.g., 50% of normal)
- If directional imbalance > 0.90: suspend quoting entirely; attempt inventory reduction only

**Delta-hedge proxy**: Polymarket binary markets cannot be delta-hedged directly on the prediction market side. However, for large inventory positions, consider using the external exchange perp/spot as a hedge. This is complex and out of scope for initial deployment, but inventory limits implicitly limit unhedged exposure.

## B.6 Quote Cancellation Thresholds

Cancel ALL open quotes for a market immediately when:
- External composite mid moves more than `EMERGENCY_REPRICE_BPS` in any direction (e.g., 50bps on spot = large move)
- External feed becomes stale (`STALE_FEED_THRESHOLD_MS`)
- tau falls below `MIN_TAU_TO_QUOTE` (e.g., 120 seconds)
- Circuit breaker activates
- Market enters DEGRADED state

The cancel-quote response to external feed moves is the primary protection against adverse selection from informed flow. Speed of cancellation is critical.

## B.7 Inventory Mean Reversion and Directional Exposure Control

The MM should not accumulate large directional positions. Inventory management is the core operational challenge.

**Inventory limits**:
```
MAX_INVENTORY_PER_MARKET   = config.max_inventory_per_market (e.g., 100 contracts)
MAX_NET_INVENTORY_PER_ASSET = config.max_net_asset_inventory (e.g., 200 contracts equiv.)
MAX_NET_TOTAL_INVENTORY     = config.max_net_total_inventory
```

**Inventory reduction logic**:
When inventory exceeds `SOFT_INVENTORY_LIMIT` (e.g., 60% of max):
- Increase skew aggressively to lean quotes toward inventory reduction
- Widen the spread on the inventory-adding side

When inventory exceeds `HARD_INVENTORY_LIMIT` (e.g., 85% of max):
- Stop quoting on the inventory-adding side entirely (one-sided quoting only)
- Post a market-order-equivalent aggressive limit on the inventory-reducing side

When inventory exceeds MAX (circuit breaker):
- Cancel all quotes; post aggressive market-order limit to reduce inventory immediately
- Enter DEFENSIVE mode for `HARD_INVENTORY_RECOVERY_COOLDOWN_MS` after reduction

## B.8 When to Stop Quoting Entirely

Stop quoting a market when any of the following:
- tau < MIN_TAU_TO_QUOTE
- External feed stale
- Circuit breaker active
- Inventory at hard limit and cannot reduce
- Toxicity measure at extreme level
- Market about to resolve (resolution mechanism active)
- Polymarket WS disconnected or book stale

Stop quoting ALL markets when:
- Total portfolio loss > daily_loss_limit
- System in PANIC mode
- Kill switch activated

## B.9 State Transitions

```
MARKET MAKING STATE for each market_id:
  INACTIVE → INITIALIZING (building initial book/FV) → QUOTING_BOTH_SIDES
  QUOTING_BOTH_SIDES → DEFENSIVE (high toxicity/vol) → QUOTING_BOTH_SIDES (after recovery)
  QUOTING_BOTH_SIDES → ONE_SIDED_ONLY (inventory limit approached) → QUOTING_BOTH_SIDES
  QUOTING_BOTH_SIDES → SUSPENDED (expiry/circuit breaker) → INACTIVE
  Any state → EMERGENCY (inventory hard limit / loss limit) → INACTIVE
```

---

# C. SYSTEM ARCHITECTURE

## C.1 Module Map

The MM system shares the data ingestion layer and configuration layer with Strategy 1 but has independent signal engine, execution engine, portfolio state engine, and risk engine.

```
┌─────────────────────────────────────────────────────────────────┐
│              SHARED DATA INGESTION LAYER (same as Strat 1)      │
│  BinanceWSConsumer | BybitWSConsumer | PolymarketWSConsumer      │
│  FeedNormalizer | BookBuilder                                    │
└─────────────────────────────────────────────────────────────────┘
          │
┌─────────────────────────────────────────────────────────────────┐
│              MM-SPECIFIC SIGNAL ENGINE                          │
│  FairValueEngine (shared logic, independent instance)           │
│  VolatilityEstimator | TauCalculator                            │
│  QuoteEngine (bid/ask generation with all adjustments)          │
│  ToxicityMonitor | OrderFlowAnalyzer                            │
│  InventorySkewCalculator | SpreadAdaptationEngine               │
└─────────────────────────────────────────────────────────────────┘
          │
┌─────────────────────────────────────────────────────────────────┐
│              MM-SPECIFIC RISK ENGINE                            │
│  InventoryLimitChecker | ExposureEngine | InventoryHeatmap      │
│  AdverseSelectionMonitor | DrawdownMonitor | CircuitBreaker     │
└─────────────────────────────────────────────────────────────────┘
          │
┌─────────────────────────────────────────────────────────────────┐
│              MM-SPECIFIC EXECUTION ENGINE                       │
│  QuoteSubmitter | QuoteCancellationManager | QuoteTracker       │
│  FillHandler | PartialFillHandler | QuoteRefreshScheduler       │
│  RateLimiter (independent token bucket from Strat 1)            │
└─────────────────────────────────────────────────────────────────┘
          │
┌─────────────────────────────────────────────────────────────────┐
│              MM-SPECIFIC PORTFOLIO STATE ENGINE                 │
│  InventoryStore | PnLEngine | ExposureTracker                   │
│  MakerFillAccounting | ReconciliationEngine                     │
└─────────────────────────────────────────────────────────────────┘
```

## C.2 Concurrency Model

Market making has a fundamentally different hot path from latency arb:
- The hot path is: External price change → recompute fair value → compute new quotes → cancel old quotes → post new quotes
- This must complete within `MAX_REPRICE_LATENCY_MS` (e.g., 100ms) to avoid extended exposure to stale quotes
- Quote submission is NOT as latency-critical as in Strategy 1, but quote cancellation IS — a stale bid/ask that gets hit after a large move is the primary loss mechanism

Key design: prioritize order cancellation latency over quote posting latency. Use pre-built cancel messages to minimize time-to-cancel.

## C.3 Quote Lifecycle Management

Each live quote has a full state machine:
```
QUOTE STATE: PENDING_POST → LIVE → [FILLED | PARTIALLY_FILLED | CANCELLED]
```

The system maintains a quote registry: `Map<market_id, QuoteState>` where QuoteState contains:
- current bid quote (exchange_order_id, price, size, posted_at_ts)
- current ask quote (exchange_order_id, price, size, posted_at_ts)
- pending cancellations
- pending reprice

When a reprice is triggered while a cancel is in-flight, the system must handle the race condition: if the cancel is acknowledged before the new quote is posted, there is a brief window with no liquidity posted (acceptable). If a fill arrives during the in-flight cancel, it must be processed as a fill against the old quote regardless of the cancellation request.

---

# D. PAPER TRADING SYSTEM DESIGN

## D.1 Critical MM-Specific Simulation Biases

Market making paper trading has uniquely severe simulation biases that must be explicitly modeled:

**Passive fill bias** (most severe): In paper trading, simulated passive fills assume you get filled whenever the market trades at your price. In reality, you are only filled if your queue position at that price level is reached. Polymarket is a CLOB — queue position is price-time priority. A live order posted after several other orders at the same price will be filled last.

**Mitigation**: Apply a queue-position fill probability model:
```
P(fill | price_crossed) = queue_fraction_model(
    time_since_posted,      // longer → better queue position over time
    num_concurrent_quotes,  // more competition → worse queue
    order_size,             // larger → takes longer to fill through queue
)
```
This model MUST be calibrated against live data. Initial assumption: 40% fill probability on any given crossing event if queue position is unknown. This is a conservative assumption.

**Spread collapse bias**: In paper trading, the simulated spread never tightens due to your own quotes. In reality, posting tight quotes invites other participants to match or better your quotes, narrowing the effective spread you collect.

**Inventory smoothing bias**: In paper trading, inventory builds and reduces smoothly. In production, large fills happen at single moments, and large inventory imbalances can persist for extended periods without opposing flow.

## D.2 Fill Simulation for Passive Orders

For bid orders:
```
simulated_fill_trigger = any_trade_at_or_below_bid_price
if simulated_fill_trigger:
    fill_probability = queue_position_fill_probability_model()
    if random() < fill_probability:
        record_fill(price=bid_price, size=min(order_size, available_opposing_volume))
```

The `available_opposing_volume` is taken from the trade print stream, not the order book. This ensures fills are only simulated when actual trades occur, not just when the order book shows a crossing.

## D.3 Maker Fee Modeling

Polymarket charges different fees for maker vs. taker orders. Maker orders (passive limit orders) currently pay zero or reduced fees. This must be accurately modeled in paper trading:
```
fee_per_fill_maker = MAKER_FEE_RATE * fill_value  // typically 0 or very low
fee_per_fill_taker = TAKER_FEE_RATE * fill_value  // higher
```

The gas cost per transaction applies regardless of maker/taker status and is a significant factor for small trade sizes.

## D.4 Spread PnL Attribution

The MM PnL must be broken down into:
1. **Spread PnL**: PnL earned purely from bid-ask spread (round-trip trades)
2. **Inventory PnL**: PnL earned/lost from holding directional inventory
3. **Fee PnL**: Net fees paid (expected to be negative but small for maker)
4. **Gamma PnL**: PnL from options-like convexity effects near expiry

This attribution is critical for understanding whether the strategy is working as designed (positive spread PnL, bounded inventory PnL) or is generating PnL from lucky directional bets (misleading positive results in paper trading).

---

# E. LIVE PRODUCTION SYSTEM DESIGN

## E.1 Quote Management Lifecycle

Live market making requires persistent, managed liquidity on both sides. The system maintains:
- A target quote state (what quotes should be live)
- An actual quote state (what quotes are confirmed live on exchange)
- A pending queue (quotes in flight: posted but not confirmed, or cancelled but not confirmed)

Quote state reconciliation runs every `QUOTE_RECONCILE_INTERVAL_MS` (e.g., 1000ms):
```
for each market_id:
    target = compute_target_quote_state(market_id)
    actual = get_confirmed_live_quotes(market_id)
    diff   = reconcile(target, actual)
    for action in diff.required_actions:
        if action.type == CANCEL: submit_cancel(action.order_id)
        if action.type == POST:   submit_quote(action.quote)
        if action.type == AMEND:  // Polymarket may support amend; if not, cancel+repost
```

## E.2 Emergency Inventory Handling

If inventory reaches the hard limit and cannot be reduced by passive quoting:
1. Cancel all passive quotes immediately.
2. Compute the fair exit price for the excess inventory.
3. Post an aggressive limit order at `fair_value - EMERGENCY_EXIT_PREMIUM_BPS` (willing to cross spread to exit).
4. Log emergency inventory event with full context.
5. Alert operator.
6. Resume normal quoting only after inventory is within soft limit.

## E.3 Connection Architecture

Market making requires persistent, low-latency connections:
- One persistent WebSocket connection to Polymarket for receiving fills (do not reconnect per quote)
- One persistent HTTP/2 connection pool for order submission (avoid per-request connection overhead)
- Pre-computed CLOB message serializations for common quote sizes and price levels (reduces per-quote computation)

## E.4 Heartbeat and Feed Health Monitoring

Market making is uniquely sensitive to feed staleness: a stale fair value estimate means quoting at wrong prices for an extended period, leading to adverse fills.

Heartbeat checks:
- External feed health: if Binance or Bybit feed is stale > 500ms → immediate quote cancellation
- Polymarket book staleness: if book has not updated in > 2 seconds → assume connection issue; suspend quoting
- Self-heartbeat: the system must produce a heartbeat event every 100ms to prove the event loop is not blocked; if heartbeat is missed for 500ms → system alerts and potentially halts

---

# F. LATENCY ARBITRAGE STRATEGY-SPECIFIC REQUIREMENTS

*(Not applicable to this strategy — see Strategy 1)*

---

# G. MARKET MAKING STRATEGY-SPECIFIC REQUIREMENTS

## G.1 Spread Competitiveness Logic

The MM must be competitive in the order book but not suicidally tight. Competition awareness:
```
// Observe current book best bid/ask
poly_best_bid = book.bids[0].price
poly_best_ask = book.asks[0].price
current_book_spread = poly_best_ask - poly_best_bid

// Our target quotes
our_bid = fair_value - half_spread_adjusted
our_ask = fair_value + half_spread_adjusted

// Competitiveness check: are we inside, at, or outside the book?
if our_ask < poly_best_ask:  // we are inside the ask → we are most aggressive ask
    // This is good; we may capture the next buy
elif our_ask == poly_best_ask:  // we are at best ask → shared queue
    // Acceptable
elif our_ask > poly_best_ask:  // we are outside the ask → our ask won't fill first
    // Consider whether to tighten; never tighten below MIN_HALF_SPREAD_BPS
```

The MM should NOT chase the spread tighter than its model supports. Better to be second-best and earn full spread occasionally than to be best-ask and earn nothing (or lose to adverse selection).

## G.2 Toxicity Filtering in Detail

**VPIN-inspired metric**:
```
// Compute order flow imbalance over last V volume units
for each trade in last_N_trades:
    if trade.side == BUY:   buy_volume += trade.size
    else:                   sell_volume += trade.size

total_volume = buy_volume + sell_volume
order_imbalance = abs(buy_volume - sell_volume) / total_volume

// High order_imbalance → directional, potentially informed flow
toxicity_signal = order_imbalance  // ranges 0.0 (balanced) to 1.0 (all one-sided)
```

**Toxicity thresholds**:
- < 0.40: Normal market making conditions; standard spreads
- 0.40–0.60: Mildly directional; widen spread by 20%
- 0.60–0.75: Directional; widen spread by 50%; reduce quote size
- 0.75–0.90: Highly directional; one-sided quoting only; aggressive inventory management
- > 0.90: Suspend quoting; emergency inventory reduction if needed

**Rolling window**: Toxicity is computed over the last `TOXICITY_WINDOW_TRADES` trades (e.g., 20 trades). Shorter window is more responsive but more noisy; longer is more stable but slower to detect regime change. Must be calibrated.

## G.3 Quote Fade Conditions

Quote fade = cancel quotes without immediate replacement (temporary withdrawal of liquidity).

Trigger quote fade when:
- External composite mid moves more than `FADE_TRIGGER_BPS` in < `FADE_TRIGGER_WINDOW_MS` (fast market condition)
- Trade print size exceeds `LARGE_TRADE_THRESHOLD_CONTRACTS` (large informed order hits the book)
- Multiple large prints on same side within `LARGE_PRINT_WINDOW_MS` (sustained directional aggression)
- Fair value estimate uncertainty is high (vol estimate confidence low)

Fade duration: `FADE_DURATION_MS` (e.g., 500–2000ms). After fade, recompute quotes from scratch and re-enter.

## G.4 Market Making in 5-Minute Markets

5-minute markets are the most dangerous environment for market making:
- tau is always < 5 minutes by definition
- The gamma exposure is always very high
- A 1% spot move can swing fair value by 10+ percentage points
- Adverse selection risk is extreme

**Recommendation**: Do NOT quote 5-minute markets until the strategy is well-validated in 1-hour markets. If quoting, use minimum size (1 contract), maximum spreads (5x normal), and stop quoting at tau < 3 minutes.

## G.5 Market Making in 1-Hour Markets

1-hour markets are the most suitable for market making:
- tau is large (> 30 minutes typically) → fair value moves slowly with spot
- Gamma is relatively low
- Liquidity is deeper
- Adverse selection is less extreme (more time for mean reversion)

Start deployment here. Calibrate all parameters against 1-hour markets before expanding.

## G.6 Inventory Skew Calibration

The optimal skew sensitivity (SKEW_SENSITIVITY) must be empirically calibrated. Too much skew → you rarely fill on the over-skewed side → low fill rate → low PnL. Too little skew → inventory accumulates → directional risk builds.

Target calibration: at 50% of max inventory, the skew should reduce the expected fill rate on the inventory-adding side by approximately 30–40% relative to zero-skew. Verify this empirically from paper trading fill data.

---

# H. DATA REQUIREMENTS

## H.1 MM-Specific Data Requirements

**Mandatory**:
- Polymarket CLOB Level 2 full depth (not just top-of-book) — depth allows accurate queue position estimation
- Polymarket trade print stream (essential for toxicity monitoring and fill simulation)
- External feed as in Strategy 1

**Strongly recommended**:
- Polymarket historical trade prints (for calibrating toxicity thresholds)
- Historical Polymarket order book snapshots at 1-second intervals (for queue position estimation calibration)
- Historical fill rates per price level (to calibrate queue-position fill probability model)

**Critical for paper trading realism**:
- Historical trade print data is critical. Without it, fill simulation must use synthetic assumptions that are likely very wrong. Minimum 90 days of Polymarket trade prints per target market.

## H.2 Data Collection Priority

MM has a higher ongoing data collection requirement than latency arb because model parameters (toxicity thresholds, skew sensitivity, spread multipliers) must be recalibrated regularly as market conditions change.

Build a separate data collection service that:
1. Captures and stores all Polymarket WS messages (book updates + trade prints) with recv_ts
2. Stores all Binance/Bybit tick data
3. Computes and stores derived metrics hourly (realized vol, toxicity stats, book depth stats)
4. Provides a queryable interface for the calibration tooling

---

# I. OBSERVABILITY, LOGGING, TELEMETRY, AND KPIs

## I.1 Console Summary (every 30 seconds)

```
========================================================================
POLYMARKET MARKET MAKER | 2024-XX-XX HH:MM:SS UTC | MODE: [PAPER/LIVE]
========================================================================
CAPITAL:        Available: $X,XXX.XX | In Use: $X,XXX.XX | Total: $X,XXX.XX
EXPOSURE:       Total: $X,XXX.XX | BTC: $XXX | ETH: $XXX | SOL: $XX | XRP: $XX
------------------------------------------------------------------------
PnL:            Realized: +$XXX.XX | Unrealized: +$XX.XX | Total: +$XXX.XX
PnL BREAKDOWN:  Spread PnL: +$XXX | Inventory PnL: -$XX | Fee PnL: -$XX
DRAWDOWN:       Current: -X.X% | Max (session): -X.X%
SHARPE (1H):    X.XX
------------------------------------------------------------------------
ACTIVE MARKETS: X quoting | X suspended | X in defensive | X in 1-sided
INVENTORY (contracts):
  BTC 5min:  Net:  +12 | Max: 100 | Heat: ██░░░░░░░░ 12%
  BTC 15min: Net:  -34 | Max: 100 | Heat: ███░░░░░░░ 34% ← SHORT SKEWED
  BTC 1hr:   Net:   +7 | Max: 200 | Heat: ░░░░░░░░░░  4%
  ETH 1hr:   Net:  +89 | Max: 200 | Heat: ████████░░ 45% ← WARNING
------------------------------------------------------------------------
QUOTES LIVE:    X bid orders | X ask orders | X pending cancels
FILL STATS:     Maker Fills: XXX | Taker Fills: XX | Partial Fills: XX
                Quote Hit Rate (bid): XX% | Quote Hit Rate (ask): XX%
                Fill Rate vs Queue Model: actual XX% vs model XX%
SPREAD STATS:   Avg Spread Earned: X.X bps | Target: X.X bps
TOXICITY:       BTC 1hr: LOW (X.XX) | ETH 1hr: MED (X.XX) | BTC 15m: HIGH (X.XX)
------------------------------------------------------------------------
LATENCY (ms):   Quote Cancel: p50=X p95=XX | Quote Post: p50=XX p95=XXX
FEED HEALTH:    Binance: LIVE (Xms) | Bybit: LIVE (Xms) | Poly: LIVE (Xms)
WS RECONNECTS:  X (session) | Quote Fades: XX (session)
ERRORS:         Rejections: X | Cancels Missed: X | Stale Quotes: X
========================================================================
```

## I.2 MM-Specific KPIs

**Primary KPIs**:
- Total PnL (realized + unrealized)
- Spread PnL (isolated from inventory PnL)
- Inventory PnL (isolated directional component)
- Win rate by market (% of round-trips profitable)
- Average spread captured vs. average spread targeted

**Execution quality metrics**:
- Quote hit rate (% of quotes that get filled at each side)
- Queue fill model accuracy (predicted fill probability vs. actual)
- Average time quotes are live before fill or cancel
- Cancel-to-post ratio (very high = spending all time canceling, not collecting)
- Adverse fill rate (fills that precede adverse price moves > X bps)

**Inventory metrics**:
- Average absolute inventory level (should be low if skew is working)
- Max inventory reached (per market, per session)
- Time spent at inventory soft limit (should be < 20% of active time)
- Time spent at inventory hard limit (should be < 1% — this is a warning sign)
- Inventory reduction trade count and average cost

**Toxicity metrics**:
- Toxicity score distribution (histogram)
- Time spent in defensive mode (per market)
- Time spent suspended per market

---

# J. TESTING AND VALIDATION

## J.1 MM-Specific Unit Tests

- QuoteEngine: test bid/ask generation for known fair value, inventory, spread inputs; verify boundary enforcement; verify skew direction is correct
- InventorySkewCalculator: verify skew increases as inventory increases; verify hard limit triggers correctly
- ToxicityMonitor: feed synthetic trade sequence; verify imbalance calculation; verify threshold triggers
- SpreadAdaptationEngine: verify tau-based spread multipliers; verify all tau thresholds
- QuoteRefreshScheduler: verify reprice triggers; verify MAX_QUOTE_AGE_MS; verify all cancellation triggers

## J.2 MM-Specific Integration Tests

- Round-trip test: simulate a buy fill on bid, then sell fill on ask; verify PnL = spread earned - fees
- Inventory accumulation test: simulate 50 consecutive buy fills; verify skew reaches maximum; verify one-sided quoting activates
- Hard limit test: push inventory to hard limit; verify emergency exit activates
- Toxicity test: simulate 20 consecutive one-sided fills; verify defensive mode activates; verify quotes widen
- Expiry approach test: advance tau to 90 seconds; verify quoting suspends
- Feed stale test: stop external feed for 1 second; verify immediate quote cancellation

## J.3 Paper Trading Validation for MM

Key metrics to validate in paper trading (measured against actual Polymarket live order flow data):
- Fill rate per price level: compare simulated to actual trades at those levels
- PnL attribution: spread vs. inventory vs. fee should sum to total
- Adverse selection measurement: what fraction of fills precede adverse moves > 20bps?
- Inventory turnover: average inventory duration; mean reversion speed

**Production readiness criteria for MM** (stricter than Strat 1 due to higher operational complexity):
- 60-day paper trading run minimum (vs. 30 days for Strategy 1)
- Positive spread PnL with clearly separated inventory PnL
- Adverse selection rate < 30% of fills (empirically measured)
- Queue fill model accuracy within 25% of predictions
- Zero inventory hard-limit events in last 30 days of paper trading
- All circuit breakers tested in integration environment

---

# K. IMPLEMENTATION ROADMAP

## Phase 0: Infrastructure Foundation (Weeks 1–2)
**Shared with Strategy 1.** If building both strategies, this phase needs to be done only once.

## Phase 1: Fair Value Engine and Quoting Logic (Weeks 3–5)
**Objectives**: Build and validate the core quoting engine.
**Tasks**:
- Implement FairValueEngine (can reuse Strat 1 module)
- Implement QuoteEngine with full spread generation logic
- Implement InventorySkewCalculator
- Implement SpreadAdaptationEngine with tau-based adjustments
- Unit test all components
**Validation**: Quotes generated correctly from synthetic inputs
**Risk**: Spread calibration; optimal SKEW_SENSITIVITY unknown pre-live
**Complexity**: High
**Deliverable**: QuoteEngine producing correct bid/ask for all inputs

## Phase 2: Toxicity and Adverse Selection Layer (Weeks 6–7)
**Tasks**:
- Implement ToxicityMonitor with rolling order flow imbalance
- Implement OrderFlowAnalyzer
- Implement defensive mode logic
- Connect to QuoteEngine (toxicity inputs to spread adjustments)
**Complexity**: Medium-High
**Deliverable**: Working adverse selection protection

## Phase 3: Execution Layer (Weeks 8–9)
**Tasks**:
- Implement QuoteSubmitter, CancellationManager, QuoteTracker
- Implement QuoteRefreshScheduler with all trigger conditions
- Implement fill handler and partial fill handler
- Implement rate limiter (independent of Strategy 1)
**Complexity**: High
**Deliverable**: Full execution loop working in paper mode

## Phase 4: Paper Trading (Weeks 10–14)
**Objectives**: Begin 60-day paper trading run; calibrate models.
**Tasks**:
- Deploy full paper trading bot
- Collect fill data for queue model calibration
- Collect toxicity data for threshold calibration
- Run full test suite
- Calibrate skew sensitivity from paper trading results
**Validation**: PnL attribution stable; no critical bugs
**Risk**: Low fill rates; adverse selection higher than expected
**Deliverable**: Calibrated paper trading system

## Phase 5: Paper Trading Extended Validation (Weeks 15–22)
**Objectives**: Continue 60-day run; validate calibrated model; meet production readiness criteria.

## Phase 6: Live Production (Weeks 23–28)
**Objectives**: Deploy live with minimal capital; 1-hour BTC market only initially.
**Tasks**:
- Deploy live system on 1-hour BTC markets only
- $200 initial capital; maximum 1 contract per side
- Monitor adverse selection rate; compare to paper
- Gradually add markets and capital as validation grows

---

# L. DELIVERABLES

## Paper Trading Deliverables
- `market_maker/paper/main.py`
- `market_maker/paper/fill_simulator_passive.py`
- `market_maker/paper/queue_position_model.py`
- `market_maker/paper/pnl_engine.py` (with PnL attribution)

## Production Deliverables
- `market_maker/live/main.py`
- `market_maker/live/quote_submitter.py`
- `market_maker/live/quote_lifecycle_manager.py`
- `market_maker/live/emergency_inventory_handler.py`
- `market_maker/live/reconciliation.py`

## Shared / Reusable
- `shared/ws_consumers/` (both strategies)
- `shared/fair_value/` (both strategies use same FV model)
- `shared/config/` (shared secrets management)
- `shared/logging/` (same structured logger)

## Tooling
- `tools/calibration/toxicity_threshold_calibrator.py`
- `tools/calibration/skew_sensitivity_optimizer.py`
- `tools/data_collection/polymarket_book_recorder.py`
- `tools/replay/mm_replay_harness.py`
- `ops/mm_runbook.md`
- `ops/mm_emergency_procedures.md`

---

# M. CRITICAL FAILURE MODES

## M.1 The Inventory Trap

Market making's most catastrophic failure: accumulating large directional inventory just before a large adverse price move. This is not a latent risk — it WILL happen. The question is whether the position size is small enough that the loss is tolerable.

**Scenario**: BTC is at $64,900 with a $65,000 strike on the 1-hour market. Fair value ≈ 0.45 (slightly OTM). Market maker is quoting bid=0.42, ask=0.48. A sequence of retail buy orders fills the MM ask repeatedly. MM is now short 150 contracts at avg price 0.47. Then a large Binance buyer pushes BTC to $65,200. Fair value jumps to 0.72. The MM is short 150 contracts of a contract now worth 0.72, having sold at avg 0.47. Loss: 150 × (0.72 - 0.47) × $1 = $37.50 per $1-sized contract. Scaled to real notional, this can be significant.

**Mitigation depth**: This scenario CANNOT be fully mitigated. It can only be bounded by position limits and spread widening. The MM must accept that it will lose on directional moves and must earn enough spread on normal flow to make these losses a fraction of total earnings.

## M.2 The Passive Fill Rate Illusion

The most dangerous paper trading bias: overestimated passive fill rate. If the queue model overstates your queue position (assumes 60% fill probability when reality is 20%), paper trading will show 3x the fill rate of production. Paper trading looks excellent; live trading is a disaster.

**Detection**: Compare paper trading fill rate per price level to actual observed trade volume at that level during the paper trading period. If simulated fills exceed actual crossing volume at your queue fraction, the model is optimistic.

**Mitigation**: Default queue assumption should be 25% fill probability (pessimistic), not 50% or higher. Only increase after empirical validation.

## M.3 Gas Cost Accumulation

Market making generates many more transactions than latency arb (posting/canceling quotes frequently). Each cancel and each post is a Polygon transaction with gas cost. At high quote refresh rates, gas costs can consume all spread PnL.

**Empirical measurement required**: Measure actual gas cost per quote cycle at various network congestion levels. Compute minimum viable spread given gas costs. This may force minimum quote size larger than desired.

## M.4 Adversarial Order Flow

On thin prediction markets, adversarial bots may attempt to manipulate the order book to extract fills from market makers at unfavorable prices. Specific patterns:
- **Quote stuffing**: Rapid posting and cancellation of large quotes to make the book appear deep, then withdrawing when the MM provides opposing liquidity.
- **Layering**: Posting fake interest on one side to push the fair value estimate, then taking the other side.

These are difficult to fully protect against. Detection heuristics:
- Flag participants whose orders cancel within `MANIPULATION_CANCEL_THRESHOLD_MS` (e.g., 200ms) of posting at high frequency
- Increase spread when book depth appears anomalously deep relative to historical norms

## M.5 Resolution Edge Cases

Binary market resolutions are not always clean:
- **Disputed resolutions**: The UMA oracle can be disputed, leading to a multi-day resolution process. During this time, the market may be halted or continue trading. Positions are frozen or illiquid.
- **Oracle failure**: The resolution oracle fails to report, leading to a void or delayed resolution.
- **Ambiguous resolution criteria**: Some markets have resolution criteria that are subject to interpretation.

The MM system must monitor resolution status via API and immediately suspend quoting in any market where resolution status is non-standard.