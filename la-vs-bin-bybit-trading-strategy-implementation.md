# POLYMARKET HFT SYSTEM — STRATEGY 1: LATENCY ARBITRAGE
## Internal Architecture & Execution Design Document
### Version 1.0 | Confidential Engineering Document

---

# PREAMBLE

This document is an engineering-grade implementation plan for a Latency Arbitrage trading system targeting Polymarket BTC/ETH/SOL/XRP binary prediction markets, using Binance and Bybit as external price oracles. It is written for a technical engineer building real production systems. Nothing herein is simplified. All operational failure modes, execution constraints, and simulation biases are treated as first-class engineering concerns.

---

# A. STRATEGY THESIS AND MARKET MICROSTRUCTURE

## A.1 Why Latency Arbitrage May Work on Polymarket

Polymarket binary markets on crypto assets (BTC/ETH/SOL/XRP) price the probability that the spot price of an asset will exceed a given strike at a defined resolution time. These markets are fundamentally derivative instruments on the underlying spot price, but their liquidity structure, participant composition, and order flow toxicity differ significantly from centralized exchange order books.

The core thesis: the Polymarket order book refreshes more slowly than the Binance/Bybit spot and perpetual order books. A participant who observes a directional price move on Binance before that information is fully digested by Polymarket liquidity providers can buy or sell probability at a price that has not yet adjusted to reflect the new external signal. The edge is the lag between external price discovery and Polymarket order book repricing.

This is not risk-free arbitrage. It is a probabilistic edge that depends on:
1. Your latency advantage being real and persistent against the current Polymarket market-making cohort.
2. The external signal being directionally predictive for the resolution outcome, not merely for short-term price noise.
3. The Polymarket book not having already priced the move before your execution lands.

**Critical distinction**: This is NOT a pure riskless arb. You are not simultaneously hedging on Binance and Polymarket. You are taking a directional position on Polymarket, informed by a fast external signal. The risk of being wrong on direction before resolution remains. Holding period is not sub-second — it extends to the resolution of the market (hours to days in some cases). This changes the risk profile fundamentally versus traditional HFT latency arb.

## A.2 Expected Edge Source

- **Information lag**: Polymarket market makers are slower to cancel and reprice quotes after external price moves.
- **Participant heterogeneity**: Retail participants and slower bots post stale quotes that can be lifted or hit at advantageous implied probabilities.
- **Binary optionality mispricing**: During rapid spot moves, the immediate change in resolution probability is nonlinear and not trivially computed — many participants use linear approximations or delayed models.
- **Spread monetization**: When Polymarket spreads widen during volatility, the bot can cross at more favorable prices than the fair value implies.

**Empirically uncertain assumptions** (must be measured in production):
- Actual latency advantage over current Polymarket participants: unknown until live testing.
- Rate of stale quote refreshing by existing Polymarket market makers.
- Frequency and persistence of mispricings exceeding post-fee break-even thresholds.

## A.3 Market Microstructure Characteristics

Polymarket binary markets have the following structural characteristics that define the operating environment:

**Order book depth**: Thin relative to centralized exchanges. Best bid/ask spreads on BTC/ETH markets are typically 1–3 cents on a $0.50 contract (2–6% relative spread). SOL/XRP markets are structurally wider and less liquid. Depth beyond the top of book degrades quickly.

**Participant composition**: A mix of directional retail speculators, slower automated market makers, news-driven position takers, and arbitrageurs watching the same external signals you are. The competition for latency arb edges is real and growing.

**Resolution mechanics**: Markets resolve via the UMA optimistic oracle or Polymarket's own resolution process. Resolution is NOT continuous — there is a defined resolution time (end of 5-min, 15-min, 1-hour candle), and price is typically taken from a reference oracle (often a centralized exchange VWAP or spot price at a specific timestamp). This means:
- Resolution probability is time-dependent: as expiry approaches, a market trading at 0.60 with the price currently above the strike will converge toward 1.00 or 0.00 very rapidly.
- The delta of the implied probability with respect to the underlying spot price changes as time-to-expiry shrinks — this is the binary option "gamma" effect.
- Holding a position to expiry has very different risk from trading in/out.

**Implied probability dynamics**: For a binary market "BTC above $65,000 at 3:00 PM UTC", the fair value probability is approximately:
```
P(S_T > K) = N(d2) where d2 = [ln(S/K) + (-0.5*σ²)*τ] / (σ*√τ)
```
where S is current spot, K is strike, τ is time-to-expiry in years, σ is realized/implied volatility. At long time horizons, probability changes slowly with spot. Near expiry, delta (sensitivity of P to spot) explodes. This creates both opportunity and danger.

**Spread dynamics**: Spreads on Polymarket widen meaningfully during:
- News events (CPI, FOMC, major exchange outages)
- High spot volatility regimes
- Approach to resolution time (especially the final 5–15 minutes)
- Low-liquidity hours

**Liquidity fragmentation**: Each strike/expiry combination is a separate market. BTC markets are more liquid than ETH, which is more liquid than SOL/XRP. 1-hour markets have more liquidity than 5-minute markets. 5-minute markets near expiry can be nearly illiquid.

## A.4 Expected Failure Regimes

1. **Latency erosion**: Other participants upgrade infrastructure and the lag differential disappears.
2. **Gamma trap**: Near expiry, small moves in spot cause large swings in fair value. A position entered on an arb signal can invert before the next quote update.
3. **Resolution oracle divergence**: Polymarket's resolution oracle uses a different price source than the Binance/Bybit feeds you're watching. The arb signal disappears when the oracle source diverges.
4. **Market adaptation**: Market makers widen spreads aggressively to defend against arbitrageurs, eliminating edge.
5. **Feed outage during position hold**: External feed goes stale while you hold an unhedged Polymarket position with time-to-expiry remaining.

## A.5 Expected Decay Mechanisms

- Competition from other latency arb participants will compress edge over months.
- Polymarket market makers will progressively shorten quote refresh cycles as the platform grows.
- The strategy edge is likely front-loaded into the first 6–12 months of deployment and will require continuous recalibration.

---

