import json
import requests
from collections import defaultdict

with open('profile_wallets_ranked.json') as f:
    ranked = json.load(f)

print(f"Total ranked wallets available: {len(ranked)}")

# Skip the first two (0: Sniper, 1: 0x9ac833)
for i, s in enumerate(ranked[2:7], start=3):
    wallet = s['wallet']
    diag = s['diagnostic']
    p = s['performance']
    
    # We only care about scalpers or directional that actually have some resolved trades
    if p['resolved_trades'] < 10 or 'Market Maker' in diag:
        print(f"\n[RANK #{i}] {wallet} | SKIP ({diag} / Resolved: {p['resolved_trades']})")
        continue

    print(f"\n=======================================================")
    print(f"RANK #{i} | Wallet: {wallet}")
    print(f"Diagnostic: {diag} | WR: {p['win_rate']:.1f}% | PnL: +")
    
    trades = []
    offset = 0
    for _ in range(2):
        try:
            resp = requests.get(f"https://data-api.polymarket.com/trades?user={wallet}&limit=1000&offset={offset}")
            t = resp.json()
            if not isinstance(t, list) or not t: break
            trades.extend(t)
            offset += 1000
        except:
            pass
            
    slugs = list(set([t.get('slug') for t in trades if t.get('slug')]))
    resolutions = {}
    for slug in slugs:
        try:
            resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
            events = resp.json()
            if events and events[0].get("markets"):
                m = events[0]["markets"][0]
                if m.get("closed"):
                    prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
                    resolutions[slug] = (float(prices[0]), float(prices[1]))
        except:
            pass

    stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0, 'spent': 0.0})

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
        
        key = f"{asset} {duration} {side}"
        stats[key]['spent'] += 1.0
        stats[key]['pnl'] += pnl
        if won: stats[key]['wins'] += 1
        else: stats[key]['losses'] += 1

    for k, v in sorted(stats.items()):
        t_count = v['wins'] + v['losses']
        if t_count < 5: continue
        wr = v['wins'] / t_count * 100
        roi = v['pnl'] / v['spent'] * 100
        print(f"  {k:15}: WR {wr:5.1f}% | ROI {roi:+6.1f}% | Trades: {t_count:3}")

