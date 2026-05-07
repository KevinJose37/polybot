"""
Full multi-strategy performance analysis for a given session.
Reads all available hft_trades_*.json files and produces a leaderboard.
"""
import json
import os
import sys
import io
from datetime import datetime

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = r"c:\Users\USER\Documents\Estudio\polystudio"

# Strategy -> trades file mapping
FILES = {
    "V1":     "hft_trades.json",
    "V2":     "hft_trades_v2.json",
    "V2OPT":  "hft_trades_v2opt.json",
    "V2OPT2": "hft_trades_v2opt2.json",
    "V2OPT3": "hft_trades_v2opt3.json",
    "V4":     "hft_trades_v4.json",
    "V5":     "hft_trades_v5.json",
    "V6":     "hft_trades_v6.json",
    "V7":     "hft_trades_v7.json",
}

ASSETS = ["BTC", "ETH", "SOL", "XRP"]

def analyze(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        trades = json.load(f)

    closed = [t for t in trades if t.get("status") in ("won", "lost", "sold")]
    open_t = [t for t in trades if t.get("status") == "open"]
    if not closed:
        return None

    wins   = sum(1 for t in closed if t.get("status") == "won" or (t.get("status") == "sold" and t.get("pnl", 0) > 0))
    losses = len(closed) - wins
    pnl    = sum(t.get("pnl", 0) for t in closed)
    stake  = sum(t.get("stake", 1.0) for t in closed)
    wr     = wins / len(closed) * 100 if closed else 0
    roi    = pnl / stake * 100 if stake else 0
    avg_entry = sum(t.get("entry_price", 0) for t in closed) / len(closed) if closed else 0

    # Per-asset
    assets = {}
    for t in closed:
        a = t.get("asset", "???")
        if a not in assets:
            assets[a] = {"w": 0, "l": 0, "pnl": 0.0}
        assets[a]["pnl"] += t.get("pnl", 0)
        if t.get("status") == "won" or (t.get("status") == "sold" and t.get("pnl", 0) > 0):
            assets[a]["w"] += 1
        else:
            assets[a]["l"] += 1

    # Time range
    times = []
    for t in closed:
        for key in ("entry_time", "exit_time"):
            v = t.get(key)
            if v:
                try:
                    times.append(datetime.fromisoformat(v.replace("Z", "+00:00")))
                except Exception:
                    pass
    time_range = f"{min(times).strftime('%H:%M')}-{max(times).strftime('%H:%M')} UTC" if times else "N/A"

    return {
        "trades": len(closed),
        "open": len(open_t),
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": pnl,
        "roi": roi,
        "stake": stake,
        "avg_entry": avg_entry,
        "assets": assets,
        "time_range": time_range,
    }

print("=" * 90)
print("  LEADERBOARD — Session 1pm-6pm CDT (2026-05-06)")
print("=" * 90)
print(f"  {'Bot':<10} {'Trades':>7} {'W/L':>10} {'WR':>7} {'P&L':>9} {'ROI':>8} {'Avg Entry':>11}")
print(f"  {'-'*10} {'-'*7} {'-'*10} {'-'*7} {'-'*9} {'-'*8} {'-'*11}")

results = {}
for name, fname in sorted(FILES.items()):
    path = os.path.join(BASE, fname)
    r = analyze(path)
    if r is None:
        continue
    results[name] = r
    wl = f"{r['wins']}W/{r['losses']}L"
    flag = " <--" if r["roi"] == max(x["roi"] for x in [analyze(os.path.join(BASE, f)) for f in FILES.values() if os.path.exists(os.path.join(BASE, f))] if x) else ""
    print(f"  {name:<10} {r['trades']:>7} {wl:>10} {r['wr']:>6.1f}% ${r['pnl']:>+7.2f} {r['roi']:>+7.1f}% ${r['avg_entry']:>9.4f}")

print()
print("=" * 90)
print("  PER-ASSET BREAKDOWN")
print("=" * 90)

for name, r in results.items():
    print(f"\n  {name}")
    print(f"  {'Asset':<6} {'W/L':>8} {'WR':>7} {'P&L':>9}")
    for asset in ASSETS:
        a = r["assets"].get(asset)
        if not a:
            continue
        total = a["w"] + a["l"]
        wr = a["w"] / total * 100 if total else 0
        print(f"  {asset:<6} {a['w']}W/{a['l']}L{' ':>2} {wr:>6.1f}% ${a['pnl']:>+7.2f}")