# B. EXACT TRADING LOGIC

## B.1 Signal Generation

**External feed normalization**: The system maintains a real-time composite reference price for each asset (BTC, ETH, SOL, XRP) computed from:
- Binance spot best mid: `(best_bid_binance + best_ask_binance) / 2`
- Bybit spot best mid: `(best_bid_bybit + best_ask_bybit) / 2`
- Composite mid: `weighted_avg(binance_mid, bybit_mid, weights=[0.6, 0.4])` (weights are configurable and should be calibrated)

The composite price is only considered valid if:
- Both feeds have been updated within `STALE_FEED_THRESHOLD_MS` (e.g., 500ms; must be empirically calibrated)
- The spread on each exchange is within `MAX_EXCHANGE_SPREAD_BPS` (e.g., 10bps; filters micro-burst dislocations)
- Binance and Bybit mids do not deviate by more than `MAX_FEED_DIVERGENCE_BPS` from each other (e.g., 15bps; flags feed corruption or cross-exchange dislocation)

**Fair value computation**:
```
fair_value(market) = binary_option_prob(
    S       = composite_mid,
    K       = market.strike_price,
    tau     = (market.resolution_timestamp - now_utc()) / SECONDS_PER_YEAR,
    sigma   = get_volatility_estimate(asset, tau),
    resolution_source = market.oracle_reference
)
```

The volatility estimate sigma is computed from:
- Rolling realized volatility over the last N periods (configurable, e.g., 20 candles of the resolution timeframe)
- A forward-looking adjustment factor derived from option-implied volatility on Binance/Bybit if available
- An emergency fallback constant (e.g., 80% annualized) when live vol data is unavailable

**Critical**: tau must use the oracle resolution time, NOT the market closing time. These can differ.

**Mispricing detection**:
```
poly_best_ask = polymarket_order_book[market_id].asks[0].price
poly_best_bid = polymarket_order_book[market_id].bids[0].price
mid_poly = (poly_best_ask + poly_best_bid) / 2

edge_to_buy  = fair_value - poly_best_ask
edge_to_sell = poly_best_bid - fair_value

raw_edge_buy  = edge_to_buy  - TOTAL_FEE_ESTIMATE
raw_edge_sell = edge_to_sell - TOTAL_FEE_ESTIMATE
```

**TOTAL_FEE_ESTIMATE** includes:
- Polymarket taker fee (currently ~2bps on winning side; verify at deployment time)
- Expected slippage above best ask (modeled as a function of trade size / available liquidity at best level)
- Gas cost normalized to contract size (CLOB gas on Polygon; variable)

**Signal trigger conditions** (ALL must be true):
```
SIGNAL_BUY if:
  raw_edge_buy > MIN_EDGE_BPS (configurable, e.g., 50bps on probability scale)
  AND tau > MIN_TAU (e.g., 2 minutes to expiry — below this, gamma risk too high)
  AND tau < MAX_TAU (e.g., 59 minutes — avoid deep OTM markets with negligible delta)
  AND external_feed_is_valid() == True
  AND polymarket_book_is_fresh(market_id) == True
  AND current_position(market_id) < MAX_POSITION_PER_MARKET
  AND total_exposure < MAX_TOTAL_EXPOSURE
  AND asset_exposure(asset) < MAX_ASSET_EXPOSURE
  AND not in COOLDOWN_PERIOD(market_id)
  AND circuit_breaker_not_triggered()
```

## B.2 Invalidation and Cancellation Logic

**Invalidation** (abort signal before order submission):
- External feed becomes stale during the signal evaluation window (defined as time between signal trigger and order submission; must be < `MAX_SIGNAL_AGE_MS`, e.g., 50ms)
- Polymarket best ask moves adversely by more than `MAX_SLIP_TOLERANCE` before order lands
- External composite mid changes by more than `MAX_SIGNAL_DECAY_BPS` between signal generation and order acknowledgment

**Post-submission cancellation**:
- If order is not filled within `ORDER_TIMEOUT_MS` (e.g., 500ms for aggressive taker orders), cancel and re-evaluate
- If external feed has moved adversely by `CANCEL_THRESHOLD_BPS` while order is live, cancel immediately
- If market resolution time is now within `MIN_TAU` seconds, cancel all open orders for that market

## B.3 Exit Logic

The system is NOT purely a delta-one arb. Positions are held until one of:
1. **Fair value flip**: Fair value crosses from above-entry to below-entry (or vice versa for shorts), net of fees, suggesting position is now adverse. Exit at best available bid/ask.
2. **Expiry approach**: With less than `MIN_TAU_TO_HOLD` time remaining (e.g., 3 minutes), begin position reduction to avoid binary gamma exposure near resolution.
3. **Stop-loss trigger**: Unrealized loss on position exceeds `MAX_POSITION_LOSS_BPS` (e.g., 200bps of notional). Hard exit.
4. **Inventory limit**: If position size exceeds configured limits due to fills at different price levels, trim excess.
5. **Resolution**: Market resolves. Position is closed by resolution mechanics.

## B.4 Throttling and Anti-Overtrading Logic

- Per-market cooldown: After any trade, impose `COOLDOWN_MS_PER_MARKET` (e.g., 2000ms) before re-evaluating that market.
- Global signal throttle: Maximum `MAX_SIGNALS_PER_SECOND` evaluated across all markets (e.g., 10/sec).
- Submission rate limiter: Polymarket CLOB API rate limits must be respected. Implement a token bucket with burst capacity matching API limits.
- Duplicate signal filter: If the same market triggers a signal within the last `DUPLICATE_SIGNAL_WINDOW_MS` (e.g., 100ms), discard the duplicate.

## B.5 State Transitions

```
MARKET STATE for each market_id:
  IDLE → SIGNAL_EVALUATED → ORDER_SUBMITTED → ORDER_LIVE → 
  [FILLED | PARTIALLY_FILLED | CANCELLED | REJECTED] → 
  POSITION_OPEN → [EXIT_SIGNAL | STOP_TRIGGERED | EXPIRY_APPROACH | RESOLVED] → 
  POSITION_CLOSED → IDLE
```

