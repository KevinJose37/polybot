"""
optimize_v9.py — Grid search for optimal V9 parameters.

Tests hundreds of parameter combinations against historical V2opt3 trades
to find the best configuration that maximizes P&L while improving WR.
"""
import json
from itertools import product
from collections import defaultdict


def classify_price(price):
    if price < 0.30: return "extreme_low"
    elif price < 0.46: return "low"
    elif price <= 0.54: return "golden"
    elif price <= 0.65: return "high"
    else: return "extreme_high"

def classify_time_of_day(utc_hour):
    if 5 <= utc_hour < 13: return "overnight"
    elif 13 <= utc_hour < 17: return "morning"
    elif 17 <= utc_hour < 23: return "afternoon"
    else: return "evening"


def simulate(trades, lookup, params):
    """Run one simulation with given params. Returns stats dict."""
    pen_factor = params["pen_factor"]
    pen_wr = params["pen_wr_threshold"]
    block_wr = params["block_wr_threshold"]
    boost_factor = params["boost_factor"]
    boost_wr = params["boost_wr_threshold"]
    sig_threshold = params["signal_threshold"]
    min_price = params["min_entry_price"]
    max_price = params["max_entry_price"]
    max_score = params["max_signal_score"]
    
    taken = []
    skipped = []
    
    for t in trades:
        entry_price = t["entry_price"]
        signal_score = abs(t.get("signal_score", 0) or 0)
        
        # Price/score filters (V2opt3 base)
        if not (min_price <= entry_price <= max_price):
            skipped.append(t)
            continue
        if signal_score > max_score:
            skipped.append(t)
            continue
        
        # Context lookup
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(t["entry_time"].replace("Z", "+00:00"))
            utc_hour = dt.hour
        except:
            utc_hour = 12
        
        tod = classify_time_of_day(utc_hour)
        bucket = classify_price(entry_price)
        key = f"{tod}|{t['asset']}|{bucket}"
        entry = lookup.get(key)
        
        modified_score = signal_score
        
        if entry:
            wr = entry.get("wr", 50)
            sample = entry.get("sample_size", 0)
            is_fallback = entry.get("is_fallback", True)
            
            if wr <= block_wr and sample >= 5:
                modified_score = 0.0
            elif wr < pen_wr:
                modified_score = signal_score * pen_factor
            elif wr >= boost_wr and not is_fallback:
                modified_score = signal_score * boost_factor
        
        if modified_score >= sig_threshold:
            taken.append(t)
        else:
            skipped.append(t)
    
    n = len(taken)
    wins = sum(1 for t in taken if t["result"] == "won")
    pnl = sum(t["pnl"] for t in taken)
    wr = round(wins / n * 100, 1) if n > 0 else 0
    
    skip_pnl = sum(t["pnl"] for t in skipped)
    skip_wins = sum(1 for t in skipped if t["result"] == "won")
    
    return {
        "trades": n,
        "wins": wins,
        "wr": wr,
        "pnl": round(pnl, 2),
        "roi": round(pnl / 24 * 100, 1),
        "skipped": len(skipped),
        "skipped_pnl": round(skip_pnl, 2),
        "params": params,
    }


