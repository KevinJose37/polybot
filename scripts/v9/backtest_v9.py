"""
backtest_v9.py — Simula cómo V9 hubiera filtrado los trades históricos.

Para cada trade en el dataset:
1. Aplica el gate de V9 (lookup table) al signal_score original
2. Si el score modificado >= threshold (0.35) → V9 lo toma
3. Si el score fue BLOCKED/PENALIZED bajo threshold → V9 lo skipea

Compara:
- V2opt3 real (todos sus trades)
- V9 simulado (trades filtrados por el gate)
- ALL strategies pooled (para ver el universo completo)
"""
import json
from collections import defaultdict

# ── V9 Configuration (must match signals_v9.py) ───────────────
SIGNAL_THRESHOLD = 0.35
PENALIZE_THRESHOLD_WR = 50
PENALIZE_FACTOR = 0.6
BOOST_THRESHOLD_WR = 65
BOOST_FACTOR = 1.15
BLOCK_THRESHOLD_WR = 25
MIN_ENTRY_PRICE = 0.32
MAX_ENTRY_PRICE = 0.65
MAX_SIGNAL_SCORE = 0.80


def classify_price(price: float) -> str:
    if price < 0.30:
        return "extreme_low"
    elif price < 0.46:
        return "low"
    elif price <= 0.54:
        return "golden"
    elif price <= 0.65:
        return "high"
    else:
        return "extreme_high"


def classify_time_of_day(utc_hour: int) -> str:
    if 5 <= utc_hour < 13:
        return "overnight"
    elif 13 <= utc_hour < 17:
        return "morning"
    elif 17 <= utc_hour < 23:
        return "afternoon"
    else:
        return "evening"


def simulate_v9_gate(trade: dict, lookup: dict) -> dict:
    """
    Simulate V9's context-aware gate on a single trade.
    Returns dict with action, original/modified score, and whether V9 takes the trade.
    """
    asset = trade["asset"]
    entry_price = trade["entry_price"]
    signal_score = abs(trade.get("signal_score", 0) or 0)
    pnl = trade["pnl"]
    result = trade["result"]
    
    from datetime import datetime
    entry_time = trade.get("entry_time", "")
    try:
        dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        utc_hour = dt.hour
    except:
        utc_hour = 12  # default
    
    tod = classify_time_of_day(utc_hour)
    bucket = classify_price(entry_price)
    key = f"{tod}|{asset}|{bucket}"
    
    # ── V2opt3 filters (price band, score ceiling) ────────────
    # These are applied BEFORE the V9 gate
    passed_price_filter = MIN_ENTRY_PRICE <= entry_price <= MAX_ENTRY_PRICE
    passed_score_ceiling = signal_score <= MAX_SIGNAL_SCORE
    
    if not passed_price_filter or not passed_score_ceiling:
        return {
            "action": "FILTERED_BY_V2OPT3",
            "original_score": signal_score,
            "modified_score": signal_score,
            "v9_takes": False,
            "reason": f"price={entry_price:.2f}" if not passed_price_filter else f"score={signal_score:.2f}>cap",
            "lookup_key": key,
            "pnl": pnl,
            "result": result,
        }
    
    # ── V9 lookup gate ────────────────────────────────────────
    entry = lookup.get(key)
    
    if entry is None:
        # No data → pass through
        action = "NO_DATA"
        modified_score = signal_score
    else:
        wr = entry.get("wr", 50)
        sample = entry.get("sample_size", 0)
        is_fallback = entry.get("is_fallback", True)
        
        if wr <= BLOCK_THRESHOLD_WR and sample >= 5:
            action = "BLOCKED"
            modified_score = 0.0
        elif wr < PENALIZE_THRESHOLD_WR:
            action = "PENALIZED"
            modified_score = round(signal_score * PENALIZE_FACTOR, 4)
        elif wr >= BOOST_THRESHOLD_WR and not is_fallback:
            action = "BOOSTED"
            modified_score = round(signal_score * BOOST_FACTOR, 4)
        else:
            action = "PASS"
            modified_score = signal_score
    
    v9_takes = modified_score >= SIGNAL_THRESHOLD
    
    return {
        "action": action,
        "original_score": signal_score,
        "modified_score": modified_score,
        "v9_takes": v9_takes,
        "lookup_key": key,
        "lookup_wr": entry.get("wr") if entry else None,
        "lookup_n": entry.get("sample_size") if entry else None,
        "pnl": pnl,
        "result": result,
    }