Partial fills create a PARTIAL_POSITION sub-state that requires explicit handling — the system must track filled quantity separately from submitted quantity and recompute edge on residual order.

---

# C. SYSTEM ARCHITECTURE

## C.1 Module Map

```
┌─────────────────────────────────────────────────────────────────┐
│                     CONFIGURATION LAYER                         │
│   ConfigLoader │ SecretsManager │ StrategyParamStore            │
└─────────────────────────────────────────────────────────────────┘
          │
┌─────────────────────────────────────────────────────────────────┐
│                    DATA INGESTION LAYER                         │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐                   │
│  │ BinanceWSConsumer│   │BybitWSConsumer   │                   │
│  └────────┬─────────┘   └────────┬─────────┘                   │
│           └─────────┬────────────┘                             │
│              ┌──────▼──────┐                                    │
│              │ FeedNormlzr │   ← composite price, staleness    │
│              └──────┬──────┘       tracking, divergence alerts │
│                     │                                           │
│  ┌──────────────────▼──────────────────┐                       │
│  │      PolymarketWSConsumer           │                       │
│  │  (order book, trade stream,         │                       │
│  │   position stream, market metadata) │                       │
│  └──────────────────┬──────────────────┘                       │
│                     │                                           │
│              ┌──────▼──────┐                                    │
│              │ BookBuilder │   ← maintains L2 snapshots        │
│              └──────┬──────┘       with sequence tracking      │
└─────────────────────┼───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                   SIGNAL ENGINE                                  │
│  FairValueEngine │ MispricingDetector │ SignalThrottler          │
│  VolatilityEstimator │ TauCalculator │ EdgeCalculator            │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                    RISK ENGINE                                   │
│  PositionLimitChecker │ ExposureEngine │ CircuitBreaker          │
│  DrawdownMonitor │ FeedHealthMonitor │ HardStopEvaluator         │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                  EXECUTION ENGINE                                │
│  OrderBuilder │ OrderSubmitter │ OrderTracker                    │
│  CancellationManager │ FillHandler │ PartialFillHandler          │
│  RateLimiter (token bucket)                                      │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                PORTFOLIO STATE ENGINE                            │
│  PositionStore │ InventoryEngine │ PnLEngine                    │
│  ExposureTracker │ CapitalAccountant │ ReconciliationEngine      │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│              OBSERVABILITY ENGINE                                │
│  MetricsCollector │ StructuredLogger │ ConsoleReporter           │
│  AlertManager │ KPIAggregator │ AnomalyDetector                 │
└─────────────────────────────────────────────────────────────────┘
```

## C.2 Concurrency Model

The system uses a single-process, multi-task async architecture based on an event loop (e.g., Python asyncio or Rust tokio). Critical latency path is:

```
WS message received → BookBuilder update → FairValueEngine re-evaluation →
MispricingDetector check → RiskEngine gate → OrderSubmitter dispatch
```

This entire hot path must be on the same event loop with no blocking I/O. All persistence (logging, DB writes) must be dispatched to background tasks and MUST NOT block the hot path.

**Thread/task boundaries**:
- WebSocket consumers: one async task per exchange (Binance, Bybit, Polymarket)
- FeedNormalizer and BookBuilder: inline on WS receive callbacks (no queue overhead)
- Signal evaluation: triggered by every relevant WS update; must complete in < `MAX_SIGNAL_EVAL_LATENCY_US` (e.g., 500 microseconds; needs benchmarking)
- Order submission: async HTTP with dedicated connection pool (no DNS re-resolution per order)
- Reconciliation: separate lower-frequency task (e.g., every 5 seconds)
- Metrics/logging: fire-and-forget async writes to ring buffer, flushed by background task

## C.3 Failure Isolation

- Each WS consumer has an independent reconnect loop with exponential backoff and max retry count.
- A WS consumer failure does NOT propagate to the execution engine; instead it transitions the system to a DEGRADED state where new signals are suppressed but existing positions are managed.
- The execution engine has its own circuit breaker that activates on consecutive order rejections.
- Disk I/O failure in the logging engine must NOT halt trading; log queue overflow is tolerated with counter increment.

## C.4 Recovery and Reconciliation

On startup (cold or warm restart):
1. Load persisted position state from DB.
2. Query Polymarket API for actual live positions.
3. Diff persisted vs actual; log all discrepancies; reconcile to actual (truth = exchange).
4. Resume monitoring and execution from reconciled state.
5. DO NOT submit new orders until reconciliation is complete.

Reconciliation runs continuously every `RECONCILE_INTERVAL_SEC` (e.g., 30 seconds) to catch state divergence from missed WS messages.

---

# D. PAPER TRADING SYSTEM DESIGN

## D.1 Design Philosophy

The paper trading system is not a simplified simulation. It is a full execution of the live strategy logic, with only the final order submission layer replaced by a simulated fill engine. Every signal evaluation, risk check, state transition, and PnL accounting operation runs identically to the live system.

**The most dangerous bias in paper trading**: Assuming your simulated orders fill at the price that triggered the signal. This is almost always wrong.

## D.2 Fill Simulation Methodology

For aggressive (taker) orders on Polymarket:

**Best-case fill assumption (optimistic; DO NOT USE AS DEFAULT)**:
Fill at best ask at time of signal trigger.

**Realistic fill assumption**:
```
simulated_fill_price = best_ask_at_signal_time 
                      + slippage_model(order_size, book_depth, tau, volatility)
                      + latency_adj(latency_estimate_p95)
```

Where:
- `slippage_model`: For a given order size, consume book levels sequentially from the best ask, compute VWAP. If order size exceeds available depth, assume inability to fill and discard the signal.
- `latency_adj`: Model the expected adverse price movement during the time between signal generation and order acknowledgment. This is a function of: (a) estimated round-trip latency to Polymarket CLOB, (b) current market volatility, (c) observed fill rate of competing orders. Initially estimate as a constant conservative value (e.g., 0.5 bps adverse per 100ms of latency); calibrate against live data.

## D.3 Queue Position Estimation

