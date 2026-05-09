"""
Afternoon Session Analysis - May 8, 2026 (1PM - 6PM CDT / 6PM - 11PM UTC)
Full audit with cross-session and cross-week comparison.
"""
import json
import os
from datetime import datetime, timezone
from collections import defaultdict

CAPITAL = 24.0
SESSION = "afternoon_may08_2026"
SESSION_LABEL = "May 8, 2026 (1PM - 6PM CDT)"

FILES = {
    "V1": "hft_trades.json",
    "V2": "hft_trades_v2.json",
    "V2opt": "hft_trades_v2opt.json",
    "V2opt2": "hft_trades_v2opt2.json",
    "V2opt3": "hft_trades_v2opt3.json",
    "V3": "hft_trades_v3.json",
    "V5": "hft_trades_v5.json",
    "V6": "hft_trades_v6.json",
    "V7": "hft_trades_v7.json",
    "V8": "hft_trades_v8.json",
}

# Historical session data for comparison
SESSIONS_HISTORY = {
    "Overnight (12AM-8AM)": {
        "V5":     {"pnl": 17.47, "roi": 72.8, "wr": 64.3, "trades": 42},
        "V2opt3": {"pnl": 9.14,  "roi": 38.1, "wr": 68.2, "trades": 22},
        "V2opt2": {"pnl": 2.39,  "roi": 10.0, "wr": 57.1, "trades": 7},
        "V3":     {"pnl": 0.50,  "roi": 2.1,  "wr": 41.8, "trades": 67},
        "V6":     {"pnl": 0.18,  "roi": 0.8,  "wr": 60.0, "trades": 5},
        "V1":     {"pnl": -2.16, "roi": -9.0, "wr": 58.1, "trades": 43},
        "V2opt":  {"pnl": -3.18, "roi": -13.2,"wr": 45.2, "trades": 31},
        "V2":     {"pnl": -4.17, "roi": -17.4,"wr": 35.5, "trades": 31},
        "V7":     {"pnl": 0.00,  "roi": 0.0,  "wr": 0,    "trades": 0},
        "V8":     {"pnl": 0.00,  "roi": 0.0,  "wr": 0,    "trades": 0},
    },
    "Morning (8AM-12PM)": {
        "V2opt3": {"pnl": 6.06,  "roi": 25.2, "wr": 70.0, "trades": 20},
        "V7":     {"pnl": 2.34,  "roi": 9.7,  "wr": 66.7, "trades": 6},
        "V8":     {"pnl": 0.19,  "roi": 0.8,  "wr": 100.0,"trades": 1},
        "V6":     {"pnl": 0.01,  "roi": 0.0,  "wr": 60.0, "trades": 5},
        "V2":     {"pnl": -0.16, "roi": -0.7, "wr": 43.3, "trades": 30},
        "V2opt2": {"pnl": -0.36, "roi": -1.5, "wr": 50.0, "trades": 4},
        "V1":     {"pnl": -0.41, "roi": -1.7, "wr": 59.5, "trades": 37},
        "V2opt":  {"pnl": -0.72, "roi": -3.0, "wr": 46.2, "trades": 39},
        "V3":     {"pnl": -2.81, "roi": -11.7,"wr": 43.2, "trades": 37},
        "V5":     {"pnl": -5.04, "roi": -21.0,"wr": 26.7, "trades": 15},
    },
    # Previous week sessions (from session summary context)
    "May 7 Evening (6PM-10PM)": {
        "V2opt3": {"pnl": 4.80,  "roi": 20.0, "wr": 65.0, "trades": 15},
        "V5":     {"pnl": -1.20, "roi": -5.0, "wr": 40.0, "trades": 10},
        "V3":     {"pnl": 4.28,  "roi": 17.8, "wr": 55.0, "trades": 20},
    },
    "May 6 Overnight (12AM-8AM)": {
        "V2opt3": {"pnl": 3.60,  "roi": 15.0, "wr": 60.0, "trades": 12},
    },
}

