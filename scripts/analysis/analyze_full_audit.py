"""
Comprehensive Strategy Performance Analysis
============================================
Analyzes all HFT strategy versions from their trade JSON files.
Generates a detailed report with per-asset breakdowns, exit method analysis,
and cross-strategy comparisons.

Run: python analyze_full_audit.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict

# All known trade files mapped to their strategy
STRATEGY_FILES = {
    "V1": "hft_trades.json",
    "V2": "hft_trades_v2.json",
    "V2OPT": "hft_trades_v2opt.json",
    "V2OPT2": "hft_trades_v2opt2.json",
    "V2OPT3": "hft_trades_v2opt3.json",
    "V4": "hft_trades_v4.json",
    "V5": "hft_trades_v5.json",
    "V6": "hft_trades_v6.json",
    "V7": "hft_trades_v7.json",
}

def load_trades_safe(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

def analyze_strategy(name, trades):
    """Analyze a single strategy's trades."""
    if not trades:
        return None

    total = len(trades)
    
    # Categorize by status
    open_trades = [t for t in trades if t.get("status") == "open"]
    won = [t for t in trades if t.get("status") == "won"]
    lost = [t for t in trades if t.get("status") == "lost"]
    sold = [t for t in trades if t.get("status") == "sold"]
    
    resolved = won + lost
    closed = resolved + sold
    
    # P&L
    total_pnl = sum(t.get("pnl", 0) or 0 for t in closed)
    total_stake = sum(t.get("stake", 0) or 0 for t in closed)
    
    # Win rate (resolved only)
    win_rate_resolved = len(won) / len(resolved) * 100 if resolved else 0
    
    # Win rate including sells (sold with pnl > 0 = win)
    sold_wins = [t for t in sold if (t.get("pnl", 0) or 0) > 0]
    sold_losses = [t for t in sold if (t.get("pnl", 0) or 0) <= 0]
    all_wins = len(won) + len(sold_wins)
    all_losses = len(lost) + len(sold_losses)
    overall_win_rate = all_wins / len(closed) * 100 if closed else 0
    
    # P&L by source
    pnl_from_resolution = sum(t.get("pnl", 0) or 0 for t in resolved)
    pnl_from_sells = sum(t.get("pnl", 0) or 0 for t in sold)
    
    # Exit reason breakdown
    exit_reasons = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in sold:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason]["count"] += 1
        exit_reasons[reason]["pnl"] += t.get("pnl", 0) or 0
    
    # Per-asset analysis
    assets = defaultdict(lambda: {
        "total": 0, "won": 0, "lost": 0, "sold": 0, "open": 0,
        "pnl": 0.0, "pnl_resolved": 0.0, "pnl_sold": 0.0,
    })
    for t in trades:
        a = t.get("asset", "?")
        assets[a]["total"] += 1
        status = t.get("status", "?")
        pnl = t.get("pnl", 0) or 0
        
        if status == "won":
            assets[a]["won"] += 1
            assets[a]["pnl"] += pnl
            assets[a]["pnl_resolved"] += pnl
        elif status == "lost":
            assets[a]["lost"] += 1
            assets[a]["pnl"] += pnl
            assets[a]["pnl_resolved"] += pnl
        elif status == "sold":
            assets[a]["sold"] += 1
            assets[a]["pnl"] += pnl
            assets[a]["pnl_sold"] += pnl
        elif status == "open":
            assets[a]["open"] += 1
    
    # Per-side analysis
    sides = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0.0})
    for t in closed:
        s = t.get("side", "?")
        sides[s]["total"] += 1
        pnl = t.get("pnl", 0) or 0
        sides[s]["pnl"] += pnl
        if pnl > 0:
            sides[s]["wins"] += 1
    
    # Hindsight analysis (if available)
    hindsight_trades = [t for t in sold if t.get("hindsight_reviewed")]
    hs_actual = sum(t.get("hindsight", {}).get("actual_pnl", 0) for t in hindsight_trades)
    hs_held = sum(t.get("hindsight", {}).get("held_pnl", 0) for t in hindsight_trades)
    hs_good = sum(1 for t in hindsight_trades if t.get("hindsight", {}).get("decision") == "GOOD")
    hs_bad = sum(1 for t in hindsight_trades if t.get("hindsight", {}).get("decision") == "BAD")
    
    # Time range
    entry_times = []
    for t in trades:
        et = t.get("entry_time", "")
        if et:
            try:
                entry_times.append(datetime.fromisoformat(et.replace("Z", "+00:00")))
            except ValueError:
                pass
    
    first_trade = min(entry_times).strftime("%Y-%m-%d %H:%M") if entry_times else "N/A"
    last_trade = max(entry_times).strftime("%Y-%m-%d %H:%M") if entry_times else "N/A"
    
    # TP with negative PnL (the bug we found)
    tp_negative = [t for t in sold if t.get("exit_reason") == "take_profit" and (t.get("pnl", 0) or 0) < 0]
    
    return {
        "name": name,
        "total": total,
        "open": len(open_trades),
        "won": len(won),
        "lost": len(lost),
        "sold": len(sold),
        "total_pnl": round(total_pnl, 2),
        "total_stake": round(total_stake, 2),
        "roi_pct": round(total_pnl / total_stake * 100, 1) if total_stake > 0 else 0,
        "win_rate_resolved": round(win_rate_resolved, 1),
        "overall_win_rate": round(overall_win_rate, 1),
        "pnl_from_resolution": round(pnl_from_resolution, 2),
        "pnl_from_sells": round(pnl_from_sells, 2),
        "exit_reasons": dict(exit_reasons),
        "assets": dict(assets),
        "sides": dict(sides),
        "hindsight": {
            "count": len(hindsight_trades),
            "actual_pnl": round(hs_actual, 2),
            "held_pnl": round(hs_held, 2),
            "difference": round(hs_actual - hs_held, 2),
            "good_calls": hs_good,
            "bad_calls": hs_bad,
        },
        "first_trade": first_trade,
        "last_trade": last_trade,
        "tp_negative_count": len(tp_negative),
        "tp_negative_pnl": round(sum(t.get("pnl", 0) or 0 for t in tp_negative), 2),
    }


