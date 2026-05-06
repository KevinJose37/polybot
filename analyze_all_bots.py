"""Analyze all HFT bot strategies from their trade JSON files."""
import json
import os
from collections import defaultdict

BASE = r"c:\Users\USER\Documents\Estudio\polystudio"

files = {
    "v1": "hft_trades.json",
    "v2": "hft_trades_v2.json",
    "v2opt": "hft_trades_v2opt.json",
    "v2opt2": "hft_trades_v2opt2.json",
    "v2opt3": "hft_trades_v2opt3.json",
    "v4": "hft_trades_v4.json",
    "v5": "hft_trades_v5.json",
    "v6": "hft_trades_v6.json",
}

results = {}

for strategy, fname in files.items():
    path = os.path.join(BASE, fname)
    if not os.path.exists(path):
        continue
    
    with open(path, "r") as f:
        trades = json.load(f)
    
    total = len(trades)
    if total == 0:
        results[strategy] = {"total": 0}
        continue

    closed = [t for t in trades if t.get("status") in ("won", "lost", "sold")]
    open_trades = [t for t in trades if t.get("status") == "open"]
    
    wins = [t for t in closed if t.get("status") == "won" or (t.get("status") == "sold" and (t.get("pnl", 0) or 0) > 0)]
    losses = [t for t in closed if t.get("status") == "lost" or (t.get("status") == "sold" and (t.get("pnl", 0) or 0) <= 0)]
    
    total_pnl = sum(t.get("pnl", 0) or 0 for t in closed)
    total_stake = sum(t.get("stake", 1) or 1 for t in closed)
    
    # Per-asset breakdown
    asset_pnl = defaultdict(lambda: {"pnl": 0, "wins": 0, "losses": 0, "trades": 0})
    for t in closed:
        asset = t.get("asset", "?")
        pnl = t.get("pnl", 0) or 0
        asset_pnl[asset]["pnl"] += pnl
        asset_pnl[asset]["trades"] += 1
        if pnl > 0:
            asset_pnl[asset]["wins"] += 1
        else:
            asset_pnl[asset]["losses"] += 1
    
    # Side breakdown
    side_pnl = defaultdict(lambda: {"pnl": 0, "wins": 0, "losses": 0, "trades": 0})
    for t in closed:
        side = t.get("side", "?")
        pnl = t.get("pnl", 0) or 0
        side_pnl[side]["pnl"] += pnl
        side_pnl[side]["trades"] += 1
        if pnl > 0:
            side_pnl[side]["wins"] += 1
        else:
            side_pnl[side]["losses"] += 1

    # Exit reason breakdown
    exit_reasons = defaultdict(int)
    for t in closed:
        reason = t.get("exit_reason", t.get("status", "resolution"))
        exit_reasons[reason] += 1

    # Entry price analysis
    entry_prices = [t.get("entry_price", 0) for t in closed if t.get("entry_price")]
    avg_entry = sum(entry_prices) / len(entry_prices) if entry_prices else 0

    # Winning entry prices vs losing
    win_entries = [t.get("entry_price", 0) for t in wins if t.get("entry_price")]
    loss_entries = [t.get("entry_price", 0) for t in losses if t.get("entry_price")]
    avg_win_entry = sum(win_entries) / len(win_entries) if win_entries else 0
    avg_loss_entry = sum(loss_entries) / len(loss_entries) if loss_entries else 0

    # Hindsight analysis (sell vs hold)
    hindsight_trades = [t for t in closed if t.get("hindsight")]
    bad_sells = sum(1 for t in hindsight_trades if t["hindsight"].get("decision") == "BAD")
    good_sells = sum(1 for t in hindsight_trades if t["hindsight"].get("decision") == "GOOD")
    
    hold_diff = sum(
        t["hindsight"].get("difference", 0) 
        for t in hindsight_trades
    ) if hindsight_trades else 0

    wr = len(wins) / len(closed) * 100 if closed else 0
    roi = total_pnl / total_stake * 100 if total_stake > 0 else 0

    results[strategy] = {
        "total": total,
        "closed": len(closed),
        "open": len(open_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "total_stake": round(total_stake, 2),
        "roi": round(roi, 1),
        "avg_entry_price": round(avg_entry, 3),
        "avg_win_entry": round(avg_win_entry, 3),
        "avg_loss_entry": round(avg_loss_entry, 3),
        "asset_breakdown": dict(asset_pnl),
        "side_breakdown": dict(side_pnl),
        "exit_reasons": dict(exit_reasons),
        "hindsight_good": good_sells,
        "hindsight_bad": bad_sells,
        "hindsight_hold_diff": round(hold_diff, 2),
    }

# Print summary
print("=" * 90)
print(f"{'Strategy':<10} {'Trades':<8} {'W/L':<10} {'WR%':<8} {'P&L':>8} {'ROI%':>8} {'AvgEntry':>10}")
print("=" * 90)

# Sort by P&L
sorted_strats = sorted(results.items(), key=lambda x: x[1].get("total_pnl", 0), reverse=True)

for strat, data in sorted_strats:
    if data["total"] == 0:
        print(f"{strat:<10} {'(empty)':<8}")
        continue
    if data.get("closed", 0) == 0:
        print(f"{strat:<10} {data['total']:<8} {'all open':<10}")
        continue
    
    wl = f"{data['wins']}W/{data['losses']}L"
    print(f"{strat:<10} {data['closed']:<8} {wl:<10} {data['win_rate']:<8} {data['total_pnl']:>+8.2f} {data['roi']:>+7.1f}% {data['avg_entry_price']:>10.3f}")

print("=" * 90)

# Per-strategy detail
for strat, data in sorted_strats:
    if data.get("closed", 0) == 0:
        continue
    
    print(f"\n{'=' * 50}")
    print(f"  {strat.upper()} -- Detail")
    print(f"{'=' * 50}")
    
    # Asset breakdown
    print(f"  Assets:")
    for asset, info in sorted(data["asset_breakdown"].items(), key=lambda x: x[1]["pnl"], reverse=True):
        a_wr = info['wins'] / info['trades'] * 100 if info['trades'] > 0 else 0
        print(f"    {asset:<6} {info['trades']}tr  {info['wins']}W/{info['losses']}L  WR={a_wr:.0f}%  P&L=${info['pnl']:+.2f}")
    
    # Side breakdown
    print(f"  Sides:")
    for side, info in data["side_breakdown"].items():
        s_wr = info['wins'] / info['trades'] * 100 if info['trades'] > 0 else 0
        print(f"    {side:<6} {info['trades']}tr  {info['wins']}W/{info['losses']}L  WR={s_wr:.0f}%  P&L=${info['pnl']:+.2f}")
    
    # Exit reasons
    print(f"  Exit reasons: {data['exit_reasons']}")
    
    # Entry price insight
    if data["avg_win_entry"] > 0 and data["avg_loss_entry"] > 0:
        print(f"  Avg entry: wins=${data['avg_win_entry']:.3f} vs losses=${data['avg_loss_entry']:.3f}")
    
    # Hindsight
    if data["hindsight_good"] + data["hindsight_bad"] > 0:
        print(f"  Hindsight: {data['hindsight_good']} GOOD sells, {data['hindsight_bad']} BAD sells -> net ${data['hindsight_hold_diff']:+.2f}")

    if data.get("open", 0) > 0:
        print(f"  Open positions: {data['open']}")