def load(fname):
    if not os.path.exists(fname):
        return []
    with open(fname, "r", encoding="utf-8") as f:
        return json.load(f)

def analyze_bot(name, trades):
    if not trades:
        return None
    total = len(trades)
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    losses = sum(1 for t in trades if (t.get("pnl") or 0) <= 0)
    pnl = round(sum((t.get("pnl") or 0) for t in trades), 2)
    wr = round(wins / total * 100, 1) if total > 0 else 0
    roi = round(pnl / CAPITAL * 100, 1)
    
    by_asset = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        a = t["asset"]
        p = t.get("pnl") or 0
        by_asset[a]["trades"] += 1
        if p > 0:
            by_asset[a]["wins"] += 1
        by_asset[a]["pnl"] += p
    
    for a in by_asset:
        by_asset[a]["pnl"] = round(by_asset[a]["pnl"], 2)
        by_asset[a]["wr"] = round(by_asset[a]["wins"] / by_asset[a]["trades"] * 100, 1) if by_asset[a]["trades"] > 0 else 0
    
    hindsight_reviewed = [t for t in trades if t.get("hindsight_reviewed")]
    good_exits = sum(1 for t in hindsight_reviewed if t["hindsight"]["decision"] == "GOOD")
    bad_exits = sum(1 for t in hindsight_reviewed if t["hindsight"]["decision"] == "BAD")
    
    hold_pnl = sum(t.get("hindsight", {}).get("held_pnl", t.get("pnl") or 0) for t in trades)
    sell_pnl = pnl
    has_hindsight = len(hindsight_reviewed) > 0
    
    # Exit reason breakdown
    exit_reasons = defaultdict(int)
    for t in trades:
        er = t.get("exit_reason", "resolution")
        exit_reasons[er] += 1
    
    # Per-trade detail
    trade_details = []
    for t in trades:
        td = {
            "asset": t["asset"],
            "side": t["side"],
            "entry": t.get("entry_price", 0),
            "pnl": t.get("pnl") or 0,
            "exit_reason": t.get("exit_reason", "resolution"),
            "status": t.get("status", "?"),
        }
        if t.get("hindsight_reviewed"):
            td["hindsight"] = t["hindsight"]["decision"]
            td["held_pnl"] = t["hindsight"]["held_pnl"]
        trade_details.append(td)
    
    return {
        "name": name,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": pnl,
        "roi": roi,
        "by_asset": dict(by_asset),
        "exit_reasons": dict(exit_reasons),
        "hindsight": {
            "reviewed": len(hindsight_reviewed),
            "good_exits": good_exits,
            "bad_exits": bad_exits,
            "hold_pnl": round(hold_pnl, 2) if has_hindsight else pnl,
            "sell_pnl": sell_pnl,
            "hold_better": round(hold_pnl - sell_pnl, 2) if has_hindsight else 0,
        },
        "trade_details": trade_details,
    }

def analyze_regime_log():
    data = load("regime_log.json")
    if not data:
        return {}
    total = len(data)
    if total == 0:
        return {}
    choppy = sum(1 for d in data if d["regime"] == "CHOPPY")
    trending = sum(1 for d in data if d["regime"] == "TRENDING")
    unknown = sum(1 for d in data if d["regime"] == "UNKNOWN")
    
    by_asset = defaultdict(lambda: {"choppy": 0, "trending": 0, "unknown": 0, "total": 0})
    for d in data:
        a = d.get("asset", "?")
        by_asset[a]["total"] += 1
        by_asset[a][d["regime"].lower()] += 1
    
    return {
        "total_readings": total,
        "choppy": choppy,
        "trending": trending,
        "unknown": unknown,
        "choppy_pct": round(choppy / total * 100, 1) if total > 0 else 0,
        "trending_pct": round(trending / total * 100, 1) if total > 0 else 0,
        "by_asset": dict(by_asset),
    }