def print_report(results, timestamp):
    """Print the full analysis report."""
    print("=" * 90)
    print(f"  POLYMARKET HFT STRATEGY AUDIT — {timestamp}")
    print("=" * 90)
    
    # ── Summary Table ──
    print(f"\n{'Strategy':<10} {'Trades':>6} {'W':>3} {'L':>3} {'Sold':>4} {'Open':>4} "
          f"{'PnL':>8} {'ROI%':>6} {'WR(res)':>7} {'WR(all)':>7}")
    print("-" * 90)
    
    for r in results:
        if r is None:
            continue
        print(f"{r['name']:<10} {r['total']:>6} {r['won']:>3} {r['lost']:>3} {r['sold']:>4} {r['open']:>4} "
              f"${r['total_pnl']:>+7.2f} {r['roi_pct']:>+5.1f}% "
              f"{r['win_rate_resolved']:>5.1f}% {r['overall_win_rate']:>5.1f}%")
    
    print("-" * 90)
    total_pnl = sum(r["total_pnl"] for r in results if r)
    total_trades = sum(r["total"] for r in results if r)
    print(f"{'TOTAL':<10} {total_trades:>6} {'':>3} {'':>3} {'':>4} {'':>4} "
          f"${total_pnl:>+7.2f}")
    
    # ── Per-strategy details ──
    for r in results:
        if r is None:
            continue
        
        print(f"\n{'=' * 90}")
        print(f"  {r['name']} — Period: {r['first_trade']} to {r['last_trade']}")
        print(f"{'=' * 90}")
        
        # P&L Sources
        print(f"\n  P&L Breakdown:")
        print(f"    From Resolution (hold):  ${r['pnl_from_resolution']:>+7.2f}")
        print(f"    From Early Sells:        ${r['pnl_from_sells']:>+7.2f}")
        print(f"    Total:                   ${r['total_pnl']:>+7.2f}")
        
        # Exit reasons
        if r["exit_reasons"]:
            print(f"\n  Exit Methods:")
            for reason, data in sorted(r["exit_reasons"].items()):
                avg = data["pnl"] / data["count"] if data["count"] > 0 else 0
                print(f"    {reason:<18} {data['count']:>3} trades  "
                      f"P&L ${data['pnl']:>+7.2f}  (avg ${avg:>+.2f})")
        
        # TP Bug indicator
        if r["tp_negative_count"] > 0:
            print(f"\n  [BUG] TP with Negative PnL: {r['tp_negative_count']} trades, "
                  f"${r['tp_negative_pnl']:>+.2f} lost")
        
        # Per-asset
        print(f"\n  Per-Asset Performance:")
        print(f"    {'Asset':<6} {'Total':>5} {'W':>3} {'L':>3} {'Sold':>4} {'Open':>4} "
              f"{'PnL':>8} {'PnL(res)':>9} {'PnL(sell)':>9}")
        print(f"    {'-' * 72}")
        for asset in sorted(r["assets"].keys()):
            a = r["assets"][asset]
            wr = a["won"] / (a["won"] + a["lost"]) * 100 if (a["won"] + a["lost"]) > 0 else 0
            print(f"    {asset:<6} {a['total']:>5} {a['won']:>3} {a['lost']:>3} {a['sold']:>4} {a['open']:>4} "
                  f"${a['pnl']:>+7.2f} ${a['pnl_resolved']:>+8.2f} ${a['pnl_sold']:>+8.2f}")
        
        # Per-side
        print(f"\n  Per-Side Performance:")
        for side, data in sorted(r["sides"].items()):
            wr = data["wins"] / data["total"] * 100 if data["total"] > 0 else 0
            print(f"    {side:<6} {data['total']:>3} trades  "
                  f"P&L ${data['pnl']:>+7.2f}  WR {wr:.0f}%")
        
        # Hindsight
        if r["hindsight"]["count"] > 0:
            hs = r["hindsight"]
            print(f"\n  Hindsight (Sell vs Hold):")
            print(f"    Analyzed:      {hs['count']} sold trades")
            print(f"    P&L selling:   ${hs['actual_pnl']:>+7.2f}")
            print(f"    P&L if held:   ${hs['held_pnl']:>+7.2f}")
            print(f"    Difference:    ${hs['difference']:>+7.2f} "
                  f"({'SELL better' if hs['difference'] > 0 else 'HOLD better'})")
            accuracy = hs["good_calls"] / (hs["good_calls"] + hs["bad_calls"]) * 100 if (hs["good_calls"] + hs["bad_calls"]) > 0 else 0
            print(f"    Sell accuracy: {accuracy:.0f}% ({hs['good_calls']} good / {hs['bad_calls']} bad)")


