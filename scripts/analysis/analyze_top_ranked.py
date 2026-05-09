import json
import requests
from collections import defaultdict

print('Loading ranked wallets...')
with open('profile_wallets_ranked.json') as f:
    ranked = json.load(f)

print(f'Found {len(ranked)} ranked wallets.')

for i, s in enumerate(ranked[:3]):
    wallet = s['wallet']
    print(f"\n=======================================================")
    print(f"RANK #{i+1} | Wallet: {wallet}")
    print(f"Diagnostic: {s['diagnostic']}")
    
    p = s['performance']
    print(f"Reported WR: {p['win_rate']:.1f}% | PnL:  | PF: {p['profit_factor']:.2f} | Stab: {p['stability_score']:.2f}")
    
    print(f"Fetching trades for deeper analysis...")
    trades = []
    offset = 0
    for _ in range(5):
        resp = requests.get(f"https://data-api.polymarket.com/trades?user={wallet}&limit=1000&offset={offset}")
        t = resp.json()
        if not isinstance(t, list) or not t: break
        trades.extend(t)
        offset += 1000
    
    slugs = list(set([t.get('slug') for t in trades if t.get('slug')]))
    resolutions = {}
    for j, slug in enumerate(slugs):
        try:
            resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
            events = resp.json()
            if events and events[0].get("markets"):
                m = events[0]["markets"][0]
                if m.get("closed"):
                    prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
                    resolutions[slug] = (float(prices[0]), float(prices[1]))
        except Exception:
            pass

    stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0, 'spent': 0.0, 'volume': 0.0})

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
            stats[key]['volume'] += sz
            if won: stats[key]['wins'] += 1
            else: stats[key]['losses'] += 1

    print("\n--- PERFORMANCE BY ASSET & SIDE ---")
    for k, v in sorted(stats.items()):
        if '|' in k: continue
        t_count = v['wins'] + v['losses']
        if t_count < 5: continue
        wr = v['wins'] / t_count * 100
        roi = v['pnl'] / v['spent'] * 100
        print(f"  {k:15}: WR {wr:5.1f}% | ROI {roi:+6.1f}% | Trades: {t_count}")

    print("\n--- DRILL DOWN BY BET SIZE ---")
    for k, v in sorted(stats.items()):
        if '|' not in k: continue
        t_count = v['wins'] + v['losses']
        if t_count < 5: continue
        wr = v['wins'] / t_count * 100
        roi = v['pnl'] / v['spent'] * 100
        print(f"  {k:30}: WR {wr:5.1f}% | ROI {roi:+6.1f}% | Trades: {t_count}")

