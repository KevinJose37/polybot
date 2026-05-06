import json

with open('hft_trades_copy.json') as f:
    trades = json.load(f)

stats = {}
for t in trades:
    slug = t.get('market_slug', 'unknown')
    status = t.get('status', 'open')
    if status not in ('won', 'lost', 'sold'): continue
    
    asset = t.get('asset', 'unknown')
    
    # determine duration from slug
    duration = 'unknown'
    if '-5m-' in slug: duration = '5m'
    elif '-15m-' in slug: duration = '15m'
    
    key = f'{asset} {duration}'
    if key not in stats:
        stats[key] = {'pnl': 0.0, 'wins': 0, 'losses': 0}
        
    pnl = float(t.get('pnl', 0) or 0)
    stats[key]['pnl'] += pnl
    if pnl > 0:
        stats[key]['wins'] += 1
    else:
        stats[key]['losses'] += 1

print('--- Analysis by Asset and Duration ---')
for k, v in stats.items():
    total = v['wins'] + v['losses']
    winrate = (v['wins'] / total * 100) if total > 0 else 0
    pnl = v['pnl']
    print(f"{k:10}: PNL=  | W:{v['wins']} L:{v['losses']} | WinRate: {winrate:.1f}%")