Polymarket is a CLOB (Central Limit Order Book). For passive (maker) orders, queue position matters significantly. For the latency arb strategy, orders are predominantly aggressive (taker), so queue position is less critical — but the system still models queue position for passive exits.

For passive exit orders:
- Assume worst-case queue position (back of queue at posted price level).
- Queue position degrades over time if the market moves away and back (queue resets).
- Model fill probability for passive orders as: `P(fill | price_level_crossed) = queue_position_fraction` where queue_position_fraction starts at 1.0 (end of queue) and must be estimated from observed fill data.

**NOTE**: Without actual CLOB queue visibility (which the Polymarket API does NOT provide), all queue position assumptions are estimates. This is a known simulation bias. The paper trading system MUST log a warning on every passive fill simulation, noting this assumption.

## D.4 Latency Modeling

The paper trading system does NOT use instantaneous fills. It models:
- Signal detection latency: time from WS message receipt to signal evaluation completion (measurable in paper trading; should match or be slightly better than production)
- Order submission latency: time from order builder invocation to simulated order acknowledgment. Use a latency distribution sampled from: (a) empirical measurements against Polymarket testnet/API latency, (b) initially a log-normal distribution with parameters (mean=80ms, p95=200ms, p99=500ms) — these MUST be replaced with empirical values once live latency data is available.
- Fill latency: for aggressive orders, fill is simultaneous with acknowledgment. For passive orders, fill is triggered by a simulated opposing order crossing the book.

Latency must be implemented as an actual async delay in the paper trading execution path, not a post-hoc adjustment. The signal evaluation and risk checks must run against market data state at the time of signal trigger, but the fill price must reflect the market state at simulated order acknowledgment time (signal_trigger_time + latency_sample).

## D.5 Stale Book Handling in Simulation

During replay or live paper trading, the book state used for fill simulation must be the book state at `t = signal_trigger_time + simulated_latency`, NOT at signal_trigger_time. This is critical.

Implementation: maintain a book state history ring buffer with timestamps. On fill simulation, look up the book state at `t_fill = t_signal + latency_sample`. If the buffer doesn't have a state at exactly that time, use linear interpolation of nearest snapshots. If the buffer gap is larger than `MAX_BOOK_GAP_MS` (e.g., 100ms), flag as stale book fill and mark in PnL records.

## D.6 Partial Fill Simulation

Partial fills occur when:
- Available liquidity at best ask is less than order size.
- The system models partial fills as filling only the available quantity at best ask, with the remainder considered unfilled (no sweep to next level).
- This is CONSERVATIVE relative to real execution where you could sweep multiple levels — the intent is to avoid optimistic fill assumptions.

Operationally: the paper trading system must explicitly track unfilled quantity, re-evaluate edge on the residual, and re-submit (or cancel) accordingly, exactly as the live system would.

## D.7 Avoiding Lookahead Bias

**Rule**: Any signal evaluation, edge calculation, or position management decision can only use information timestamped at or before the current simulation clock time.

**Enforcement mechanisms**:
- All data structures are indexed by receive timestamp (when the WS message arrived at the system), NOT by exchange timestamp.
- The simulation clock advances event-by-event, not wall-clock.
- Volatility estimates use only historical data up to `t_current - 1_period` (no current-period vol in the estimate).
- Resolution outcomes are explicitly hidden from the simulation until `t_resolution + epsilon`.

## D.8 Avoiding Optimistic Fill Assumptions

Documented known biases and how each is handled:

| Bias | Mitigation |
|---|---|
| Fill at signal price | Always simulate fill at price + slippage at t_fill, not t_signal |
| Instant fills | Simulate latency delay before fill; book can move adversely |
| No partial fills | Explicit partial fill modeling; residual re-evaluation |
| Ignoring book sweep cost | VWAP fill model for larger orders |
| Passive fills always at best price | Queue position degradation model |
| No fee impact | All fees applied to every simulated trade |
| No gas cost | Gas cost included in fee model |
| Unlimited liquidity | Order rejected if book depth insufficient |

## D.9 PnL Accounting

```
realized_pnl(trade) = (exit_price - entry_price) * contracts * contract_size
                     - total_fees_paid

unrealized_pnl(position) = (current_mid_price - avg_entry_price) 
                           * current_contracts * contract_size
                           - (estimated_exit_fee)

total_pnl = sum(realized_pnl, all closed trades) + sum(unrealized_pnl, all open positions)
```

Mark price for unrealized PnL: use the fair value model output, NOT the Polymarket mid price. The fair value is the system's estimate of what the position is worth; using Polymarket mid risks double-counting the very mispricing you're trying to exploit.

---

# E. LIVE PRODUCTION SYSTEM DESIGN

## E.1 Live Order Lifecycle

```
Signal Generated
       ↓
Risk Check (synchronous, must complete < 10ms)
       ↓
Order Built (order object with all parameters)
       ↓
Rate Limit Check (token bucket; block if insufficient tokens)
       ↓
HTTP POST to Polymarket CLOB API
       ↓
Await acknowledgment (with timeout: ORDER_ACK_TIMEOUT_MS, e.g., 2000ms)
       ↓
[ACK received] → update OrderTracker with exchange order_id
[Timeout]      → treat as UNKNOWN state; query order status; cancel if necessary
[Error]        → log, increment error counter, apply cooldown
       ↓
Await fill notification via WS stream
       ↓
[Fill event received] → update PositionStore; trigger PnL calculation
[Partial fill]        → update PositionStore with partial; re-evaluate residual
[Cancel ack]          → remove from OrderTracker; log
```

## E.2 Timeout and Retry Handling

The UNKNOWN order state (timeout without acknowledgment) is the most dangerous state in production. The system MUST:
1. Immediately query the REST API for order status.
2. If order found as OPEN: acknowledge it, track it normally, impose `UNKNOWN_RECOVERY_COOLDOWN_MS` before next order.
3. If order found as FILLED: process the fill; update state.
4. If order not found: assume the order was never submitted; log; proceed carefully.
5. NEVER submit a replacement order until the UNKNOWN state is fully resolved.

Retry logic: Orders are NEVER automatically retried on submission failure. Each order requires a fresh signal evaluation. This prevents double-fills and stale signal execution.

