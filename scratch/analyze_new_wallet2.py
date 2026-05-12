import requests
import collections
import statistics

wallet = "0x53208bf2aac48b8253b2bdf6d92496df789df3b2"
url = f"https://data-api.polymarket.com/activity?user={wallet}&limit=3000"

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

for tid, p in positions.items():
    if p['status'] == 'open':
        continue
    
    pnl = p['sell_value'] - p['buy_size']
    asset = p['asset']
    
    if pnl > 0:
        wins.append(p)
    else:
        losses.append(p)

def print_bracket(sizes):
    brackets = {"<$50": 0, "$50-$100": 0, "$100-$200": 0, "$200-$300": 0, ">$300": 0}
    for s in sizes:
        if s < 50: brackets["<$50"] += 1
        elif s < 100: brackets["$50-$100"] += 1
        elif s < 200: brackets["$100-$200"] += 1
        elif s < 300: brackets["$200-$300"] += 1
        else: brackets[">$300"] += 1
    for k, v in brackets.items():
        if v > 0:
            print(f"    {k}: {v} trades")

win_sizes = [w['buy_size'] for w in wins]
loss_sizes = [l['buy_size'] for l in losses]

print("--- DISTRIBUCIÓN DE APUESTAS EN WINS ---")
print_bracket(win_sizes)
print("--- DISTRIBUCIÓN DE APUESTAS EN LOSSES ---")
print_bracket(loss_sizes)

print("\n--- DETALLE DE LAS PÉRDIDAS ---")
for l in losses:
    print(f"Perdió en: {l['title']}")
    print(f"  Apostó: ${l['buy_size']:.2f} | Recuperó: ${l['sell_value']:.2f} | PnL: ${l['sell_value'] - l['buy_size']:.2f}")