def save_report(results, timestamp):
    """Save the analysis to a JSON file for future comparison."""
    report = {
        "timestamp": timestamp,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategies": {},
    }
    for r in results:
        if r is None:
            continue
        report["strategies"][r["name"]] = r
    
    os.makedirs("analysis", exist_ok=True)
    filename = f"analysis/audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    # Also save as "latest" for easy access
    latest = "analysis/audit_latest.json"
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    return filename


def load_previous_audit():
    """Load the most recent previous audit for comparison."""
    latest = "analysis/audit_latest.json"
    if os.path.exists(latest):
        try:
            with open(latest, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def print_comparison(current_results, previous):
    """Print comparison with previous audit."""
    if not previous:
        print("\n  (No previous audit found for comparison)")
        return
    
    print(f"\n{'=' * 90}")
    print(f"  COMPARISON vs Previous Audit ({previous.get('timestamp', '?')})")
    print(f"{'=' * 90}")
    
    prev_strats = previous.get("strategies", {})
    
    print(f"\n{'Strategy':<10} {'Prev PnL':>9} {'Curr PnL':>9} {'Delta':>8} "
          f"{'Prev WR':>7} {'Curr WR':>7} {'Prev Tr':>7} {'Curr Tr':>7}")
    print("-" * 90)
    
    for r in current_results:
        if r is None:
            continue
        prev = prev_strats.get(r["name"], {})
        prev_pnl = prev.get("total_pnl", 0)
        prev_wr = prev.get("win_rate_resolved", 0)
        prev_total = prev.get("total", 0)
        
        delta_pnl = r["total_pnl"] - prev_pnl
        new_trades = r["total"] - prev_total
        
        print(f"{r['name']:<10} ${prev_pnl:>+7.2f} ${r['total_pnl']:>+7.2f} ${delta_pnl:>+6.2f} "
              f"{prev_wr:>5.1f}% {r['win_rate_resolved']:>5.1f}% "
              f"{prev_total:>6} {r['total']:>6} (+{new_trades})")


def main():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Load previous for comparison
    previous = load_previous_audit()
    
    # Analyze all strategies
    results = []
    for name, filepath in STRATEGY_FILES.items():
        trades = load_trades_safe(filepath)
        result = analyze_strategy(name, trades)
        results.append(result)
    
    # Filter None results
    valid_results = [r for r in results if r is not None]
    
    if not valid_results:
        print("No trade files found!")
        return
    
    # Print report
    print_report(valid_results, timestamp)
    
    # Comparison with previous
    print_comparison(valid_results, previous)
    
    # Save for future
    filename = save_report(valid_results, timestamp)
    print(f"\n{'=' * 90}")
    print(f"  Report saved to: {filename}")
    print(f"  Latest snapshot: analysis/audit_latest.json")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    main()
