import json
import glob
import os
from datetime import datetime

# Load ALL V1 trades from every session
all_trades = []

v1_files = [
    ("S1: Overnight May 5", "archive/hft_trades.json"),
    ("S2: May 6 Night", "archive/trades_20260506_7pm_11pm/hft_trades.json"),
    ("S3: May 7 Late Night", "archive/trades_20260507_12am_8am/hft_trades.json"),
    ("S4: May 7 Afternoon", "backup_trades_2026-05-07_12pm_6pm/hft_trades.json"),
    ("S5: Tonight", "hft_trades.json"),
    ("S1-original", "analysis/sesion1_may05_overnight/hft_trades_v1_original.json"),
    ("S1-session", "analysis/sesion1_may05_overnight/hft_trades.json"),
    ("Morning backup", "backup_trades_20260504_morning/hft_trades.json"),
]

seen_ids = set()  # Deduplicate by (entry_time, asset, side, entry_price)

for label, path in v1_files:
    if not os.path.exists(path):
        continue
    try:
        data = json.load(open(path, encoding='utf-8'))
        trades = data.get('trades', data) if isinstance(data, dict) else data
        for t in trades:
            if t.get('pnl') is None:
                continue
            # Dedup key
            key = (t.get('entry_time',''), t.get('asset',''), t.get('side',''), t.get('entry_price',0))
            if key in seen_ids:
                continue
            seen_ids.add(key)
            
            # Parse entry time
            try:
                et = datetime.fromisoformat(t['entry_time'].replace('Z', '+00:00'))
                t['_hour_utc'] = et.hour
                t['_date'] = et.strftime('%Y-%m-%d')
                # CDT = UTC-5
                cdt_hour = (et.hour - 5) % 24
                t['_hour_cdt'] = cdt_hour
                # Night = 6pm-8am CDT (18-24, 0-8), Day = 8am-6pm CDT (8-18)
                t['_period'] = 'NIGHT' if (cdt_hour >= 18 or cdt_hour < 8) else 'DAY'
            except:
                t['_hour_utc'] = -1
                t['_hour_cdt'] = -1
                t['_period'] = 'UNKNOWN'
            
            t['_session'] = label
            all_trades.append(t)
    except Exception as e:
        pass

print(f"Total V1 trades loaded (deduplicated): {len(all_trades)}")
print()

# ══════════════════════════════════════════════════════════════
# QUESTION 1: Is XRP bad at night across ALL days?
# ══════════════════════════════════════════════════════════════
print("=" * 70)
print("  Q1: XRP PERFORMANCE BY TIME OF DAY (ALL SESSIONS)")
print("=" * 70)

for asset in ['BTC', 'ETH', 'XRP', 'SOL']:
    for period in ['DAY', 'NIGHT']:
        subset = [t for t in all_trades if t.get('asset') == asset and t.get('_period') == period]
        if not subset:
            continue
        wins = len([t for t in subset if float(t['pnl']) > 0])
        losses = len([t for t in subset if float(t['pnl']) <= 0])
        pnl = sum(float(t['pnl']) for t in subset)
        wr = wins / len(subset) * 100 if subset else 0
        
        # By date
        dates = {}
        for t in subset:
            d = t.get('_date', '?')
            if d not in dates:
                dates[d] = {'w': 0, 'l': 0, 'pnl': 0}
            if float(t['pnl']) > 0:
                dates[d]['w'] += 1
            else:
                dates[d]['l'] += 1
            dates[d]['pnl'] += float(t['pnl'])
        
        print(f"\n  {asset} ({period}):")
        print(f"    Total: {len(subset)} trades | {wins}W/{losses}L | WR: {wr:.1f}% | PnL: ${pnl:+.2f}")
        print(f"    By date:")
        for d, v in sorted(dates.items()):
            print(f"      {d}: {v['w']}W/{v['l']}L | PnL: ${v['pnl']:+.2f}")

# ══════════════════════════════════════════════════════════════
# QUESTION 2: Choppy Market Analysis
# Define "choppy" as |signal_score| < 0.50
# ══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("  Q2: CHOPPY vs TRENDING MARKET ANALYSIS")
print("  (Choppy = |signal_score| < 0.50, Trending = >= 0.50)")
print("=" * 70)

choppy = [t for t in all_trades if t.get('signal_score') and abs(float(t['signal_score'])) < 0.50]
trending = [t for t in all_trades if t.get('signal_score') and abs(float(t['signal_score'])) >= 0.50]

def summarize(label, subset):
    if not subset:
        print(f"\n  {label}: No trades")
        return
    wins = len([t for t in subset if float(t['pnl']) > 0])
    losses = len([t for t in subset if float(t['pnl']) <= 0])
    pnl = sum(float(t['pnl']) for t in subset)
    wr = wins / len(subset) * 100
    avg_score = sum(abs(float(t.get('signal_score', 0))) for t in subset) / len(subset)
    avg_entry = sum(float(t.get('entry_price', 0)) for t in subset) / len(subset)
    print(f"\n  {label}:")
    print(f"    Total: {len(subset)} trades | {wins}W/{losses}L | WR: {wr:.1f}% | PnL: ${pnl:+.2f}")
    print(f"    Avg Score: {avg_score:.3f} | Avg Entry: ${avg_entry:.3f}")

summarize("ALL CHOPPY TRADES (score < 0.50)", choppy)
summarize("ALL TRENDING TRADES (score >= 0.50)", trending)

# Choppy by period
print()
print("  CHOPPY by Time of Day:")
for period in ['DAY', 'NIGHT']:
    subset = [t for t in choppy if t.get('_period') == period]
    summarize(f"  Choppy {period}", subset)

# Choppy by asset
print()
print("  CHOPPY by Asset:")
for asset in ['BTC', 'ETH', 'XRP', 'SOL']:
    subset = [t for t in choppy if t.get('asset') == asset]
    summarize(f"  Choppy {asset}", subset)

# Trending by asset
print()
print("  TRENDING by Asset:")
for asset in ['BTC', 'ETH', 'XRP', 'SOL']:
    subset = [t for t in trending if t.get('asset') == asset]
    summarize(f"  Trending {asset}", subset)

# Choppy by period AND asset (the full matrix)
print()
print("  FULL MATRIX: Asset x Period x Regime")
print(f"  {'Asset':<5} {'Period':<7} {'Regime':<10} {'Trades':>6} {'WR':>7} {'PnL':>8}")
print(f"  {'-'*5} {'-'*7} {'-'*10} {'-'*6} {'-'*7} {'-'*8}")

for asset in ['BTC', 'ETH', 'XRP']:
    for period in ['DAY', 'NIGHT']:
        for regime, label in [(choppy, 'CHOPPY'), (trending, 'TRENDING')]:
            subset = [t for t in regime if t.get('asset') == asset and t.get('_period') == period]
            if not subset:
                continue
            wins = len([t for t in subset if float(t['pnl']) > 0])
            pnl = sum(float(t['pnl']) for t in subset)
            wr = wins / len(subset) * 100
            print(f"  {asset:<5} {period:<7} {label:<10} {len(subset):>6} {wr:>6.1f}% {pnl:>+7.2f}")
