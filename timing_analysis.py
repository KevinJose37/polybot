import requests
import json
from collections import Counter

wallet = '0x9ac833e9cf85bb662cd4a0cfe3b3b4df7222d27c'

trades = []
offset = 0
for _ in range(2):
    resp = requests.get(f"https://data-api.polymarket.com/trades?user={wallet}&limit=1000&offset={offset}")
    t = resp.json()
    if not isinstance(t, list) or not t: break
    trades.extend(t)
    offset += 1000

print(f"Analyzing timing of {len(trades)} trades for {wallet}...")

# Example slug: btc-updown-5m-1715011200
# The last part is the expiry timestamp in seconds.

seconds_before_expiry = []
prices_bought = []

for t in trades:
    slug = t.get('slug', '')
    ts = t.get('timestamp', 0)
    price = float(t.get('price', 0))
    
    if not slug or not ts: continue
    
    # Extract expiry from slug
    parts = slug.split('-')
    if len(parts) > 0 and parts[-1].isdigit():
        expiry_ts = int(parts[-1])
        trade_ts = int(float(ts))
        
        diff = expiry_ts - trade_ts
        seconds_before_expiry.append(diff)
        prices_bought.append(price)

if not seconds_before_expiry:
    print("Could not parse expiry from slugs.")
else:
    avg_diff = sum(seconds_before_expiry) / len(seconds_before_expiry)
    min_diff = min(seconds_before_expiry)
    max_diff = max(seconds_before_expiry)
    
    print("\n=== TIMING ANALYSIS ===")
    print(f"Average time before expiry : {avg_diff:.1f} seconds")
    print(f"Minimum time before expiry : {min_diff} seconds")
    print(f"Maximum time before expiry : {max_diff} seconds")
    
    # Bucket the times
    buckets = {'< 10s': 0, '10s - 30s': 0, '30s - 60s': 0, '1m - 3m': 0, '> 3m': 0}
    for diff in seconds_before_expiry:
        if diff < 10: buckets['< 10s'] += 1
        elif diff <= 30: buckets['10s - 30s'] += 1
        elif diff <= 60: buckets['30s - 60s'] += 1
        elif diff <= 180: buckets['1m - 3m'] += 1
        else: buckets['> 3m'] += 1
        
    print("\n=== ENTRY TIME BUCKETS ===")
    for k, v in buckets.items():
        print(f"  {k:10}: {v} trades ({v/len(seconds_before_expiry)*100:.1f}%)")

    # Price analysis
    print("\n=== PRICE ANALYSIS ===")
    avg_p = sum(prices_bought) / len(prices_bought)
    print(f"Average entry price: ")
    
    price_buckets = {'< 0.10': 0, '0.10 - 0.30': 0, '0.30 - 0.70': 0, '0.70 - 0.90': 0, '> 0.90': 0}
    for p in prices_bought:
        if p < 0.10: price_buckets['< 0.10'] += 1
        elif p <= 0.30: price_buckets['0.10 - 0.30'] += 1
        elif p <= 0.70: price_buckets['0.30 - 0.70'] += 1
        elif p <= 0.90: price_buckets['0.70 - 0.90'] += 1
        else: price_buckets['> 0.90'] += 1
        
    print("\n=== ENTRY PRICE BUCKETS ===")
    for k, v in price_buckets.items():
        print(f"  {k:15}: {v} trades ({v/len(prices_bought)*100:.1f}%)")

