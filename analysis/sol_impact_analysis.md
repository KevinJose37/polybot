# SOL Impact Analysis — All Strategies

Data from 4+ hours of parallel paper trading across 8 bots.

## Per-Bot SOL Impact Summary

| Bot | SOL Trades | SOL WR | SOL P&L | Verdict | Without SOL: WR | Without SOL: ROI |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **V1** | 8 | 75.0% | +$0.81 | ✅ HELPED | 59.4% (-1.6pp) | -8.8% (-2.0pp) |
| **V2** | 19 | 57.9% | -$1.08 | ❌ HURT | 60.7% (+0.7pp) | +7.1% (+2.9pp) |
| **V2OPT** | 13 | 46.2% | **-$3.59** | ❌ HURT | 71.7% (+5.0pp) | **+22.2% (+9.4pp)** |
| **V2OPT2** | 8 | 50.0% | +$0.53 | ✅ HELPED | 56.2% (+2.1pp) | +11.2% (+1.5pp) |
| **V2OPT3** | 1 | 100% | +$1.00 | ✅ HELPED | 50.0% (-16.7pp) | -4.0% (-34.7pp) |
| **V4** | 6 | 66.7% | -$0.51 | ❌ HURT | 63.3% (-0.3pp) | +4.9% (+1.2pp) |
| **V5** | 8 | 62.5% | +$2.75 | ✅ HELPED | 53.6% (-1.1pp) | +1.7% (-4.1pp) |
| **V6** | 2 | 100% | +$0.50 | ✅ HELPED | 80.0% (-5.7pp) | +33.6% (+2.5pp) |

## Key Findings

### SOL is NOT universally toxic — it's strategy-dependent

The data tells a nuanced story:

- **SOL HURT 3 bots**: V2 (-$1.08), V2OPT (**-$3.59**), V4 (-$0.51)
- **SOL HELPED 5 bots**: V1 (+$0.81), V2OPT2 (+$0.53), V2OPT3 (+$1.00), V5 (+$2.75), V6 (+$0.50)

> [!IMPORTANT]
> SOL's impact is heavily strategy-dependent. Bots with **active exits** (TP/SL) get destroyed by SOL's volatility — the noise triggers premature sells. But bots with **hold-to-resolution** or **lenient exits** profit from SOL because its volatility creates wide entry opportunities that resolve correctly.

### The V2OPT case is devastating
V2OPT's SOL performance is the worst across all bots: **46.2% WR, -$3.59 P&L, -$0.28/trade**. Without SOL, V2OPT would have jumped from +12.8% ROI to **+22.2% ROI** — almost doubling its returns.

### But V5 tells the opposite story
V5 made **+$2.75 from SOL** at a 62.5% WR. SOL was V5's second-best asset. Without it, V5 drops from +5.8% ROI to just +1.7% ROI.

### The real insight: SOL + Hold-to-Resolution works
In V2OPT2 (hold-to-resolution), SOL was **+$0.53 positive** with a 50% WR. In V2OPT (active exits, same signal), SOL was **-$3.59**. Same signal, same market, different exit strategy → opposite result. This confirms that SOL's problem is the **exit noise**, not the **entry signal**.

## Verdict for V7

Since V7 uses `hold_to_resolution=True`, the data suggests SOL *could* be profitable. However:

1. **Sample size is small** — SOL's positive results in hold-to-resolution bots came from 8-9 trades total
2. **SOL's best P&L/trade** across all bots was only +$0.34 (V5), while BTC and ETH routinely hit +$0.20-$0.27 with much higher consistency
3. **The downside risk is real** — when SOL goes wrong (V2OPT), it goes *very* wrong (-$0.28/trade)

> [!TIP]
> The conservative play for V7 is correct: **exclude SOL for now**. If V7 proves stable with BTC/ETH/XRP, SOL can be re-added as an experiment later with its own isolated trade file for clean measurement.
