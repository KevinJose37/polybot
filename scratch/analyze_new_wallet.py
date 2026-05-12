import requests
import collections
import statistics

wallet = "0x53208bf2aac48b8253b2bdf6d92496df789df3b2"
url = f"https://data-api.polymarket.com/activity?user={wallet}&limit=3000"

print(f"Fetching data for {wallet}...")
try:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    all_activity = resp.json()
except Exception as e:
    print(f"Error: {e}")
    exit(1)

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

print(f"Found {len(buy_events)} Buys, {len(sell_events)} Sells, {len(redeem_events)} Redeems")

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
coin_stats = collections.defaultdict(lambda: {'wins': 0, 'losses': 0, 'win_bets': [], 'loss_bets': []})

for tid, p in positions.items():
    if p['status'] == 'open':
        continue
    
    pnl = p['sell_value'] - p['buy_size']
    asset = p['asset']
    
    if p['sell_value'] > 0:
        wins.append(p)
        coin_stats[asset]['wins'] += 1
        coin_stats[asset]['win_bets'].append(p['buy_size'])
    else:
        losses.append(p)
        coin_stats[asset]['losses'] += 1
        coin_stats[asset]['loss_bets'].append(p['buy_size'])

total_resolved = len(wins) + len(losses)
wr = (len(wins) / total_resolved * 100) if total_resolved > 0 else 0

win_sizes = [w['buy_size'] for w in wins]
loss_sizes = [l['buy_size'] for l in losses]

print("========================================")
print(f"ANÁLISIS DE WALLET: {wallet}")
print("========================================")
print(f"Total Trades Resueltos: {total_resolved}")
print(f"Win Rate: {wr:.2f}% ({len(wins)}W / {len(losses)}L)")
print(f"Total PnL: ${sum([p['sell_value'] - p['buy_size'] for p in wins + losses]):.2f}")
print("")
print("--- TAMAÑO DE APUESTAS ---")
print(f"WIN - Promedio de apuesta: ${statistics.mean(win_sizes) if win_sizes else 0:.2f}")
print(f"WIN - Apuesta Max: ${max(win_sizes) if win_sizes else 0:.2f}")
print(f"WIN - Apuesta Min: ${min(win_sizes) if win_sizes else 0:.2f}")
print("")
print(f"LOSS - Promedio de apuesta: ${statistics.mean(loss_sizes) if loss_sizes else 0:.2f}")
print(f"LOSS - Apuesta Max: ${max(loss_sizes) if loss_sizes else 0:.2f}")
print(f"LOSS - Apuesta Min: ${min(loss_sizes) if loss_sizes else 0:.2f}")
print("")
print("--- DESGLOSE POR MONEDA (ASSET) ---")
for asset, st in sorted(coin_stats.items(), key=lambda x: x[1]['wins']+x[1]['losses'], reverse=True):
    total = st['wins'] + st['losses']
    a_wr = st['wins'] / total * 100
    avg_win = statistics.mean(st['win_bets']) if st['win_bets'] else 0
    avg_loss = statistics.mean(st['loss_bets']) if st['loss_bets'] else 0
    max_win = max(st['win_bets']) if st['win_bets'] else 0
    max_loss = max(st['loss_bets']) if st['loss_bets'] else 0
    
    print(f"[{asset}] Trades: {total} | WR: {a_wr:.1f}% ({st['wins']}W/{st['losses']}L)")
    print(f"      Avg Win Bet: ${avg_win:.2f} (Max: ${max_win:.2f}) | Avg Loss Bet: ${avg_loss:.2f} (Max: ${max_loss:.2f})")

print("========================================")
