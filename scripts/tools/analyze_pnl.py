"""Compare: Our TP exits vs pure ohanism (resolution only)."""
import json
from pathlib import Path

trades = json.loads(Path("data/trades/copy_89b5cdaa.json").read_text())

resolved = [t for t in trades if t.get("status") in ("won", "lost", "sold")]
open_t = [t for t in trades if t.get("status") == "open"]

# Split by exit mechanism
resolutions = [t for t in resolved if t.get("exit_reason") == "resolution"]
tp_exits = [t for t in resolved if (t.get("exit_reason") or "").startswith("TP")]

# ═══════════════════════════════════════════════════
# SCENARIO A: Our current system (TP + resolution)
# ═══════════════════════════════════════════════════
a_trades = resolutions + tp_exits
a_pnl = sum(t.get("pnl", 0) or 0 for t in a_trades)
a_wins = sum(1 for t in a_trades if (t.get("pnl", 0) or 0) > 0)
a_losses = len(a_trades) - a_wins
a_wr = a_wins / len(a_trades) * 100 if a_trades else 0

# ═══════════════════════════════════════════════════
# SCENARIO B: Pure ohanism (only resolutions, no TP)
# What would TP trades have done at resolution?
# ═══════════════════════════════════════════════════
# For TP trades: they exited early at profit.
# Without TP, they would have stayed open until $1.00 or $0.00
# We can estimate using ohanism's resolution WR

res_wr = sum(1 for t in resolutions if t.get("status") == "won") / len(resolutions) * 100 if resolutions else 0

# For each TP trade, simulate what would have happened at resolution
tp_if_won = []
tp_if_lost = []
for t in tp_exits:
    entry = t.get("entry_price", 0)
    shares = t.get("shares", 0)
    stake = t.get("stake", 0)
    # If won at resolution: exit at $1.00
    win_pnl = (1.0 - entry) * shares
    # If lost at resolution: exit at $0.00
    loss_pnl = -stake
    tp_if_won.append(win_pnl)
    tp_if_lost.append(loss_pnl)

# Estimate using actual resolution WR
import random
random.seed(42)
n_simulations = 10000
b_pnl_sims = []
for _ in range(n_simulations):
    sim_pnl = sum(t.get("pnl", 0) or 0 for t in resolutions)  # Real resolutions stay same
    for i in range(len(tp_exits)):
        if random.random() < res_wr / 100:
            sim_pnl += tp_if_won[i]
        else:
            sim_pnl += tp_if_lost[i]
    b_pnl_sims.append(sim_pnl)