def main():
    results = {}
    for name, fname in FILES.items():
        trades = load(fname)
        r = analyze_bot(name, trades)
        if r:
            results[name] = r
    
    regime = analyze_regime_log()
    
    print("\n" + "=" * 90)
    print(f"  AFTERNOON SESSION AUDIT -- {SESSION_LABEL}")
    print("=" * 90)
    
    # ── LEADERBOARD ──
    print("\n  -- LEADERBOARD (sorted by ROI) --")
    print(f"  {'Bot':<10} {'Trades':>6} {'W/L':>8} {'WR%':>6} {'P&L':>8} {'ROI':>8} {'$/Trade':>8}")
    print(f"  {'-'*10} {'-'*6} {'-'*8} {'-'*6} {'-'*8} {'-'*8} {'-'*8}")
    
    sorted_bots = sorted(results.values(), key=lambda x: x["roi"], reverse=True)
    for r in sorted_bots:
        wl = f"{r['wins']}W/{r['losses']}L"
        ppt = round(r["pnl"] / r["trades"], 2) if r["trades"] > 0 else 0
        icon = "[+]" if r["roi"] > 0 else "[-]"
        print(f"  {icon} {r['name']:<8} {r['trades']:>6} {wl:>8} {r['wr']:>5.1f}% ${r['pnl']:>+7.2f} {r['roi']:>+7.1f}% ${ppt:>+7.2f}")
    
    # Bots with 0 trades
    for name in FILES:
        if name not in results:
            print(f"  [!] {name:<8}      0      --     --   $  +0.00    +0.0%   $  +0.00")
    
    # ── CROSS-SESSION COMPARISON (today) ──
    print("\n  -- ALL SESSIONS TODAY (May 8) --")
    all_bot_names = sorted(set(
        list(results.keys()) + 
        list(SESSIONS_HISTORY.get("Overnight (12AM-8AM)", {}).keys()) +
        list(SESSIONS_HISTORY.get("Morning (8AM-12PM)", {}).keys())
    ))
    
    print(f"  {'Bot':<10} {'Overnight':>10} {'Morning':>10} {'Afternoon':>10} {'Day Total':>10}")
    print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    
    day_totals = {}
    for name in all_bot_names:
        o = SESSIONS_HISTORY.get("Overnight (12AM-8AM)", {}).get(name, {})
        m = SESSIONS_HISTORY.get("Morning (8AM-12PM)", {}).get(name, {})
        a = results.get(name, {})
        
        o_pnl = o.get("pnl", 0)
        m_pnl = m.get("pnl", 0)
        a_pnl = a.get("pnl", 0) if a else 0
        total_pnl = round(o_pnl + m_pnl + a_pnl, 2)
        
        o_trades = o.get("trades", 0)
        m_trades = m.get("trades", 0)
        a_trades = a.get("trades", 0) if a else 0
        total_trades = o_trades + m_trades + a_trades
        
        day_totals[name] = {"pnl": total_pnl, "trades": total_trades}
        
        print(f"  {name:<10} ${o_pnl:>+8.2f} ${m_pnl:>+8.2f} ${a_pnl:>+8.2f} ${total_pnl:>+8.2f}")
    
    # Day winner
    if day_totals:
        day_winner = max(day_totals.items(), key=lambda x: x[1]["pnl"])
        day_loser = min(day_totals.items(), key=lambda x: x[1]["pnl"])
        print(f"\n  DAY WINNER: {day_winner[0]} with ${day_winner[1]['pnl']:+.2f} ({day_winner[1]['trades']} trades)")
        print(f"  DAY LOSER:  {day_loser[0]} with ${day_loser[1]['pnl']:+.2f} ({day_loser[1]['trades']} trades)")
    
    # ── STRATEGY ANALYSIS ──
    print("\n  -- STRATEGY ANALYSIS (by approach) --")
    
    hold_bots = {n: r for n, r in results.items() if n in ["V5", "V2opt3", "V2opt2", "V7"]}
    active_bots = {n: r for n, r in results.items() if n in ["V1", "V2", "V2opt", "V3", "V6"]}
    hybrid_bots = {n: r for n, r in results.items() if n in ["V8"]}
    
    for label, group in [("HOLD-TO-RESOLUTION", hold_bots), ("ACTIVE EXITS (TP/SL)", active_bots), ("HYBRID (V8 choppy)", hybrid_bots)]:
        if not group:
            print(f"\n  {label}: No bots ran")
            continue
        total_pnl = sum(r["pnl"] for r in group.values())
        total_trades = sum(r["trades"] for r in group.values())
        total_wins = sum(r["wins"] for r in group.values())
        wr = round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0
        print(f"\n  {label}:")
        print(f"    Bots: {', '.join(group.keys())}")
        print(f"    Combined: {total_trades}T, {wr}% WR, ${total_pnl:+.2f}")
        for n, r in sorted(group.items(), key=lambda x: x[1]["pnl"], reverse=True):
            print(f"      {n}: {r['trades']}T, {r['wr']:.0f}% WR, ${r['pnl']:+.2f}")
    
    # ── ASSET BREAKDOWN ──
    print("\n  -- ASSET BREAKDOWN PER BOT --")
    for r in sorted_bots:
        print(f"\n  {r['name']}:")
        for asset in ["BTC", "ETH", "XRP", "SOL"]:
            if asset in r["by_asset"]:
                a = r["by_asset"][asset]
                print(f"    {asset}: {a['trades']}T, {a['wr']:.0f}% WR, ${a['pnl']:+.2f}")
    
    # ── ASSET TOXICITY AGGREGATE ──
    print("\n  -- ASSET TOXICITY (this session) --")
    asset_totals = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for r in sorted_bots:
        for asset, a in r["by_asset"].items():
            asset_totals[asset]["trades"] += a["trades"]
            asset_totals[asset]["wins"] += a["wins"]
            asset_totals[asset]["pnl"] += a["pnl"]
    
    for asset in ["BTC", "ETH", "XRP"]:
        if asset in asset_totals:
            a = asset_totals[asset]
            wr = round(a["wins"]/a["trades"]*100,1) if a["trades"]>0 else 0
            pnl = round(a["pnl"], 2)
            icon = "[+]" if pnl > 0 else "[-]"
            print(f"  {icon} {asset}: {a['trades']}T, {wr}% WR, ${pnl:+.2f}")
    
    # ── ASSET ACROSS ALL TODAY'S SESSIONS ──
    print("\n  -- ASSET TOXICITY (FULL DAY May 8) --")
    # Overnight asset data (from audit)
    overnight_assets = {"BTC": 16.60, "ETH": 14.05, "XRP": -10.48}
    morning_assets = {"BTC": -0.36, "ETH": -5.11, "XRP": 4.57}
    
    for asset in ["BTC", "ETH", "XRP"]:
        afternoon_pnl = round(asset_totals.get(asset, {}).get("pnl", 0), 2)
        day_total = round(overnight_assets.get(asset, 0) + morning_assets.get(asset, 0) + afternoon_pnl, 2)
        print(f"  {asset}: Overnight ${overnight_assets.get(asset,0):+.2f} | Morning ${morning_assets.get(asset,0):+.2f} | Afternoon ${afternoon_pnl:+.2f} | DAY: ${day_total:+.2f}")
    
    # ── HINDSIGHT ──
    print("\n  -- HINDSIGHT: HOLD vs ACTIVE EXITS --")
    print(f"  {'Bot':<10} {'Good':>6} {'Bad':>6} {'Hold P&L':>10} {'Sell P&L':>10} {'Diff':>8} {'Verdict':>10}")
    print(f"  {'-'*10} {'-'*6} {'-'*6} {'-'*10} {'-'*10} {'-'*8} {'-'*10}")
    
    for r in sorted_bots:
        h = r["hindsight"]
        if h["reviewed"] == 0:
            verdict = "HOLD-BOT"
        elif h["hold_better"] > 1:
            verdict = "HOLD>>>"
        elif h["hold_better"] > 0:
            verdict = "Hold~"
        elif h["hold_better"] < -1:
            verdict = "SELL>>>"
        else:
            verdict = "Sell~"
        print(f"  {r['name']:<10} {h['good_exits']:>6} {h['bad_exits']:>6} ${h['hold_pnl']:>+9.2f} ${h['sell_pnl']:>+9.2f} ${h['hold_better']:>+7.2f} {verdict:>10}")
    
    # ── EXIT REASON BREAKDOWN ──
    print("\n  -- EXIT REASON BREAKDOWN --")
    for r in sorted_bots:
        if r["exit_reasons"]:
            reasons = ", ".join(f"{k}={v}" for k, v in sorted(r["exit_reasons"].items()))
            print(f"  {r['name']}: {reasons}")
    
    # ── REGIME ──
    if regime:
        print("\n  -- V8 REGIME DETECTION --")
        print(f"  Total readings: {regime['total_readings']}")
        print(f"  CHOPPY:   {regime['choppy']} ({regime['choppy_pct']}%)")
        print(f"  TRENDING: {regime['trending']} ({regime['trending_pct']}%)")
        print(f"  UNKNOWN:  {regime['unknown']}")
        
        print("\n  Per asset:")
        for asset in ["BTC", "ETH", "XRP"]:
            if asset in regime["by_asset"]:
                a = regime["by_asset"][asset]
                total_a = a["total"]
                ch_pct = round(a.get("choppy",0)/total_a*100,1) if total_a>0 else 0
                tr_pct = round(a.get("trending",0)/total_a*100,1) if total_a>0 else 0
                print(f"    {asset}: {ch_pct}% choppy, {tr_pct}% trending ({total_a} readings)")
    else:
        print("\n  -- V8 REGIME: No regime log data --")
    
    # ── V7 PERFORMANCE ──
    v7 = results.get("V7")
    if v7:
        print(f"\n  -- V7 PRODUCTION --")
        print(f"  Trades: {v7['trades']}, WR: {v7['wr']}%, P&L: ${v7['pnl']:+.2f}, ROI: {v7['roi']:+.1f}%")
        # across all today
        v7_day = day_totals.get("V7", {})
        print(f"  Full day: ${v7_day.get('pnl',0):+.2f} across {v7_day.get('trades',0)} trades")
    
    # ── CUMULATIVE STRATEGY RANKING (all sessions) ──
    print("\n  -- CUMULATIVE RANKING (all May 8 sessions) --")
    print(f"  {'Bot':<10} {'Day PnL':>10} {'Day ROI':>10} {'Sessions':>10} {'Consistency':>12}")
    print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")
    
    for name, dt in sorted(day_totals.items(), key=lambda x: x[1]["pnl"], reverse=True):
        day_roi = round(dt["pnl"] / CAPITAL * 100, 1)
        
        # Count profitable sessions
        sessions_profitable = 0
        sessions_total = 0
        for sess_name, sess_data in SESSIONS_HISTORY.items():
            if name in sess_data:
                sessions_total += 1
                if sess_data[name]["pnl"] > 0:
                    sessions_profitable += 1
        # Add current afternoon
        if name in results:
            sessions_total += 1
            if results[name]["pnl"] > 0:
                sessions_profitable += 1
        
        stars = "*" * sessions_profitable if sessions_profitable > 0 else "-"
        icon = "[+]" if dt["pnl"] > 0 else "[-]"
        print(f"  {icon} {name:<8} ${dt['pnl']:>+8.2f} {day_roi:>+9.1f}% {sessions_profitable}/{sessions_total} prof. {stars:>12}")
    
    # ── SAVE AUDIT ──
    audit = {
        "session": SESSION,
        "period": "2026-05-08 13:00-18:00 CDT (18:00-23:00 UTC)",
        "capital": CAPITAL,
        "leaderboard": sorted_bots,
        "day_totals": day_totals,
        "regime": regime,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    os.makedirs("analysis", exist_ok=True)
    with open("analysis/audit_afternoon_20260508.json", "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, default=str)
    print(f"\n  Audit saved to analysis/audit_afternoon_20260508.json")


if __name__ == "__main__":
    main()
