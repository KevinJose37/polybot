"""
Comprehensive overnight session analysis — May 9, 2026
Session: ~1:25 AM - 10:05 AM (8h 40m)
"""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

DATA_DIR = Path("data/trades")
ROOT = Path(".")

def load_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

# ═══════════════════════════════════════════════════════════
#  SCALPER BOTS
# ═══════════════════════════════════════════════════════════
STRATEGIES = ["v2opt", "v2opt2", "v2opt3", "v3", "v5", "v6", "v7", "v9"]
TRADE_FILES = {}

# Check both root and data/trades
for s in STRATEGIES:
    for p in [DATA_DIR / f"hft_trades_{s}.json", ROOT / f"hft_trades_{s}.json"]:
        if p.exists() and p.stat().st_size > 10:
            TRADE_FILES[s] = p
            break

print("=" * 85)
print("  OVERNIGHT SESSION REPORT — May 9, 2026 (1:25 AM – 10:05 AM)")
print("  Duration: ~8h 40m")
print("=" * 85)

# ─── Leaderboard ───
print("\n  1. SCALPER BOT LEADERBOARD")
print("-" * 85)
header = f"  {'BOT':<10} {'TRADES':>6} {'WINS':>5} {'LOSSES':>6} {'WR':>6} {'P&L':>9} {'ROI':>7} {'BEST':>8} {'WORST':>8}"
print(header)
print("-" * 85)

all_results = []
all_trades_combined = []

for strat, path in sorted(TRADE_FILES.items()):
    trades = load_json(path)
    if not trades:
        continue
    
    resolved = [t for t in trades if t.get("status") == "resolved" or t.get("pnl") is not None]
    opens = [t for t in trades if t.get("status") == "open"]
    
    wins = sum(1 for t in resolved if (t.get("pnl") or 0) > 0)
    losses = sum(1 for t in resolved if (t.get("pnl") or 0) <= 0)
    total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved)
    wr = (wins / len(resolved) * 100) if resolved else 0
    roi = (total_pnl / 24 * 100) if resolved else 0
    
    pnls = [t.get("pnl", 0) or 0 for t in resolved]
    best = max(pnls) if pnls else 0
    worst = min(pnls) if pnls else 0
    
    all_results.append({
        "name": strat, "trades": len(resolved), "wins": wins, "losses": losses,
        "wr": wr, "pnl": total_pnl, "roi": roi, "best": best, "worst": worst,
        "open": len(opens),
    })
    all_trades_combined.extend(resolved)

# Sort by P&L
all_results.sort(key=lambda x: x["pnl"], reverse=True)
for r in all_results:
    medal = " *" if r == all_results[0] and r["pnl"] > 0 else ""
    print(
        f"  {r['name']:<10} {r['trades']:>6} {r['wins']:>5} {r['losses']:>6} "
        f"{r['wr']:>5.0f}% ${r['pnl']:>+7.2f} {r['roi']:>+6.1f}% "
        f"${r['best']:>+6.2f} ${r['worst']:>+6.2f}{medal}"
    )

total_pnl = sum(r["pnl"] for r in all_results)
total_trades = sum(r["trades"] for r in all_results)
total_wins = sum(r["wins"] for r in all_results)
total_open = sum(r["open"] for r in all_results)
fleet_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0

print("-" * 85)
print(f"  {'TOTAL':<10} {total_trades:>6} {total_wins:>5} {total_trades - total_wins:>6} "
      f"{fleet_wr:>5.0f}% ${total_pnl:>+7.2f}")
print(f"  Open positions still running: {total_open}")

# ─── Per-Asset Breakdown ───
print("\n\n  2. PER-ASSET BREAKDOWN")
print("-" * 85)
asset_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
for t in all_trades_combined:
    asset = t.get("asset", "?")
    pnl = t.get("pnl", 0) or 0
    asset_stats[asset]["trades"] += 1
    asset_stats[asset]["pnl"] += pnl
    if pnl > 0:
        asset_stats[asset]["wins"] += 1