b_pnl_avg = sum(b_pnl_sims) / len(b_pnl_sims)
b_pnl_median = sorted(b_pnl_sims)[len(b_pnl_sims) // 2]
b_pnl_best = max(b_pnl_sims)
b_pnl_worst = min(b_pnl_sims)

# Also calculate "best case" (all TP trades win) and "worst case" (all lose)
b_pnl_all_win = sum(t.get("pnl", 0) or 0 for t in resolutions) + sum(tp_if_won)
b_pnl_all_lose = sum(t.get("pnl", 0) or 0 for t in resolutions) + sum(tp_if_lost)

# ═══════════════════════════════════════════════════
# DETAILED TP ANALYSIS
# ═══════════════════════════════════════════════════
tp_actual_pnl = sum(t.get("pnl", 0) or 0 for t in tp_exits)
tp_avg_pnl = tp_actual_pnl / len(tp_exits) if tp_exits else 0
tp_avg_entry = sum(t.get("entry_price", 0) for t in tp_exits) / len(tp_exits) if tp_exits else 0
tp_avg_exit = sum(t.get("exit_price", 0) for t in tp_exits) / len(tp_exits) if tp_exits else 0

# What TP captured vs what resolution would give
tp_captured_per_trade = [t.get("pnl", 0) or 0 for t in tp_exits]
tp_potential_win = [(1.0 - t.get("entry_price", 0)) * t.get("shares", 0) for t in tp_exits]
tp_potential_loss = [-t.get("stake", 0) for t in tp_exits]

print("=" * 70)
print("  OHANISM: TP EXITS vs PURE RESOLUTION")
print("=" * 70)

print(f"\n  Data: {len(resolved)} resolved + {len(open_t)} open")
print(f"  Resolution WR: {res_wr:.1f}%")

print(f"\n{'─'*70}")
print(f"  SCENARIO A: Current (TP + Resolution)")
print(f"{'─'*70}")
print(f"  Trades:  {len(a_trades)} ({len(resolutions)} resolution + {len(tp_exits)} TP)")
print(f"  WR:      {a_wr:.1f}% ({a_wins}W / {a_losses}L)")
print(f"  P&L:     ${a_pnl:+.2f}")
if a_trades:
    print(f"  Avg/trade: ${a_pnl/len(a_trades):+.2f}")

print(f"\n{'─'*70}")
print(f"  SCENARIO B: Pure Resolution (no TP, hold until $1/$0)")
print(f"{'─'*70}")
print(f"  Resolution P&L (same):   ${sum(t.get('pnl',0) or 0 for t in resolutions):+.2f}")
print(f"  TP trades at resolution (Monte Carlo {n_simulations} sims, WR={res_wr:.0f}%):")
print(f"    Average P&L:   ${b_pnl_avg:+.2f}")
print(f"    Median P&L:    ${b_pnl_median:+.2f}")
print(f"    Best sim:      ${b_pnl_best:+.2f}")
print(f"    Worst sim:     ${b_pnl_worst:+.2f}")
print(f"    All TP win:    ${b_pnl_all_win:+.2f} (best possible)")
print(f"    All TP lose:   ${b_pnl_all_lose:+.2f} (worst possible)")

print(f"\n{'─'*70}")
print(f"  TP IMPACT ANALYSIS")
print(f"{'─'*70}")
print(f"  TP trades: {len(tp_exits)}")
print(f"  TP total P&L captured: ${tp_actual_pnl:+.2f}")
print(f"  TP avg per trade:      ${tp_avg_pnl:+.2f}")
print(f"  TP avg entry→exit:     ${tp_avg_entry:.3f} → ${tp_avg_exit:.3f}")
print(f"")
print(f"  If those TP trades had RESOLVED instead:")
print(f"    At {res_wr:.0f}% WR → expected: ${b_pnl_avg - sum(t.get('pnl',0) or 0 for t in resolutions):+.2f}")
print(f"    TP captured:       ${tp_actual_pnl:+.2f}")
print(f"    Difference:        ${tp_actual_pnl - (b_pnl_avg - sum(t.get('pnl',0) or 0 for t in resolutions)):+.2f}")

# Per-trade comparison
print(f"\n  Per-TP-trade analysis (captured vs potential):")
for i, t in enumerate(tp_exits[:10]):
    entry = t.get("entry_price", 0)
    exit_p = t.get("exit_price", 0)
    pnl = t.get("pnl", 0) or 0
    pot_win = tp_potential_win[i]
    pot_loss = tp_potential_loss[i]
    print(f"    #{i+1}: entry=${entry:.3f} exit=${exit_p:.3f} captured=${pnl:+.2f} | "
          f"if_won=${pot_win:+.2f} if_lost=${pot_loss:+.2f}")
if len(tp_exits) > 10:
    print(f"    ... and {len(tp_exits)-10} more")

print(f"\n{'='*70}")
print(f"  VERDICT")
print(f"{'='*70}")
diff = a_pnl - b_pnl_avg
if diff > 0:
    print(f"  TP is HELPING: +${diff:.2f} vs pure resolution")
else:
    print(f"  TP is HURTING: ${diff:.2f} vs pure resolution")
print(f"  Current:   ${a_pnl:+.2f} (with TP)")
print(f"  Expected:  ${b_pnl_avg:+.2f} (without TP, at {res_wr:.0f}% WR)")
