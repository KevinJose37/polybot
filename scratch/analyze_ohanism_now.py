import requests
import collections
import statistics
import time

wallet = '0x89b5cdaaa4866c1e738406712012a630b4078beb'
url = f'https://data-api.polymarket.com/activity?user={wallet}&limit=3000'

try:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    all_activity = resp.json()
except Exception as e:
    print(f'Error: {e}')
    exit(1)

# Filtrar ultimas 12 horas
now_ts = int(time.time())
cutoff = now_ts - (12 * 3600)
all_activity = [a for a in all_activity if a.get('timestamp', 0) >= cutoff]

buy_events = []
sell_events = []
redeem_events = []

for item in all_activity:
    action = item.get('type')
    side = item.get('side', '')
    if action == 'TRADE':
        if side == 'BUY':
            buy_events.append(item)
        elif side == 'SELL':
            sell_events.append(item)
    elif action == 'REDEEM':
        redeem_events.append(item)

positions = collections.defaultdict(lambda: {
    'title': '', 'asset': '', 'side': '', 'buy_size': 0.0, 'buy_shares': 0.0, 
    'sell_value': 0.0, 'status': 'open', 'buy_count': 0
})

for b in buy_events:
    tid = b.get('conditionId', b.get('asset_id'))
    title = b.get('title', 'Unknown')
    outcome = b.get('outcome', 'Unknown')
    size = float(b.get('usdcSize', 0))
    shares = float(b.get('size', 0))
    
    positions[tid]['title'] = title
    positions[tid]['asset'] = title.split(' ')[0] if title else 'Unknown'
    positions[tid]['side'] = outcome
    positions[tid]['buy_size'] += size
    positions[tid]['buy_shares'] += shares
    positions[tid]['buy_count'] += 1

for s in sell_events:
    tid = s.get('conditionId', s.get('asset_id'))
    size = float(s.get('usdcSize', 0))
    if tid in positions:
        positions[tid]['sell_value'] += size
        positions[tid]['status'] = 'sold'

for r in redeem_events:
    tid = r.get('conditionId', r.get('asset_id'))
    size = float(r.get('usdcSize', 0))
    if tid in positions:
        positions[tid]['sell_value'] += size
        positions[tid]['status'] = 'redeemed'

wins = []
losses = []
coin_stats = collections.defaultdict(lambda: {'wins': 0, 'losses': 0, 'win_bets': [], 'loss_bets': [], 'pnl': 0.0})

for tid, p in positions.items():
    if p['status'] == 'open':
        continue
    
    pnl = p['sell_value'] - p['buy_size']
    asset = p['asset']
    
    if pnl > 0:
        wins.append(p)
        coin_stats[asset]['wins'] += 1
        coin_stats[asset]['win_bets'].append(p['buy_size'])
        coin_stats[asset]['pnl'] += pnl
    else:
        losses.append(p)
        coin_stats[asset]['losses'] += 1
        coin_stats[asset]['loss_bets'].append(p['buy_size'])
        coin_stats[asset]['pnl'] += pnl

total_resolved = len(wins) + len(losses)
wr = (len(wins) / total_resolved * 100) if total_resolved > 0 else 0

win_sizes = [w['buy_size'] for w in wins]
loss_sizes = [l['buy_size'] for l in losses]
total_pnl = sum([p['sell_value'] - p['buy_size'] for p in wins + losses])

print('========================================')
print(f'ANÁLISIS DE WALLET: {wallet} (Últimas 12 hrs)')
print('========================================')
print(f'Total Trades Resueltos: {total_resolved}')
print(f'Win Rate: {wr:.2f}% ({len(wins)}W / {len(losses)}L)')
print(f'Total PnL: ${total_pnl:.2f}')
print('')
print('--- TAMAÑO DE APUESTAS ---')
print(f"WIN - Promedio: ${statistics.mean(win_sizes) if win_sizes else 0:.2f} (Max: ${max(win_sizes) if win_sizes else 0:.2f})")
print(f"LOSS - Promedio: ${statistics.mean(loss_sizes) if loss_sizes else 0:.2f} (Max: ${max(loss_sizes) if loss_sizes else 0:.2f})")
print('')
print('--- DESGLOSE POR MONEDA ---')
for asset, st in sorted(coin_stats.items(), key=lambda x: x[1]['wins']+x[1]['losses'], reverse=True):
    total = st['wins'] + st['losses']
    a_wr = st['wins'] / total * 100
    avg_win = statistics.mean(st['win_bets']) if st['win_bets'] else 0
    avg_loss = statistics.mean(st['loss_bets']) if st['loss_bets'] else 0
    
    print(f'[{asset}] Trades: {total} | WR: {a_wr:.1f}% ({st["wins"]}W/{st["losses"]}L) | PnL: ${st["pnl"]:.2f}')
    print(f'      Promedio al Ganar: ${avg_win:.2f} | Promedio al Perder: ${avg_loss:.2f}')
