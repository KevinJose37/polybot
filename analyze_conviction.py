import json
import requests
import os
from datetime import datetime, timezone
from collections import defaultdict

target_wallet = '0xeebde7a0e019a63e6b476eb425505b7b3e6eba30'
all_target_trades = []
offset = 0

print("Fetching trades from API...")
for i in range(20):
    url = f'https://data-api.polymarket.com/trades?user={target_wallet}&limit=1000&offset={offset}'
    resp = requests.get(url)
    trades = resp.json()
    if not isinstance(trades, list) or not trades: break
    all_target_trades.extend(trades)
    offset += 1000
    
print(f'Fetched {len(all_target_trades)} total target trades.')

copy_trades = []
for file in ['hft_trades_copy.json', 'hft_trades_copy_05_05.json']:
    if os.path.exists(file):
        with open(file) as f:
            copy_trades.extend(json.load(f))

matches = []
for ct in copy_trades:
    status = ct.get('status')
    if status not in ('won', 'lost', 'sold'): continue
    
    asset = ct.get('asset')
    side = ct.get('side')
    ct_time = datetime.fromisoformat(ct['entry_time'].replace('Z', '+00:00'))
    
    best_match = None
    min_diff = 999999
    
    for tt in all_target_trades:
        if not isinstance(tt, dict): continue
        if ct.get('token_id') and tt.get('asset') != ct.get('token_id'): continue
        
        tt_time = int(float(tt.get('timestamp', 0)))
        diff = abs(ct_time.timestamp() - tt_time)
        if diff < 60 and diff < min_diff:  # relaxed time matching to 60s
            min_diff = diff
            best_match = tt
            
    if best_match:
        matches.append({
            'asset': asset,
            'duration': '15m' if '-15m-' in ct.get('market_slug', '') else '5m',
            'side': side,
            'status': status,
            'pnl': float(ct.get('pnl', 0) or 0),
            'original_size': float(best_match.get('size', 0))
        })

print(f'Successfully matched {len(matches)} trades with original sizes.')

results = defaultdict(lambda: {'wins': 0, 'losses': 0})

for m in matches:
    sz = m['original_size']
    if sz < 20: b = '1. < '
    elif sz < 50: b = '2. -'
    else: b = '3. > '
    
    key = f"{m['asset']} {m['duration']} {m['side']} - {b}"
    key_overall = f"OVERALL - {b}"
    
    if m['pnl'] > 0:
        results[key]['wins'] += 1
        results[key_overall]['wins'] += 1
    else:
        results[key]['losses'] += 1
        results[key_overall]['losses'] += 1

print('\n--- Analysis by Size Bucket ---')
for k in sorted(results.keys()):
    v = results[k]
    t = v['wins'] + v['losses']
    wr = (v['wins'] / t * 100) if t > 0 else 0
    print(f"{k:35}: W:{v['wins']:3} L:{v['losses']:3} | WR: {wr:5.1f}%")