## E.3 Circuit Breakers

Defined circuit breakers (all configurable):

| Trigger | Action |
|---|---|
| 5 consecutive order rejections | HALT execution for 60 seconds; alert |
| Unrealized drawdown > X% | HALT new signals; begin position reduction |
| Total loss > daily_loss_limit | FULL HALT; require manual restart |
| Both external feeds stale > 2 seconds | HALT new signals immediately |
| Polymarket WS disconnected > 30 seconds | HALT; attempt reconnect |
| Reconciliation discrepancy > $X | HALT; require manual reconciliation |
| API rate limit hit | HALT for rate limit window; alert |
| Gas price spike > threshold | HALT until gas normalizes |

## E.4 Startup Sequence

```
1. Load configuration and secrets
2. Validate configuration completeness
3. Connect to metrics/logging infrastructure
4. Initialize all state stores to empty
5. Connect to Polymarket API; fetch all open positions and active markets
6. Connect to Binance WS; await first book snapshot
7. Connect to Bybit WS; await first book snapshot
8. Connect to Polymarket WS; await book snapshots for all target markets
9. Run reconciliation against live positions
10. Initialize PnL from reconciled positions
11. Perform self-test: emit a test signal internally; verify all engines respond correctly
12. Set system state to ACTIVE
13. Begin emitting periodic console/log summaries
14. Enable signal evaluation
```

## E.5 Shutdown Sequence

```
1. Set system state to SHUTTING_DOWN
2. Stop accepting new signals
3. Cancel all open (unfilled) orders; await cancellation confirmations (timeout: 5 seconds)
4. Record all open positions to persistent store
5. Emit final PnL and position summary to console and log
6. Flush all pending log writes
7. Close WS connections gracefully
8. Exit with status code 0
```

## E.6 Kill Switch

A kill switch is a hard, immediate halt mechanism:
- Triggered by: SIGTERM, SIGINT, manual API call, circuit breaker in PANIC mode
- On activation: skip graceful order cancellation; log final state; exit immediately
- Positions are NOT automatically closed by kill switch — operator must manually close
- This is deliberate: an automated emergency close at unfavorable prices may be worse than holding

---

# F. LATENCY ARBITRAGE STRATEGY-SPECIFIC REQUIREMENTS

## F.1 Fair Value Derivation from Binance/Bybit

**Spot vs Perpetual considerations**:
The spot price is the most direct input to the binary option fair value formula. However, Bybit's most liquid market for BTC is the perpetual futures contract. The perp trades with a basis:
```
perp_price = spot_price * (1 + funding_rate_adj + basis_adj)
```
For short-term binary markets (5-min, 15-min), the basis is small but non-trivial during funding rate extremes. The system must use spot price where available (Binance spot has excellent liquidity) and apply a basis correction when using perp prices.

**Feed selection logic**:
- Primary: Binance BTC/USDT spot (highest liquidity, tightest spread)
- Secondary: Bybit BTC/USDT spot
- Tertiary: Bybit BTC/USDT perpetual with funding-adjusted conversion
- Fallback: use stale price with age-decay uncertainty expansion (widen sigma)

**Implied probability conversion**: The critical path. Any error in this conversion creates false arbitrage signals. The system must:
1. Identify the exact oracle reference used by Polymarket for each market (e.g., is it Coinbase price? TWAP? Which exchange?)
2. Adjust the composite feed to best approximate the oracle's expected price at resolution time
3. Model the expected drift between now and resolution (typically set to zero for short durations; include risk premium if desired)

**Volatility estimate quality**: sigma has the largest impact on fair value near the strike (at-the-money). For a market where the spot is exactly at strike with 10 minutes to expiry, a 1% error in annualized vol estimate translates to approximately 0.5% error in fair value probability. This is the signal-to-noise boundary for your edge detection.

The volatility estimate must be:
- Rolling realized vol: computed from tick data, not candle data (tick data captures intraday spikes better)
- Updated every `VOL_UPDATE_INTERVAL_SEC` (e.g., 10 seconds)
- Bounded: min 20% annualized, max 300% annualized (prevents degenerate model outputs)
- Regime-aware: vol estimated separately for high-vol and low-vol regimes; regime classification updated on 1-hour intervals

## F.2 Arbitrage Window Estimation

The arbitrage window is the time between:
- T1: the external price move that causes mispricing
- T2: Polymarket market makers reprice their quotes to eliminate the mispricing

Empirical measurement required. Hypothesized range: 200ms to 5 seconds based on observed market maker behavior on comparable prediction market platforms. The system must log, for every filled signal, the estimated window duration:
```
window_duration = fill_timestamp - external_move_timestamp
```
where external_move_timestamp is when the triggering price move was first observed on the composite feed.

## F.3 Post-Fee/Post-Slippage/Post-Latency Profitability

```
gross_edge_bps = (fair_value - ask_price) * 10000   [for a buy signal]

fee_cost_bps   = taker_fee_bps + gas_cost_bps (varies; measure empirically)
slippage_bps   = expected_slippage_bps(order_size, book_depth)
latency_cost_bps = expected_adverse_move_during_latency_bps

net_edge_bps = gross_edge_bps - fee_cost_bps - slippage_bps - latency_cost_bps

TRADE ONLY IF: net_edge_bps > MIN_NET_EDGE_BPS (e.g., 30bps; calibrate empirically)
```

**Uncertain assumption**: MIN_NET_EDGE_BPS is the most important parameter and also the hardest to set correctly pre-live. Start conservatively high (e.g., 80bps), observe fill rate and PnL quality, and reduce gradually.

## F.4 False Arbitrage and Noise Filtering

**Quote stuffing / noisy quotes**: Some bots post and cancel quotes rapidly. A quote that appears stale may actually be an intentional quote-stuffing pattern. Filter: only consider a book level "stale" if it persists for more than `MIN_STALE_QUOTE_AGE_MS` (e.g., 200ms) AND is not part of a rapid post/cancel cycle within the last 2 seconds.

