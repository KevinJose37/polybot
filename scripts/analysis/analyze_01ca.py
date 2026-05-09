import json
import requests
from collections import defaultdict
import time

wallet = '0x01ca5c77bf032f10b7cb5e3f730fbf497523200d'
print(f"Fetching trades for {wallet}...")

trades = []
offset = 0
for _ in range(5):
    resp = requests.get(f"https://data-api.polymarket.com/trades?user={wallet}&limit=1000&offset={offset}")
    t = resp.json()
    if not isinstance(t, list) or not t: break
    trades.extend(t)
    offset += 1000

print(f"Fetched {len(trades)} trades.")

# Collect unique markets
slugs = list(set([t.get('slug') for t in trades if t.get('slug')]))
print(f"Resolving {len(slugs)} unique markets...")

resolutions = {}
for i, slug in enumerate(slugs):
    try:
        resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
        events = resp.json()
        if events and events[0].get("markets"):
            m = events[0]["markets"][0]
            if m.get("closed"):
                prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
                resolutions[slug] = (float(prices[0]), float(prices[1]))
    except Exception as e:
        pass
    if i % 20 == 0:
        print(f"Resolved {i}/{len(slugs)}...")

print(f"Resolved {len(resolutions)} closed markets.")

# Analysis buckets
stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0, 'spent': 0.0, 'original_volume': 0.0})

for t in trades:
    slug = t.get('slug')
    if slug not in resolutions: continue
    
    side = t.get('outcome', '').upper()
    if side not in ('UP', 'DOWN'): continue
    
    price = float(t.get('price', 0))
    if price <= 0 or price >= 1: continue
    
    up_price, down_price = resolutions[slug]
    won = (up_price > 0.9) if side == 'UP' else (down_price > 0.9)
    
    pnl = (1.0 / price - 1.0) if won else -1.0
    
    # Classify
    asset = 'ETH' if 'eth' in slug.lower() or 'ethereum' in slug.lower() else 'BTC' if 'btc' in slug.lower() or 'bitcoin' in slug.lower() else 'OTHER'
    duration = '15m' if '-15m-' in slug else '5m' if '-5m-' in slug else 'OTHER'
    
    sz = float(t.get('size', 0))
    if sz < 100: size_bucket = '< '
    elif sz < 500: size_bucket = '-'
    else: size_bucket = '> '
    
    key_asset_side = f"{asset} {duration} {side}"
    key_size = f"{asset} {duration} {side} | {size_bucket}"
    
    for key in [key_asset_side, key_size]:
        stats[key]['spent'] += 1.0
        stats[key]['pnl'] += pnl
        stats[key]['original_volume'] += sz
        if won: stats[key]['wins'] += 1
        else: stats[key]['losses'] += 1

print("\n=== PERFORMANCE BY ASSET & SIDE (1$ STAKE) ===")
# Group by asset/side first
for k, v in sorted(stats.items()):
    if '|' in k: continue
    t_count = v['wins'] + v['losses']
    wr = v['wins'] / t_count * 100 if t_count else 0
    roi = v['pnl'] / v['spent'] * 100 if v['spent'] else 0
    avg_sz = v['original_volume'] / t_count if t_count else 0
    print(f"{k:15}: WR {wr:5.1f}% | PnL  | ROI {roi:+6.1f}% | Trades: {t_count:3} | Avg Size: ")

print("\n=== DRILL DOWN BY BET SIZE ===")
for k, v in sorted(stats.items()):
    if '|' not in k: continue
    t_count = v['wins'] + v['losses']
    if t_count < 5: continue # ignore tiny samples
    wr = v['wins'] / t_count * 100 if t_count else 0
    roi = v['pnl'] / v['spent'] * 100 if v['spent'] else 0
    print(f"{k:30}: WR {wr:5.1f}% | PnL  | Trades: {t_count:3}")