def main():
    # Load data
    with open("analysis/v9_training_data.json", "r") as f:
        data = json.load(f)
    with open("scalper/v9_lookup_table.json", "r") as f:
        lookup = json.load(f)
    
    all_trades = data["trades"]
    v2opt3_trades = [t for t in all_trades if t["strategy"] == "V2opt3"]
    
    # V2opt3 baseline
    v2opt3_pnl = sum(t["pnl"] for t in v2opt3_trades)
    v2opt3_wins = sum(1 for t in v2opt3_trades if t["result"] == "won")
    v2opt3_wr = round(v2opt3_wins / len(v2opt3_trades) * 100, 1)
    
    print("=" * 95)
    print("  V9 PARAMETER OPTIMIZER — Grid Search")
    print("=" * 95)
    print(f"\n  V2opt3 Baseline: {len(v2opt3_trades)}T, WR={v2opt3_wr}%, P&L=${v2opt3_pnl:+.2f}, ROI={v2opt3_pnl/24*100:+.1f}%")
    
    # ── Parameter Grid ────────────────────────────────────────
    grid = {
        "pen_factor":       [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
        "pen_wr_threshold": [30, 35, 40, 45, 50],
        "block_wr_threshold": [10, 15, 20, 25],
        "boost_factor":     [1.0, 1.10, 1.15, 1.20],
        "boost_wr_threshold": [55, 60, 65, 70],
        # Fixed (V2opt3 defaults)
        "signal_threshold": [0.35],
        "min_entry_price":  [0.32],
        "max_entry_price":  [0.65],
        "max_signal_score": [0.80],
    }
    
    keys = list(grid.keys())
    combos = list(product(*[grid[k] for k in keys]))
    print(f"  Testing {len(combos)} parameter combinations...\n")
    
    results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        r = simulate(v2opt3_trades, lookup, params)
        results.append(r)
    
    # ── Sort by P&L (primary), then WR (secondary) ───────────
    results.sort(key=lambda x: (x["pnl"], x["wr"]), reverse=True)
    
    # ── Top 20 by P&L ────────────────────────────────────────
    print(f"  TOP 20 CONFIGURATIONS BY P&L")
    print(f"  {'#':>3} {'Trades':>6} {'WR':>6} {'P&L':>8} {'ROI':>7} {'PenF':>5} {'PenWR':>5} {'BlkWR':>5} {'BstF':>5} {'BstWR':>5} {'Skip':>5}")
    print(f"  {'-'*3} {'-'*6} {'-'*6} {'-'*8} {'-'*7} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*5}")
    
    for i, r in enumerate(results[:20]):
        p = r["params"]
        star = " <<<" if r["pnl"] > v2opt3_pnl else ""
        print(f"  {i+1:>3} {r['trades']:>6} {r['wr']:>5.1f}% ${r['pnl']:>+6.2f} {r['roi']:>+6.1f}% "
              f"{p['pen_factor']:>5.1f} {p['pen_wr_threshold']:>5} {p['block_wr_threshold']:>5} "
              f"{p['boost_factor']:>5.2f} {p['boost_wr_threshold']:>5} {r['skipped']:>5}{star}")
    
    # ── Best that BEATS V2opt3 ────────────────────────────────
    beats = [r for r in results if r["pnl"] > v2opt3_pnl]
    if beats:
        print(f"\n  CONFIGURATIONS THAT BEAT V2opt3 (${v2opt3_pnl:+.2f}): {len(beats)}")
        for i, r in enumerate(beats[:10]):
            p = r["params"]
            delta = r["pnl"] - v2opt3_pnl
            print(f"    #{i+1}: {r['trades']}T WR={r['wr']}% P&L=${r['pnl']:+.2f} (+${delta:.2f}) "
                  f"| pen={p['pen_factor']}/{p['pen_wr_threshold']} blk={p['block_wr_threshold']} "
                  f"bst={p['boost_factor']}/{p['boost_wr_threshold']}")
    else:
        print(f"\n  NO configuration beats V2opt3 in raw P&L.")
        print(f"  But here are configs with HIGHER WR (more consistent):")
        high_wr = [r for r in results if r["wr"] > v2opt3_wr and r["pnl"] > 0]
        high_wr.sort(key=lambda x: (x["pnl"], x["wr"]), reverse=True)
        for i, r in enumerate(high_wr[:10]):
            p = r["params"]
            print(f"    #{i+1}: {r['trades']}T WR={r['wr']}% P&L=${r['pnl']:+.2f} "
                  f"| pen={p['pen_factor']}/{p['pen_wr_threshold']} blk={p['block_wr_threshold']} "
                  f"bst={p['boost_factor']}/{p['boost_wr_threshold']}")
    
    # ── Best Sharpe-like (WR × avg_pnl) ──────────────────────
    print(f"\n  TOP 10 BY EFFICIENCY (WR × avg_pnl, min 30 trades)")
    efficient = [r for r in results if r["trades"] >= 30]
    efficient.sort(key=lambda x: x["wr"] * (x["pnl"]/max(x["trades"],1)), reverse=True)
    for i, r in enumerate(efficient[:10]):
        p = r["params"]
        eff = round(r["wr"] * r["pnl"] / max(r["trades"], 1), 4)
        print(f"    #{i+1}: {r['trades']}T WR={r['wr']}% P&L=${r['pnl']:+.2f} Eff={eff:.3f} "
              f"| pen={p['pen_factor']}/{p['pen_wr_threshold']} blk={p['block_wr_threshold']} "
              f"bst={p['boost_factor']}/{p['boost_wr_threshold']}")
    
    # ── Per-session validation for top 3 ──────────────────────
    print(f"\n  PER-SESSION BREAKDOWN FOR TOP 3:")
    sessions = sorted(set(t["session"] for t in v2opt3_trades))
    
    for rank, r in enumerate(results[:3]):
        p = r["params"]
        print(f"\n  === Config #{rank+1}: pen={p['pen_factor']}/{p['pen_wr_threshold']} blk={p['block_wr_threshold']} bst={p['boost_factor']}/{p['boost_wr_threshold']} ===")
        print(f"  {'Session':<25} {'V2opt3':>12} {'V9':>12} {'Delta':>8}")
        print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*8}")
        
        for sess in sessions:
            sess_trades = [t for t in v2opt3_trades if t["session"] == sess]
            v2_pnl = sum(t["pnl"] for t in sess_trades)
            
            v9_result = simulate(sess_trades, lookup, p)
            delta = round(v9_result["pnl"] - v2_pnl, 2)
            icon = "^" if delta > 0 else "v" if delta < 0 else "="
            print(f"  {icon} {sess:<23} ${v2_pnl:>+10.2f} ${v9_result['pnl']:>+10.2f} ${delta:>+7.2f}")
    
    # ── WINNER ────────────────────────────────────────────────
    best = results[0]
    bp = best["params"]
    print(f"\n{'='*95}")
    print(f"  RECOMMENDED CONFIGURATION")
    print(f"{'='*95}")
    print(f"  PENALIZE_FACTOR       = {bp['pen_factor']}")
    print(f"  PENALIZE_THRESHOLD_WR = {bp['pen_wr_threshold']}")
    print(f"  BLOCK_THRESHOLD_WR    = {bp['block_wr_threshold']}")
    print(f"  BOOST_FACTOR          = {bp['boost_factor']}")
    print(f"  BOOST_THRESHOLD_WR    = {bp['boost_wr_threshold']}")
    print(f"\n  Expected: {best['trades']}T, WR={best['wr']}%, P&L=${best['pnl']:+.2f}, ROI={best['roi']:+.1f}%")
    print(f"  vs V2opt3: {len(v2opt3_trades)}T, WR={v2opt3_wr}%, P&L=${v2opt3_pnl:+.2f}, ROI={v2opt3_pnl/24*100:+.1f}%")
    delta = best["pnl"] - v2opt3_pnl
    print(f"  Delta P&L: ${delta:+.2f}")


if __name__ == "__main__":
    main()