**Momentum traps**: A large directional price move on Binance may be the start of a momentum sequence. Lifting a Polymarket ask on a strong bullish signal and then seeing spot reverse 30 seconds later is a momentum trap. Mitigations:
- Do not enter signals when external composite mid has moved more than `MAX_MOVE_FOR_ENTRY_BPS` in the same direction within the last `MOMENTUM_LOOKBACK_SEC` (e.g., 100bps in 30 seconds).
- Use a momentum filter: enter only when the external move shows early signs of stabilization (e.g., short-term realized vol is declining).

**Feed divergence**: If Binance and Bybit spot are diverging (more than `MAX_FEED_DIVERGENCE_BPS`), the composite mid is unreliable. Suspend signal generation until feeds reconverge.

**Microburst volatility**: Sub-second price spikes (flash crashes) on Binance can trigger false arb signals on Polymarket. Implement a price move validation window: a move must persist for at least `MIN_MOVE_PERSISTENCE_MS` (e.g., 100ms) before triggering a signal.

---

# G. MARKET MAKING STRATEGY-SPECIFIC REQUIREMENTS

*(Not applicable to this strategy document — see Strategy 2)*

---

# H. DATA REQUIREMENTS

## H.1 External Feed Requirements

**Mandatory (without these, strategy cannot function)**:
- Binance BTC/USDT spot full order book via WebSocket (Level 2, top 20 levels, diff stream with snapshot synchronization)
- Bybit BTC/USDT spot Level 2 WebSocket
- Same for ETH, SOL, XRP
- Polymarket CLOB Level 2 order book for all target markets (WebSocket + REST snapshot for initial sync)
- Polymarket trade stream (filled orders) for all target markets
- Polymarket market metadata (strike, resolution time, oracle source, resolution status)

**Strongly recommended**:
- Binance BTC/USDT perpetual order book (for basis measurement)
- Funding rate stream from Bybit/Binance (for basis adjustment)
- Polymarket user position stream (for position reconciliation)

**Optional (for future ML enhancement)**:
- Historical Polymarket order book snapshots (for simulator calibration)
- Historical external feed tick data (for volatility model training)
- Polymarket resolution history (for oracle reference price validation)

## H.2 Historical Depth Requirements

**Minimum viable**:
- 30 days of Binance/Bybit tick data per asset (for volatility model initialization)
- 14 days of Polymarket order book snapshots per target market (for slippage model calibration)

**Recommended**:
- 6 months of tick data per asset
- 3 months of Polymarket book snapshots

**Critical for realism**:
- At least 100 historical resolution events per market type (5-min BTC, 15-min BTC, etc.) to understand oracle behavior and resolution price basis.

## H.3 Timestamp Requirements

Every ingested event must be triple-timestamped:
1. Exchange-generated timestamp (exchange_ts)
2. WS message receipt timestamp at the strategy system wall clock (recv_ts)
3. Signal evaluation timestamp (eval_ts)

The latency = recv_ts - exchange_ts is the measurement of feed latency. The exec latency = order_ack_ts - eval_ts is the measurement of execution latency. Both must be logged per trade.

---

# I. OBSERVABILITY, LOGGING, TELEMETRY, AND KPIs

## I.1 Console Summary (printed every 30 seconds and on every state change)

```
========================================================================
POLYMARKET LAT-ARB BOT | 2024-XX-XX HH:MM:SS UTC | MODE: [PAPER/LIVE]
========================================================================
CAPITAL:       Available: $X,XXX.XX | In Use: $X,XXX.XX | Total: $X,XXX.XX
EXPOSURE:      Total: $X,XXX.XX | BTC: $XXX | ETH: $XXX | SOL: $XX | XRP: $XX
------------------------------------------------------------------------
PnL:           Realized: +$XXX.XX | Unrealized: +$XX.XX | Total: +$XXX.XX
               Daily: +$XXX.XX | Weekly: +$XXX.XX
DRAWDOWN:      Current: -X.X% | Max (session): -X.X%
SHARPE (1H):   X.XX (rolling 60-min risk-adjusted return estimate)
------------------------------------------------------------------------
POSITIONS (X open):
  BTC >$65k @ 3PM  | Size: X contracts | Entry: 0.6123 | Current FV: 0.6441 | UPnL: +$X.XX
  ETH >$3k @ 4PM   | Size: X contracts | Entry: 0.4220 | Current FV: 0.4105 | UPnL: -$X.XX
------------------------------------------------------------------------
TRADES (session):  Total: XXX | Wins: XX (XX%) | Losses: XX (XX%)
  Win Rate by Asset: BTC: XX% | ETH: XX% | SOL: XX% | XRP: XX%
  Win Rate by TF:   5min: XX% | 15min: XX% | 1hr: XX%
AVG STATS:         Duration: XXs | Edge Captured: XX bps | Slippage: X.X bps
FILL STATS:        Fill Rate: XX% | Cancel Rate: XX% | Partial Fill Rate: XX%
ORDER BOOK:        Maker/Taker: X%/XX%
------------------------------------------------------------------------
LATENCY (ms):      Signal Eval: p50=X p95=XX | Submission: p50=XX p95=XXX
FEED HEALTH:       Binance: LIVE (Xms lag) | Bybit: LIVE (Xms lag) | Poly: LIVE
STALE QUOTES:      Last hour: X events
WS RECONNECTS:     Binance: X | Bybit: X | Polymarket: X (session)
ERRORS:            Order Rejections: X | API Errors: X | Parse Errors: X
========================================================================
```

## I.2 Structured Log Format (JSON per event)

```json
{
  "ts": "2024-01-01T12:00:00.000123Z",
  "event_type": "TRADE_FILL",
  "strategy": "LAT_ARB",
  "mode": "PAPER",
  "asset": "BTC",
  "market_id": "poly_btc_65k_1500",
  "side": "BUY",
  "contracts": 50,
  "fill_price": 0.6234,
  "fair_value_at_signal": 0.6380,
  "gross_edge_bps": 146,
  "fee_bps": 12,
  "slippage_bps": 18,
  "net_edge_bps": 116,
  "signal_ts": "2024-01-01T11:59:59.950000Z",
  "fill_ts": "2024-01-01T12:00:00.000000Z",
  "latency_ms": 50,
  "external_mid_at_signal": 65234.50,
  "composite_feed_valid": true,
  "order_id": "poly_ord_abc123",
  "session_pnl_after": 423.50
}
```

