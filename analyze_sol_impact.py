"""
Analyze the impact of SOL trades across ALL bot strategies.
Shows per-asset breakdown and what each bot would look like without SOL.
"""
import json
import os
import sys
import io

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import glob

base_dir = r"c:\Users\USER\Documents\Estudio\polystudio"

# Map strategy names to their trade files
files = {
    "V2": "hft_trades_v2.json",
    "V2OPT": "hft_trades_v2opt.json",
    "V2OPT2": "hft_trades_v2opt2.json",
    "V2OPT3": "hft_trades_v2opt3.json",
    "V4": "hft_trades_v4.json",
    "V5": "hft_trades_v5.json",
    "V6": "hft_trades_v6.json",
}

# Also check archive for V1
v1_path = os.path.join(base_dir, "archive", "hft_trades.json")
if os.path.exists(v1_path):
    files["V1 (archive)"] = os.path.join("archive", "hft_trades.json")

print("=" * 100)
print("  SOL IMPACT ANALYSIS ACROSS ALL STRATEGIES")
print("=" * 100)

for strategy, filename in sorted(files.items()):
    filepath = os.path.join(base_dir, filename)
    if not os.path.exists(filepath):
        continue
    
    with open(filepath, "r") as f:
        trades = json.load(f)
    
    # Only look at resolved trades
    closed = [t for t in trades if t.get("status") in ("won", "lost", "sold")]
    
    if not closed:
        continue
    
    # Per-asset breakdown
    assets = {}
    for t in closed:
        asset = t.get("asset", "???")
        if asset not in assets:
            assets[asset] = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0, "stake_total": 0.0}
        
        assets[asset]["trades"] += 1
        assets[asset]["pnl"] += t.get("pnl", 0)
        assets[asset]["stake_total"] += t.get("stake", 1.0)
        
        status = t.get("status")
        if status == "won" or (status == "sold" and t.get("pnl", 0) > 0):
            assets[asset]["wins"] += 1
        else:
            assets[asset]["losses"] += 1
    
    # Calculate totals
    total_trades = sum(a["trades"] for a in assets.values())
    total_pnl = sum(a["pnl"] for a in assets.values())
    total_wins = sum(a["wins"] for a in assets.values())
    total_losses = sum(a["losses"] for a in assets.values())
    total_stake = sum(a["stake_total"] for a in assets.values())
    total_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
    total_roi = (total_pnl / total_stake * 100) if total_stake > 0 else 0
    
    # Without SOL
    no_sol_trades = total_trades - assets.get("SOL", {}).get("trades", 0)
    no_sol_pnl = total_pnl - assets.get("SOL", {}).get("pnl", 0)
    no_sol_wins = total_wins - assets.get("SOL", {}).get("wins", 0)
    no_sol_losses = total_losses - assets.get("SOL", {}).get("losses", 0)
    no_sol_stake = total_stake - assets.get("SOL", {}).get("stake_total", 0)
    no_sol_wr = (no_sol_wins / no_sol_trades * 100) if no_sol_trades > 0 else 0
    no_sol_roi = (no_sol_pnl / no_sol_stake * 100) if no_sol_stake > 0 else 0
    
    sol_data = assets.get("SOL", {"trades": 0, "pnl": 0, "wins": 0, "losses": 0})
    sol_wr = (sol_data["wins"] / sol_data["trades"] * 100) if sol_data["trades"] > 0 else 0
    
    print(f"\n{'─' * 100}")
    print(f"  {strategy}")
    print(f"{'─' * 100}")
    
    # Per-asset table
    print(f"  {'Asset':<8} {'Trades':>7} {'W/L':>10} {'WR%':>7} {'P&L':>10} {'P&L/Trade':>10}")
    print(f"  {'─'*8} {'─'*7} {'─'*10} {'─'*7} {'─'*10} {'─'*10}")
    
    for asset in sorted(assets.keys()):
        a = assets[asset]
        wr = (a["wins"] / a["trades"] * 100) if a["trades"] > 0 else 0
        pnl_per = a["pnl"] / a["trades"] if a["trades"] > 0 else 0
        marker = " <-- TOXIC" if asset == "SOL" and a["pnl"] < 0 else ""
        marker = " <-- BEST" if pnl_per > 0 and pnl_per == max(
            (assets[x]["pnl"] / assets[x]["trades"]) if assets[x]["trades"] > 0 else -999 
            for x in assets
        ) else marker
        print(f"  {asset:<8} {a['trades']:>7} {a['wins']}W/{a['losses']}L{' ':>2} {wr:>6.1f}% ${a['pnl']:>+8.2f} ${pnl_per:>+8.2f}{marker}")
    
    print(f"  {'─'*8} {'─'*7} {'─'*10} {'─'*7} {'─'*10} {'─'*10}")
    print(f"  {'TOTAL':<8} {total_trades:>7} {total_wins}W/{total_losses}L{' ':>2} {total_wr:>6.1f}% ${total_pnl:>+8.2f}  ROI:{total_roi:>+.1f}%")
    print(f"  {'NO SOL':<8} {no_sol_trades:>7} {no_sol_wins}W/{no_sol_losses}L{' ':>2} {no_sol_wr:>6.1f}% ${no_sol_pnl:>+8.2f}  ROI:{no_sol_roi:>+.1f}%")
    
    # Delta
    pnl_delta = no_sol_pnl - total_pnl
    wr_delta = no_sol_wr - total_wr
    roi_delta = no_sol_roi - total_roi
    
    if sol_data["trades"] > 0:
        verdict = "HELPED" if sol_data["pnl"] > 0 else "HURT"
        print(f"\n  SOL Impact: {verdict} | SOL P&L: ${sol_data['pnl']:+.2f} | SOL WR: {sol_wr:.1f}% | Removing SOL: WR {wr_delta:+.1f}pp, ROI {roi_delta:+.1f}pp")
    else:
        print(f"\n  SOL Impact: NO SOL TRADES")

print(f"\n{'=' * 100}")
print("  SUMMARY: SOL IMPACT ACROSS ALL BOTS")
print(f"{'=' * 100}")