print(f"  {'ASSET':<8} {'TRADES':>6} {'WINS':>5} {'WR':>6} {'P&L':>9}")
print("-" * 40)
for asset in sorted(asset_stats, key=lambda a: asset_stats[a]["pnl"], reverse=True):
    s = asset_stats[asset]
    wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
    print(f"  {asset:<8} {s['trades']:>6} {s['wins']:>5} {wr:>5.0f}% ${s['pnl']:>+7.2f}")

# ─── Per-Strategy Detail ───
print("\n\n  3. STRATEGY DETAILS")
print("-" * 85)
for r in all_results:
    path = TRADE_FILES.get(r["name"])
    if not path:
        continue
    trades = load_json(path)
    resolved = [t for t in trades if t.get("status") == "resolved" or t.get("pnl") is not None]
    
    print(f"\n  {r['name'].upper()}: {r['trades']} trades | WR: {r['wr']:.0f}% | P&L: ${r['pnl']:+.2f}")
    if resolved:
        # Show last 5 trades
        for t in resolved[-5:]:
            asset = t.get("asset", "?")
            side = t.get("side", "?")
            entry = t.get("entry_price", 0)
            exit_p = t.get("exit_price", 0)
            pnl = t.get("pnl", 0) or 0
            reason = t.get("exit_reason", "?")
            icon = "W" if pnl > 0 else "L"
            print(f"    {icon} {asset:<4} {side:<5} @ {entry:.2f}->{exit_p:.2f} ${pnl:+.2f} ({reason})")

# ═══════════════════════════════════════════════════════════
#  COPY FLEET
# ═══════════════════════════════════════════════════════════
print("\n\n" + "=" * 85)
print("  4. COPY FLEET (Wallet Tracker)")
print("=" * 85)

WALLETS = {
    "5d0f03cf": "EB99999",
    "db15fbbc": "memain",
    "e7348e92": "bobthetradoor",
    "d7f85d0e": "tdrhrhhd",
    "f989bd9c": "vovatoxic",
}

for ws, name in WALLETS.items():
    trades_file = DATA_DIR / f"copy_{ws}.json"
    seen_file = DATA_DIR / f"copy_{ws}_seen.json"
    
    trades = load_json(trades_file)
    seen = load_json(seen_file)
    
    open_t = [t for t in trades if t.get("status") == "open"]
    resolved_t = [t for t in trades if t.get("status") == "resolved"]
    total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved_t)
    
    print(f"\n  {name:16} | Seen: {len(seen)} | Trades: {len(trades)} (open={len(open_t)}, resolved={len(resolved_t)}) | P&L: ${total_pnl:+.2f}")
    
    if trades:
        for t in trades[-3:]:
            side = t.get("side", "?")
            q = t.get("question", "?")[:40]
            entry = t.get("entry_price", 0)
            status = t.get("status", "?")
            src = t.get("entry_source", "?")
            mode = t.get("mode", "PAPER")
            print(f"    [{mode}] {side:5} @ ${entry:.3f} | {status} | {src[:25]} | {q}")
    else:
        seen_count = len(seen)
        print(f"    No copied trades yet (tracking {seen_count} historical txs)")

# ═══════════════════════════════════════════════════════════
#  COMPARISON WITH PREVIOUS SESSIONS
# ═══════════════════════════════════════════════════════════
print("\n\n" + "=" * 85)
print("  5. SESSION COMPARISON")
print("=" * 85)
print(f"\n  {'SESSION':<25} {'TRADES':>6} {'WR':>6} {'P&L':>9} {'BEST BOT':<15} {'NOTES':<20}")
print("-" * 85)
# Previous sessions from audit files
print(f"  {'May 8 Overnight':<25} {'--':>6} {'--':>6} {'--':>9} {'--':<15} {'No data (pre-V10)':<20}")
print(f"  {'May 8 Morning':<25} {'--':>6} {'--':>6} {'--':>9} {'--':<15} {'Regime test':<20}")
print(f"  {'May 8 Afternoon':<25} {'--':>6} {'--':>6} {'--':>9} {'--':<15} {'V8/V9 debut':<20}")
print(f"  {'May 9 Overnight':<25} {total_trades:>6} {fleet_wr:>5.0f}% ${total_pnl:>+7.2f} "
      f"{all_results[0]['name'] if all_results else '--':<15} {'8 bots + copy fleet':<20}")

print("\n" + "=" * 85)