## I.3 KPIs and Monitoring Metrics

**Primary KPIs** (strategy health):
- Net PnL (realized + unrealized) — hourly, daily, session
- Win rate overall and by asset/timeframe
- Edge captured per trade (mean, p25, p75, p95)
- Sharpe ratio (rolling 24h)
- Maximum drawdown (session, rolling 7d)

**Execution quality metrics**:
- Fill rate (orders filled / orders submitted)
- Average slippage vs model prediction
- Latency distribution (p50, p95, p99 for eval, submission, fill)
- Cancel rate and cancellation reasons
- Partial fill rate

**Risk metrics**:
- Gross and net exposure by asset
- Inventory concentration
- Number of active circuit breakers
- Feed staleness events per hour

**Operational metrics**:
- WS reconnect count
- API error rate
- Reconciliation discrepancies
- Process CPU/memory usage

## I.4 Alerting

Critical alerts (immediate, pager-level):
- Circuit breaker activation
- Loss exceeds daily limit
- Any WS feed offline > 30 seconds
- Reconciliation discrepancy detected

Warning alerts (email/Slack, non-pager):
- Latency p95 exceeding threshold
- Win rate dropping below floor for sustained period
- Fill rate below expectation
- Slippage materially higher than model

---

# J. TESTING AND VALIDATION

## J.1 Unit Tests

- FairValueEngine: test against Black-Scholes reference implementations for known (S, K, tau, sigma) combinations; test boundary conditions (tau → 0, deep OTM, deep ITM)
- MispricingDetector: test signal generation with synthetic book and feed data; verify edge calculation formula; verify all invalidation conditions
- RiskEngine: test all circuit breaker conditions; test position limit logic; test exposure calculations
- FillSimulator: verify VWAP model; verify latency sampling produces correct delay distribution; verify book state lookup is using correct timestamp
- PnLEngine: verify realized/unrealized PnL formulas with known inputs

## J.2 Integration Tests

- Full signal-to-simulated-fill cycle with synthetic WS data
- Reconciliation: inject state divergence, verify system detects and resolves
- Circuit breaker activation: inject 5 consecutive rejections, verify halt behavior
- Stale feed detection: stop publishing Binance feed for 3 seconds, verify signal suppression
- Feed divergence: make Binance and Bybit diverge by 50bps, verify signal suppression
- Partial fill: simulate partial fill, verify residual handling
- Timeout recovery: simulate ACK timeout, verify UNKNOWN state handling

## J.3 Deterministic Replay Testing

Build a replay harness that:
1. Accepts a historical event log (all WS messages with recv_ts)
2. Replays events in exact recv_ts order
3. Feeds into the live strategy engine unchanged
4. Captures all generated orders, signals, and state transitions
5. Runs the same replay twice; outputs must be bit-for-bit identical

This verifies determinism. Any non-determinism (e.g., from time.now() calls inside signal logic, or random number generation) must be seeded and logged.

## J.4 Stress and Chaos Testing

- Flood test: replay 10x normal WS message rate; verify no dropped messages, latency degradation bounded
- Chaos: randomly kill/restart WS consumers during active positions; verify recovery
- Exchange outage: simulate Binance WS disconnect; verify Bybit-only fallback behavior
- Gas spike: simulate gas price 10x normal; verify order size adjustment / halt
- Memory pressure: simulate memory exhaustion; verify graceful degradation not crash

## J.5 Acceptance Criteria for Production Readiness

- Paper trading running for minimum 30 calendar days with no critical bugs
- PnL attribution explained for all significant trades (no unexplained windfalls or losses)
- All circuit breakers tested and verified in integration environment
- Latency measurements match model assumptions within 20%
- Slippage measurements match model assumptions within 30%
- Deterministic replay: 100% reproducibility confirmed
- Zero reconciliation failures over 7-day soak test

---

# K. IMPLEMENTATION ROADMAP

## Phase 0: Infrastructure Foundation (Weeks 1–2)
**Objectives**: Build data ingestion, storage, and configuration layer.
**Tasks**:
- Implement Binance/Bybit WS consumers with reconnect logic
- Implement Polymarket WS consumer (CLOB stream)
- Implement FeedNormalizer and BookBuilder
- Implement configuration layer with all parameters
- Implement structured logging
- Set up development and staging environments
**Validation**: WS data flowing, books building correctly, logs persisting
**Risk**: Polymarket CLOB API underdocumented; integration surprises expected
**Complexity**: Medium
**Deliverable**: Working data pipeline logging book state continuously

## Phase 1: Signal Engine and Fair Value (Weeks 3–5)
**Objectives**: Implement and validate the core signal generation logic.
**Tasks**:
- Implement FairValueEngine with Black-Scholes binary option model
- Implement VolatilityEstimator with rolling realized vol
- Implement MispricingDetector
- Implement EdgeCalculator with fee/slippage model
- Unit test all components against reference implementations
**Validation**: Fair value calculations match reference model; signals generated on synthetic mispricings
**Risk**: Volatility estimate quality; oracle reference identification per market
**Complexity**: High
**Deliverable**: Signal engine producing correctly computed edge estimates

## Phase 2: Paper Trading System (Weeks 6–9)
**Objectives**: Full paper trading with realistic simulation.
**Tasks**:
- Implement FillSimulator with all bias mitigations
- Implement PositionStore and PnLEngine
- Implement RiskEngine with all circuit breakers
- Connect signal engine to paper execution
- Implement console/log observability
- Begin 30-day paper trading run
**Validation**: Trades generating and logging; PnL attribution correct; all risk limits functioning
**Risk**: Simulation biases not fully eliminated; paper PnL not representative of live
**Complexity**: High
**Deliverable**: Running paper trading bot with full observability