def main():
    # Load dataset and lookup table
    with open("analysis/v9_training_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    trades = data["trades"]
    
    with open("scalper/v9_lookup_table.json", "r", encoding="utf-8") as f:
        lookup = json.load(f)
    
    print("=" * 90)
    print("  V9 BACKTEST — Historical Simulation")
    print("=" * 90)
    print(f"\n  Dataset: {len(trades)} resolved trades")
    print(f"  Lookup: {len(lookup)} context keys")
    
    # ══════════════════════════════════════════════════════════
    # SIMULATION 1: V9 applied to V2opt3's actual trades
    # (Most accurate — these are the trades V9 would see)
    # ══════════════════════════════════════════════════════════
    v2opt3_trades = [t for t in trades if t["strategy"] == "V2opt3"]
    
    print(f"\n{'='*90}")
    print(f"  SIMULATION 1: V9 Gate Applied to V2opt3 Trades ({len(v2opt3_trades)} trades)")
    print(f"{'='*90}")
    
    v9_sim = {"taken": [], "skipped": [], "blocked": [], "penalized": []}
    
    for t in v2opt3_trades:
        result = simulate_v9_gate(t, lookup)
        if result["v9_takes"]:
            v9_sim["taken"].append(t)
        else:
            v9_sim["skipped"].append(t)
            if result["action"] == "BLOCKED":
                v9_sim["blocked"].append(t)
            elif result["action"] == "PENALIZED":
                v9_sim["penalized"].append(t)
    
    v2opt3_pnl = sum(t["pnl"] for t in v2opt3_trades)
    v2opt3_wins = sum(1 for t in v2opt3_trades if t["result"] == "won")
    v2opt3_wr = round(v2opt3_wins / len(v2opt3_trades) * 100, 1) if v2opt3_trades else 0
    
    v9_taken_pnl = sum(t["pnl"] for t in v9_sim["taken"])
    v9_taken_wins = sum(1 for t in v9_sim["taken"] if t["result"] == "won")
    v9_taken_wr = round(v9_taken_wins / len(v9_sim["taken"]) * 100, 1) if v9_sim["taken"] else 0
    
    skipped_pnl = sum(t["pnl"] for t in v9_sim["skipped"])
    skipped_wins = sum(1 for t in v9_sim["skipped"] if t["result"] == "won")
    
    print(f"\n  {'Metric':<25} {'V2opt3 Real':>15} {'V9 Simulated':>15} {'Difference':>15}")
    print(f"  {'-'*25} {'-'*15} {'-'*15} {'-'*15}")
    print(f"  {'Trades':<25} {len(v2opt3_trades):>15} {len(v9_sim['taken']):>15} {len(v9_sim['taken'])-len(v2opt3_trades):>+15}")
    print(f"  {'Win Rate':<25} {v2opt3_wr:>14.1f}% {v9_taken_wr:>14.1f}% {v9_taken_wr-v2opt3_wr:>+14.1f}%")
    print(f"  {'Total P&L':<25} ${v2opt3_pnl:>+13.2f} ${v9_taken_pnl:>+13.2f} ${v9_taken_pnl-v2opt3_pnl:>+13.2f}")
    print(f"  {'ROI (on $24)':<25} {v2opt3_pnl/24*100:>+13.1f}% {v9_taken_pnl/24*100:>+13.1f}% {(v9_taken_pnl-v2opt3_pnl)/24*100:>+13.1f}%")
    
    print(f"\n  Trades V9 SKIPPED: {len(v9_sim['skipped'])}")
    print(f"    Blocked (WR<25%): {len(v9_sim['blocked'])}")
    print(f"    Penalized below threshold: {len(v9_sim['penalized'])}")
    print(f"    Skipped P&L: ${skipped_pnl:+.2f} ({skipped_wins}W/{len(v9_sim['skipped'])-skipped_wins}L)")
    
    if skipped_pnl < 0:
        print(f"    --> V9 SAVED ${abs(skipped_pnl):.2f} by skipping these losing trades!")
    else:
        print(f"    --> V9 MISSED ${skipped_pnl:.2f} in profits by being too conservative")
    
    # Detail on skipped trades
    if v9_sim["skipped"]:
        print(f"\n  Skipped trades detail:")
        for t in v9_sim["skipped"]:
            r = simulate_v9_gate(t, lookup)
            icon = "+" if t["pnl"] > 0 else "-"
            print(f"    [{icon}] {t['session']:<20} {t['asset']} {t['direction']} @{t['entry_price']:.2f} "
                  f"pnl=${t['pnl']:+.2f} | {r['action']} (key={r['lookup_key']}, WR={r.get('lookup_wr','?')}%)")
    
    # ══════════════════════════════════════════════════════════
    # SIMULATION 2: V9 Gate applied to ALL strategies
    # (Broader view — what if V9 had seen every trade?)
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*90}")
    print(f"  SIMULATION 2: V9 Gate Applied to ALL Strategies ({len(trades)} trades)")
    print(f"{'='*90}")
    
    by_strategy = defaultdict(lambda: {
        "total": 0, "wins": 0, "pnl": 0,
        "v9_taken": 0, "v9_taken_wins": 0, "v9_taken_pnl": 0,
        "v9_skipped_pnl": 0,
    })
    
    for t in trades:
        s = t["strategy"]
        by_strategy[s]["total"] += 1
        by_strategy[s]["pnl"] += t["pnl"]
        if t["result"] == "won":
            by_strategy[s]["wins"] += 1
        
        r = simulate_v9_gate(t, lookup)
        if r["v9_takes"]:
            by_strategy[s]["v9_taken"] += 1
            by_strategy[s]["v9_taken_pnl"] += t["pnl"]
            if t["result"] == "won":
                by_strategy[s]["v9_taken_wins"] += 1
        else:
            by_strategy[s]["v9_skipped_pnl"] += t["pnl"]
    
    print(f"\n  {'Strategy':<10} {'Trades':>6} {'Real PnL':>10} {'V9 Taken':>8} {'V9 PnL':>10} {'Saved':>8} {'V9 WR':>6}")
    print(f"  {'-'*10} {'-'*6} {'-'*10} {'-'*8} {'-'*10} {'-'*8} {'-'*6}")
    
    for s in sorted(by_strategy.keys()):
        d = by_strategy[s]
        saved = round(d["pnl"] - d["v9_taken_pnl"], 2)  # negative = V9 would have lost less
        v9_wr = round(d["v9_taken_wins"]/d["v9_taken"]*100,1) if d["v9_taken"]>0 else 0
        real_wr = round(d["wins"]/d["total"]*100,1)
        icon = "[+]" if d["v9_taken_pnl"] > d["pnl"] else "[-]" if d["v9_taken_pnl"] < d["pnl"] else "[=]"
        print(f"  {icon} {s:<8} {d['total']:>6} ${d['pnl']:>+8.2f} {d['v9_taken']:>8} ${d['v9_taken_pnl']:>+8.2f} ${d['v9_skipped_pnl']:>+7.2f} {v9_wr:>5.1f}%")
    
    # ══════════════════════════════════════════════════════════
    # SIMULATION 3: Per-session comparison
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*90}")
    print(f"  SIMULATION 3: V9 vs V2opt3 Per Session")
    print(f"{'='*90}")
    
    sessions = sorted(set(t["session"] for t in v2opt3_trades))
    
    print(f"\n  {'Session':<25} {'V2opt3 Trades':>13} {'V2opt3 PnL':>12} {'V9 Trades':>10} {'V9 PnL':>10} {'Delta':>8}")
    print(f"  {'-'*25} {'-'*13} {'-'*12} {'-'*10} {'-'*10} {'-'*8}")
    
    total_v2_pnl = 0
    total_v9_pnl = 0
    
    for sess in sessions:
        sess_trades = [t for t in v2opt3_trades if t["session"] == sess]
        sess_pnl = sum(t["pnl"] for t in sess_trades)
        
        v9_sess_taken = []
        for t in sess_trades:
            r = simulate_v9_gate(t, lookup)
            if r["v9_takes"]:
                v9_sess_taken.append(t)
        
        v9_sess_pnl = sum(t["pnl"] for t in v9_sess_taken)
        delta = round(v9_sess_pnl - sess_pnl, 2)
        
        total_v2_pnl += sess_pnl
        total_v9_pnl += v9_sess_pnl
        
        icon = "^" if delta > 0 else "v" if delta < 0 else "="
        print(f"  {icon} {sess:<23} {len(sess_trades):>13} ${sess_pnl:>+10.2f} {len(v9_sess_taken):>10} ${v9_sess_pnl:>+8.2f} ${delta:>+7.2f}")
    
    print(f"  {'':>25} {'-'*13} {'-'*12} {'-'*10} {'-'*10} {'-'*8}")
    delta_total = round(total_v9_pnl - total_v2_pnl, 2)
    print(f"  {'TOTAL':<25} {len(v2opt3_trades):>13} ${total_v2_pnl:>+10.2f} {sum(1 for t in v2opt3_trades if simulate_v9_gate(t, lookup)['v9_takes']):>10} ${total_v9_pnl:>+8.2f} ${delta_total:>+7.2f}")
    
    # ══════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*90}")
    print(f"  FINAL VERDICT")
    print(f"{'='*90}")
    
    if total_v9_pnl > total_v2_pnl:
        print(f"\n  V9 WINS! ${total_v9_pnl:+.2f} vs V2opt3 ${total_v2_pnl:+.2f}")
        print(f"  V9 would have made ${delta_total:+.2f} MORE than V2opt3")
        print(f"  This means the context-aware gate ADDS VALUE.")
    elif total_v9_pnl == total_v2_pnl:
        print(f"\n  DRAW. V9 ${total_v9_pnl:+.2f} = V2opt3 ${total_v2_pnl:+.2f}")
        print(f"  V9 gate didn't change any outcomes. It's neutral.")
    else:
        print(f"\n  V2opt3 WINS. V2opt3 ${total_v2_pnl:+.2f} vs V9 ${total_v9_pnl:+.2f}")
        print(f"  V9 would have LOST ${abs(delta_total):.2f} by being too conservative.")
        print(f"  The gate filtered out some winners along with the losers.")
    
    v9_roi = round(total_v9_pnl / 24 * 100, 1)
    v2_roi = round(total_v2_pnl / 24 * 100, 1)
    print(f"\n  V2opt3 cumulative ROI: {v2_roi:+.1f}%")
    print(f"  V9     cumulative ROI: {v9_roi:+.1f}%")
    print(f"  Delta:                 {v9_roi-v2_roi:+.1f}%")


if __name__ == "__main__":
    main()
