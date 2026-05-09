"""
Overnight Session Analysis — May 8, 2026 (12AM - 8AM CDT / 5AM - 1PM UTC)
Comprehensive audit of all bot strategies.
"""
import json
import os
from datetime import datetime, timezone
from collections import defaultdict

CAPITAL = 24.0

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
    
    # Per-asset breakdown
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
    
    # Hindsight analysis
    hindsight_reviewed = [t for t in trades if t.get("hindsight_reviewed")]
    good_exits = sum(1 for t in hindsight_reviewed if t["hindsight"]["decision"] == "GOOD")
    bad_exits = sum(1 for t in hindsight_reviewed if t["hindsight"]["decision"] == "BAD")
    
    # Hold vs sell comparison
    hold_pnl = sum(t.get("hindsight", {}).get("held_pnl", t.get("pnl") or 0) for t in trades)
    sell_pnl = pnl
    
    # For hold-to-resolution bots, use actual pnl as hold pnl
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
    
    # Per asset
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
    
    # ── PRINT LEADERBOARD ──
    print("\n" + "=" * 90)
    print("  📊 OVERNIGHT SESSION AUDIT — May 8, 2026 (12AM - 8AM CDT)")
    print("=" * 90)
    
    print("\n  ── LEADERBOARD (sorted by ROI) ──")
    print(f"  {'Bot':<10} {'Trades':>6} {'W/L':>8} {'WR%':>6} {'P&L':>8} {'ROI':>8} {'$/Trade':>8}")
    print(f"  {'─'*10} {'─'*6} {'─'*8} {'─'*6} {'─'*8} {'─'*8} {'─'*8}")
    
    sorted_bots = sorted(results.values(), key=lambda x: x["roi"], reverse=True)
    for r in sorted_bots:
        wl = f"{r['wins']}W/{r['losses']}L"
        ppt = round(r["pnl"] / r["trades"], 2) if r["trades"] > 0 else 0
        icon = "🟢" if r["roi"] > 0 else "🔴"
        print(f"  {icon} {r['name']:<8} {r['trades']:>6} {wl:>8} {r['wr']:>5.1f}% ${r['pnl']:>+7.2f} {r['roi']:>+7.1f}% ${ppt:>+7.2f}")
    
    # ── ASSET BREAKDOWN ──
    print("\n  ── ASSET BREAKDOWN PER BOT ──")
    for r in sorted_bots:
        print(f"\n  {r['name']}:")
        for asset in ["BTC", "ETH", "XRP", "SOL"]:
            if asset in r["by_asset"]:
                a = r["by_asset"][asset]
                print(f"    {asset}: {a['trades']}T, {a['wr']:.0f}% WR, ${a['pnl']:+.2f}")
    
    # ── HINDSIGHT ANALYSIS ──
    print("\n  ── HINDSIGHT: HOLD vs ACTIVE EXITS ──")
    print(f"  {'Bot':<10} {'Good':>6} {'Bad':>6} {'Hold P&L':>10} {'Sell P&L':>10} {'Diff':>8} {'Verdict':>10}")
    print(f"  {'─'*10} {'─'*6} {'─'*6} {'─'*10} {'─'*10} {'─'*8} {'─'*10}")
    
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
    
    # ── REGIME ANALYSIS ──
    if regime:
        print("\n  ── V8 REGIME DETECTION ──")
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
    
    # ── V7 STATUS ──
    v7 = results.get("V7")
    if v7:
        print(f"\n  ── V7 PRODUCTION BOT ──")
        print(f"  Trades: {v7['trades']}, P&L: ${v7['pnl']:+.2f}")
        if v7['trades'] == 0:
            print("  ⚠️  V7 made ZERO trades! Price cap 0.46-0.54 was too tight for overnight session.")
    
    # ── V8 STATUS ──
    v8 = results.get("V8")
    if not v8:
        print(f"\n  ── V8 CHOPPY MEAN REVERSION ──")
        print("  ⚠️  V8 made ZERO trades! The mean-reversion signals never triggered.")
        print("  Possible reasons:")
        print("    - reversion_threshold (0.008) too tight for overnight volatility")
        print("    - Regime detector was mostly TRENDING, blocking CHOPPY-only entries")
        print("    - Price filters (0.42-0.58) too narrow")
    
    # ── SAVE AUDIT ──
    audit = {
        "session": "overnight_may08_2026",
        "period": "2026-05-08 00:00 CDT - 08:00 CDT (05:00-13:00 UTC)",
        "capital": CAPITAL,
        "leaderboard": sorted_bots,
        "regime": regime,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    os.makedirs("analysis", exist_ok=True)
    with open("analysis/audit_overnight_20260508.json", "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, default=str)
    print(f"\n  ✅ Audit saved to analysis/audit_overnight_20260508.json")
    
    # ── ASSET TOXICITY ──
    print("\n  ── ASSET TOXICITY CHECK ──")
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
            icon = "🟢" if pnl > 0 else "🔴"
            print(f"  {icon} {asset}: {a['trades']}T, {wr}% WR, ${pnl:+.2f}")


if __name__ == "__main__":
    main()