## Phase 3: Paper Trading Validation (Weeks 10–13)
**Objectives**: Validate simulation quality; collect empirical data for model calibration.
**Tasks**:
- 30-day continuous paper trading run
- Collect and analyze: signal frequency, edge distribution, fill rate, simulated latency
- Calibrate MIN_NET_EDGE_BPS based on observed signal quality
- Run deterministic replay test suite
- Calibrate slippage model from book depth data
**Validation**: PnL distribution statistically stable; no critical bugs over 30-day run
**Risk**: Edge may not exist or may be extremely rare; calibration may reveal no viable strategy
**Complexity**: Medium
**Deliverable**: Validated paper trading results with calibrated model parameters

## Phase 4: Live Production (Weeks 14–18)
**Objectives**: Deploy live system with minimal capital, progressively scale.
**Tasks**:
- Implement live OrderSubmitter with Polymarket CLOB API
- Implement reconciliation engine
- Deploy with $500 initial capital, 1-contract max position
- Compare live PnL to paper PnL; identify discrepancies
- Gradually increase capital as confidence grows
**Validation**: Live trades within 20% of paper predictions; no runaway losses; all circuit breakers functional in live
**Risk**: Execution quality worse than simulated; actual edge much smaller than modeled
**Complexity**: Very High (production edge cases)
**Deliverable**: Live production bot with progressive capital deployment

## Phase 5: Production Hardening and Scaling (Ongoing)
- Latency optimization (connection co-location, persistent connections)
- Volatility model enhancement
- Multi-asset simultaneous trading
- Automated parameter recalibration

---

# L. DELIVERABLES

## Paper Trading Deliverables
- `lat_arb/paper/main.py` (or equivalent entry point)
- `lat_arb/paper/fill_simulator.py`
- `lat_arb/paper/pnl_engine.py`
- `lat_arb/shared/ws_consumers/` (Binance, Bybit, Polymarket consumers)
- `lat_arb/shared/signal_engine/`
- `lat_arb/shared/risk_engine/`
- `lat_arb/shared/config/config.yaml`
- `lat_arb/shared/logging/structured_logger.py`

## Production Deliverables
- `lat_arb/live/main.py`
- `lat_arb/live/order_submitter.py`
- `lat_arb/live/reconciliation.py`
- `lat_arb/live/circuit_breaker.py`
- `lat_arb/ops/startup_runbook.md`
- `lat_arb/ops/emergency_procedures.md`
- `lat_arb/ops/metrics_dashboard_config.json` (Grafana/similar)
- `lat_arb/replay/replay_harness.py`

---

# M. CRITICAL FAILURE MODES

## M.1 Hidden Failure Modes

**Oracle reference mismatch**: Polymarket resolves each market using a specific oracle reference (e.g., Coinbase spot price at 3:00 PM UTC). If your composite signal is using Binance/Bybit prices and the oracle uses Coinbase, there can be a persistent, systematic basis between your fair value and the actual resolution price. This will look like a model error in paper trading — you will keep getting the "wrong" resolution outcomes despite apparently correct signal-to-entry logic.

**Mitigation**: Identify the exact oracle for each market via the Polymarket market metadata API. If the oracle is not Binance/Bybit, your composite may need an oracle-specific adjustment.

**Gamma explosion near expiry**: For 5-minute markets with the spot price near the strike and 60 seconds to expiry, the delta of the binary option is extremely high. A 0.1% move in spot moves fair value by 5–10 percentage points. Any position entered in the last few minutes of a near-the-money 5-minute market is effectively a very high-leverage binary bet. The strategy MUST enforce MIN_TAU with zero tolerance.

**Concurrent position accumulation**: If the system enters positions in multiple markets simultaneously (BTC 5-min, BTC 15-min, ETH 1-hour all triggered by the same external move), the total directional exposure to BTC can be much larger than any individual position limit implies. The ExposureTracker MUST aggregate across all open positions by underlying asset.

**State divergence after partial fills**: If a partial fill occurs and the reconciliation between internal state and Polymarket state is imperfect, the system may believe it has a different position size than reality. Over many trades, this error compounds. Reconciliation must run frequently enough to catch these divergences before they become material.

## M.2 Hidden Simulation Biases

**Optimistic volatility estimate**: If the realized vol estimate is computed from candle data rather than tick data, it will systematically underestimate vol during microbursts. This makes simulated fair value more precise than live, giving paper trading a persistent edge that disappears in production.

**Ignoring Polymarket order book impact**: Your simulated orders do not actually move the Polymarket order book. In production, a 50-contract order can consume significant depth and move the price. Simulation underestimates this impact for larger orders.

**Artificial fill rate in paper trading**: If the fill simulator grants fills whenever theoretical conditions are met, but in production the Polymarket CLOB has queue-based priority and you are not first in queue, your actual fill rate will be lower. This is the most common cause of paper vs. live divergence.

## M.3 Hidden Operational Risks

**Gas price spikes on Polygon**: Polymarket runs on Polygon. During network congestion events (e.g., a popular NFT mint), gas prices can spike 10–100x normal, making small trades unprofitable or causing transaction failures. The system must monitor Polygon gas prices and have an automatic halt threshold.

**Polymarket CLOB API downtime**: Polymarket has had API outages. During these periods, you may have open positions you cannot exit. The system must model this as a risk scenario and have a manual procedure for position management during outages.

**Key management**: Private key compromise results in loss of all capital. Keys must be in a hardware security module or at minimum an encrypted secrets manager. Never in plaintext config files. This is an existential risk.

## M.4 Edge Decay Scenarios

- Competition increasing: More participants running similar strategies → mispricing windows shrink → net edge falls below MIN_NET_EDGE_BPS → strategy becomes unprofitable.
- Polymarket market maker upgrades: If major market makers on Polymarket upgrade their infrastructure, quote refresh rates improve, and the opportunity window disappears.
- Vol regime change: A sustained low-volatility period reduces the frequency and magnitude of external price moves, reducing signal frequency.

Monitoring for edge decay: Track rolling 30-day net edge per trade. If trailing net edge drops below (MIN_NET_EDGE_BPS * DECAY_WARNING_THRESHOLD), emit alert. If it drops below MIN_NET_EDGE_BPS, suspend strategy automatically.