import json

with open('hft_trades_copy_05_05.json') as f:
    trades = json.load(f)

stats = {
    'ETH 15m UP': {'spent': 0.0, 'pnl': 0.0, 'wins': 0, 'losses': 0},
    'ETH 5m UP': {'spent': 0.0, 'pnl': 0.0, 'wins': 0, 'losses': 0},
}

for t in trades:
    status = t.get('status', 'open')
    if status not in ('won', 'lost', 'sold'): continue
    
    asset = t.get('asset', 'unknown')
    side = t.get('side', 'unknown')
    
    if asset != 'ETH' or side != 'UP': continue
    
    slug = t.get('market_slug', '')
    duration = '15m' if '-15m-' in slug else '5m'
    
    key = f'ETH {duration} UP'
    
    pnl = float(t.get('pnl', 0) or 0)
    stake = float(t.get('stake', 1.0))
    
    stats[key]['spent'] += stake
    stats[key]['pnl'] += pnl
    if pnl > 0:
        stats[key]['wins'] += 1
    else:
        stats[key]['losses'] += 1

print('--- Simulation of Profit ( USD Stake) overnight ---')
for k, v in stats.items():
    total = v['wins'] + v['losses']
    wr = (v['wins'] / total * 100) if total > 0 else 0
    print(f"{k}:")
    print(f"  Trades executed: {total}")
    print(f"  Money spent (invested): ")
    print(f"  Win Rate: {wr:.1f}% (W:{v['wins']} L:{v['losses']})")
    print(f"  Net Profit (PNL): ")
    print(f"  ROI: {(v['pnl']/v['spent']*100 if v['spent'] else 0):+.1f}%")
    print()

