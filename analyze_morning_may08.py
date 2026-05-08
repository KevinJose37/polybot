"""
Morning Session Analysis - May 8, 2026 (8AM - 12PM CDT / 1PM - 5PM UTC)
Comprehensive audit of all bot strategies.
"""
import json
import os
from datetime import datetime, timezone
from collections import defaultdict

CAPITAL = 24.0
SESSION = "morning_may08_2026"
SESSION_LABEL = "May 8, 2026 (8AM - 12PM CDT)"

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

# Previous session results for comparison
PREV_SESSION = {
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
    
    return {
        "name": name,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": pnl,
        "roi": roi,
        "by_asset": dict(by_asset),
        "hindsight": {
            "reviewed": len(hindsight_reviewed),
            "good_exits": good_exits,
            "bad_exits": bad_exits,
            "hold_pnl": round(hold_pnl, 2) if has_hindsight else pnl,
            "sell_pnl": sell_pnl,
            "hold_better": round(hold_pnl - sell_pnl, 2) if has_hindsight else 0,
        }
    }

def analyze_regime_log():
    data = load("regime_log.json")
    if not data:
        return {}
    total = len(data)
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
    print(f"  MORNING SESSION AUDIT -- {SESSION_LABEL}")
    print("=" * 90)
    
    # LEADERBOARD
    print("\n  -- LEADERBOARD (sorted by ROI) --")
    print(f"  {'Bot':<10} {'Trades':>6} {'W/L':>8} {'WR%':>6} {'P&L':>8} {'ROI':>8} {'$/Trade':>8}")
    print(f"  {'-'*10} {'-'*6} {'-'*8} {'-'*6} {'-'*8} {'-'*8} {'-'*8}")
    
    sorted_bots = sorted(results.values(), key=lambda x: x["roi"], reverse=True)
    for r in sorted_bots:
        wl = f"{r['wins']}W/{r['losses']}L"
        ppt = round(r["pnl"] / r["trades"], 2) if r["trades"] > 0 else 0
        icon = "[+]" if r["roi"] > 0 else "[-]"
        print(f"  {icon} {r['name']:<8} {r['trades']:>6} {wl:>8} {r['wr']:>5.1f}% ${r['pnl']:>+7.2f} {r['roi']:>+7.1f}% ${ppt:>+7.2f}")
    
    # Bots that didn't trade
    for name in FILES:
        if name not in results:
            print(f"  [!] {name:<8}      0      --     --   $  +0.00    +0.0%   $  +0.00")
    
    # VS PREVIOUS SESSION
    print("\n  -- VS OVERNIGHT SESSION (12AM-8AM) --")
    print(f"  {'Bot':<10} {'Morning ROI':>12} {'Overnight ROI':>14} {'Delta':>8} {'Trend':>8}")
    print(f"  {'-'*10} {'-'*12} {'-'*14} {'-'*8} {'-'*8}")
    
    all_names = set(list(results.keys()) + list(PREV_SESSION.keys()))
    for name in sorted(all_names):
        curr = results.get(name)
        prev = PREV_SESSION.get(name, {})
        m_roi = curr["roi"] if curr else 0
        o_roi = prev.get("roi", 0)
        delta = round(m_roi - o_roi, 1)
        trend = "UP" if delta > 0 else ("DOWN" if delta < 0 else "FLAT")
        icon = "^" if delta > 0 else ("v" if delta < 0 else "=")
        print(f"  {icon} {name:<8} {m_roi:>+11.1f}% {o_roi:>+13.1f}% {delta:>+7.1f}% {trend:>8}")
    
    # ASSET BREAKDOWN
    print("\n  -- ASSET BREAKDOWN PER BOT --")
    for r in sorted_bots:
        print(f"\n  {r['name']}:")
        for asset in ["BTC", "ETH", "XRP", "SOL"]:
            if asset in r["by_asset"]:
                a = r["by_asset"][asset]
                print(f"    {asset}: {a['trades']}T, {a['wr']:.0f}% WR, ${a['pnl']:+.2f}")
    
    # HINDSIGHT
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
    
    # REGIME
    if regime:
        print("\n  -- V8 REGIME DETECTION --")
        print(f"  Total readings: {regime['total_readings']}")
        print(f"  CHOPPY:   {regime['choppy']} ({regime['choppy_pct']}%)")
        print(f"  TRENDING: {regime['trending']} ({regime['trending_pct']}%)")
        print(f"  UNKNOWN:  {regime['unknown']}")
        
        print("\n  Per asset regime distribution:")
        for asset in ["BTC", "ETH", "XRP"]:
            if asset in regime["by_asset"]:
                a = regime["by_asset"][asset]
                total_a = a["total"]
                ch_pct = round(a.get("choppy",0)/total_a*100,1) if total_a>0 else 0
                tr_pct = round(a.get("trending",0)/total_a*100,1) if total_a>0 else 0
                print(f"    {asset}: {ch_pct}% choppy, {tr_pct}% trending ({total_a} readings)")
    
    # V7 after widening
    v7 = results.get("V7")
    if v7:
        print(f"\n  -- V7 AFTER WIDENING (0.35-0.65) --")
        print(f"  Trades: {v7['trades']}, WR: {v7['wr']}%, P&L: ${v7['pnl']:+.2f}, ROI: {v7['roi']:+.1f}%")
        print(f"  vs Overnight: 0 trades -> {v7['trades']} trades. Filter fix WORKED!")
    
    # V8 after relaxing
    v8 = results.get("V8")
    if v8:
        print(f"\n  -- V8 AFTER RELAXING (threshold 0.004, band 0.35-0.65) --")
        print(f"  Trades: {v8['trades']}, WR: {v8['wr']}%, P&L: ${v8['pnl']:+.2f}, ROI: {v8['roi']:+.1f}%")
        print(f"  vs Overnight: 0 trades -> {v8['trades']} trades. Parameter fix WORKED!")
    elif not v8:
        print(f"\n  -- V8 --")
        v8_trades = load("hft_trades_v8.json")
        if len(v8_trades) == 0:
            print("  V8 STILL made 0 trades. Needs further tuning.")
        else:
            print(f"  V8 made {len(v8_trades)} trades.")
    
    # ASSET TOXICITY
    print("\n  -- ASSET TOXICITY CHECK --")
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
    
    # SAVE AUDIT
    audit = {
        "session": SESSION,
        "period": "2026-05-08 08:00-12:00 CDT (13:00-17:00 UTC)",
        "capital": CAPITAL,
        "leaderboard": sorted_bots,
        "regime": regime,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    os.makedirs("analysis", exist_ok=True)
    with open("analysis/audit_morning_20260508.json", "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, default=str)
    print(f"\n  Audit saved to analysis/audit_morning_20260508.json")


if __name__ == "__main__":
    main()
