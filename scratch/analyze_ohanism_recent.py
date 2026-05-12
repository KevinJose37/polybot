import requests
import collections
from datetime import datetime, timezone, timedelta

wallet = "0x89b5cdaaa4866c1e738406712012a630b4078beb"
url = f"https://data-api.polymarket.com/activity?user={wallet}&limit=3000"

print(f"Fetching data for {wallet}...")
try:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    all_activity = resp.json()
except Exception as e:
    print(f"Error: {e}")
    exit(1)

now = datetime.now(timezone.utc)
recent_cutoff = now.timestamp() - (12 * 3600)

recent_activity = []
for a in all_activity:
    ts = a.get('timestamp')
    if ts and ts > recent_cutoff:
        recent_activity.append(a)

print(f"Found {len(recent_activity)} events in the last 12 hours.")

by_asset_act = collections.defaultdict(list)
for a in recent_activity:
    asset = a.get('asset')
    if asset:
        by_asset_act[asset].append(a)

stats_by_coin_tf = collections.defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0, 'positions': 0})
stats_by_slug = collections.defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0, 'coin': '', 'tf': ''})

def get_coin(title):
    t = title.lower()
    if 'btc' in t or 'bitcoin' in t: return 'BTC'
    if 'eth' in t or 'ethereum' in t: return 'ETH'
    if 'sol' in t or 'solana' in t: return 'SOL'
    if 'xrp' in t or 'ripple' in t: return 'XRP'
    return 'Other'

def get_tf(slug):
    s = slug.lower()
    if '-5m-' in s or '5m' in s: return '5m'
    if '-15m-' in s or '15m' in s: return '15m'
    return None 

for asset, a_list in by_asset_act.items():
    title = a_list[0].get('title', 'Unknown')
    slug = a_list[0].get('slug', '')
    tf = get_tf(slug)
    if not tf: continue
    coin = get_coin(title)
    
    buys = [a for a in a_list if a.get('type') == 'TRADE' and a.get('side', '').upper() == 'BUY']
    sells = [a for a in a_list if a.get('type') == 'TRADE' and a.get('side', '').upper() == 'SELL']
    redeems = [a for a in a_list if a.get('type') == 'REDEEM']
    
    if not buys: continue
    
    cost = sum(float(a.get('usdcSize', 0)) for a in buys)
    
    proceeds = sum(float(a.get('usdcSize', 0)) for a in sells)
    proceeds += sum(float(a.get('size', 0)) * 1.0 for a in redeems) 
    
    pnl = proceeds - cost
    
    is_win = pnl > 0
    
    combo = f"{coin} {tf}"
    stats_by_coin_tf[combo]['positions'] += 1
    stats_by_coin_tf[combo]['pnl'] += pnl
    if is_win:
        stats_by_coin_tf[combo]['wins'] += 1
    else:
        stats_by_coin_tf[combo]['losses'] += 1
        
    stats_by_slug[title]['tf'] = tf
    stats_by_slug[title]['coin'] = coin
    stats_by_slug[title]['pnl'] += pnl
    if is_win:
        stats_by_slug[title]['wins'] += 1
    else:
        stats_by_slug[title]['losses'] += 1

print("\n=== RENDIMIENTO DE OHANISM (ÚLTIMAS 12 HORAS) ===")
print("Solo analizando mercados de 5m y 15m\n")

print("--- Por Moneda y Temporalidad ---")
sorted_combo = sorted(stats_by_coin_tf.items(), key=lambda x: x[1]['pnl'], reverse=True)
for combo, s in sorted_combo:
    total = s['positions']
    wr = (s['wins'] / total * 100) if total > 0 else 0
    print(f"  {combo:<10} | Posiciones: {total:<3} | WR: {wr:5.1f}% | P&L: ${s['pnl']:+8.2f}")

print("\n--- Mejores 5 Mercados Específicos ---")
sorted_slug = sorted(stats_by_slug.items(), key=lambda x: x[1]['pnl'], reverse=True)
for title, s in sorted_slug[:5]:
    total = s['wins'] + s['losses']
    wr = (s['wins'] / total * 100) if total > 0 else 0
    print(f"  {title[:45]:<45} | WR: {wr:5.1f}% | P&L: ${s['pnl']:+8.2f}")

print("\n--- Peores 5 Mercados Específicos ---")
for title, s in sorted_slug[-5:]:
    total = s['wins'] + s['losses']
    wr = (s['wins'] / total * 100) if total > 0 else 0
    print(f"  {title[:45]:<45} | WR: {wr:5.1f}% | P&L: ${s['pnl']:+8.2f}")
