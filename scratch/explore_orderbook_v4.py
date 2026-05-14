"""
Exploration v4 - Resolve condition_ids to market names via Polymarket API,
then do predictability analysis on crypto markets.
"""
import duckdb
import sys
import io
import json
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PARQUET = r"D:\Proyectos\polystudio\polystudio\data\parquet\polymarket_orderbook_2026-05-13T22.parquet"
con = duckdb.connect()
con.execute("SET memory_limit='512MB'")

# ====================================================================
# Get top 200 condition_ids to resolve via API
# ====================================================================
print("=" * 80)
print("RESOLVING CONDITION IDS TO MARKET NAMES")
print("=" * 80)

top_cids = con.execute(f"""
    SELECT CAST(market AS VARCHAR) as cid, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY cid
    ORDER BY cnt DESC
    LIMIT 200
""").fetchall()

# Try to resolve via Polymarket API
# The condition_id is a hex string that can be looked up
crypto_keywords = ['bitcoin', 'btc', 'xbt', 'ethereum', 'eth', 'solana', 'sol', 
                   'crypto', 'price', 'above', 'below', 'hit', 'reach',
                   'dogecoin', 'doge', 'ripple', 'xrp', 'cardano', 'ada',
                   'avalanche', 'avax', 'polygon', 'matic', 'chainlink', 'link']

resolved = {}
crypto_markets = {}
non_crypto = {}
errors = 0

print(f"\nResolving top {len(top_cids)} condition_ids via Polymarket API...")

for i, (cid, cnt) in enumerate(top_cids):
    if i % 20 == 0:
        print(f"  Progress: {i}/{len(top_cids)}...")
    
    # Try CLOB API
    try:
        url = f"https://clob.polymarket.com/markets/{cid}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            question = data.get('question', data.get('description', 'Unknown'))
            resolved[cid] = {
                'question': question,
                'records': cnt,
                'tokens': data.get('tokens', []),
                'condition_id': cid
            }
            
            # Check if crypto
            q_lower = question.lower()
            is_crypto = any(kw in q_lower for kw in crypto_keywords)
            if is_crypto:
                crypto_markets[cid] = resolved[cid]
    except Exception as e:
        errors += 1
        # Try gamma API as fallback
        try:
            url = f"https://gamma-api.polymarket.com/markets?condition_ids={cid}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if data and len(data) > 0:
                    question = data[0].get('question', 'Unknown')
                    resolved[cid] = {
                        'question': question,
                        'records': cnt,
                        'condition_id': cid,
                        'source': 'gamma'
                    }
                    q_lower = question.lower()
                    is_crypto = any(kw in q_lower for kw in crypto_keywords)
                    if is_crypto:
                        crypto_markets[cid] = resolved[cid]
        except:
            pass

print(f"\nResolved: {len(resolved)}/{len(top_cids)} condition_ids")
print(f"Errors: {errors}")
print(f"Crypto markets found: {len(crypto_markets)}")

# Print ALL resolved markets (top 200)
print("\n--- ALL RESOLVED MARKETS (top 200 by record count) ---")
for cid, info in sorted(resolved.items(), key=lambda x: x[1]['records'], reverse=True):
    is_crypto = cid in crypto_markets
    tag = " [CRYPTO]" if is_crypto else ""
    print(f"  {info['records']:>8,} records | {info['question'][:80]}{tag}")

# Print crypto markets detail
print("\n" + "=" * 80)
print(f"CRYPTO MARKETS FOUND: {len(crypto_markets)}")
print("=" * 80)

if crypto_markets:
    for cid, info in sorted(crypto_markets.items(), key=lambda x: x[1]['records'], reverse=True):
        print(f"\n  Market: {info['question']}")
        print(f"  Condition ID: {cid[:40]}...")
        print(f"  Records: {info['records']:,}")
        
        # Get token details
        tokens = info.get('tokens', [])
        if tokens:
            for t in tokens:
                tid = t.get('token_id', 'N/A')
                outcome = t.get('outcome', 'N/A')
                print(f"    Token: {outcome} -> {tid[:40]}...")
        
        # Get detailed stats from parquet
        stats = con.execute(f"""
            SELECT 
                MIN(timestamp)::VARCHAR, MAX(timestamp)::VARCHAR,
                MIN(price), MAX(price), AVG(price)::DECIMAL(9,4),
                MIN(best_bid), MAX(best_bid),
                MIN(best_ask), MAX(best_ask),
                COUNT(*),
                COUNT(DISTINCT asset_id)
            FROM read_parquet('{PARQUET}')
            WHERE CAST(market AS VARCHAR) = '{cid}'
              AND event_type = 'price_change'
        """).fetchone()
        print(f"    Time range: {stats[0]} to {stats[1]}")
        print(f"    Price: min={stats[2]}, max={stats[3]}, avg={stats[4]}")
        print(f"    Best bid: {stats[5]} - {stats[6]}")
        print(f"    Best ask: {stats[7]} - {stats[8]}")
        print(f"    Records: {stats[9]:,}, Unique tokens: {stats[10]}")
else:
    print("  No crypto-specific price markets found in top 200.")
    print("  This may indicate the dataset covers ALL Polymarket markets,")
    print("  not just crypto price prediction markets.")

# ====================================================================
# Also search by checking if asset_ids from known crypto markets exist
# ====================================================================
print("\n" + "=" * 80)
print("BROADER CRYPTO SEARCH")
print("=" * 80)

# Search all resolved questions for price-related patterns
price_pattern_markets = {}
for cid, info in resolved.items():
    q = info['question'].lower()
    # Pattern: "X above/below $Y" or "X price" or "X to hit $Y"
    price_patterns = ['above $', 'below $', 'hit $', 'reach $', 'price of', 'end above', 'end below',
                      'close above', 'close below', 'higher than', 'lower than', '$100k', '$200k',
                      'all-time high', 'ath']
    if any(p in q for p in price_patterns):
        price_pattern_markets[cid] = info

print(f"\nMarkets with price prediction patterns: {len(price_pattern_markets)}")
for cid, info in sorted(price_pattern_markets.items(), key=lambda x: x[1]['records'], reverse=True):
    print(f"  {info['records']:>8,} | {info['question'][:100]}")

# ====================================================================
# Categorize ALL resolved markets
# ====================================================================
print("\n" + "=" * 80)
print("MARKET CATEGORIES")
print("=" * 80)

categories = {
    'crypto_price': [], 'politics': [], 'sports': [], 'entertainment': [],
    'economics': [], 'tech': [], 'other': []
}

politics_kw = ['trump', 'biden', 'president', 'election', 'congress', 'senate', 'governor', 'democrat', 'republican', 'vote', 'political']
sports_kw = ['nba', 'nfl', 'mlb', 'soccer', 'football', 'basketball', 'baseball', 'tennis', 'f1', 'formula', 'ufc', 'boxing', 'championship', 'playoff', 'finals', 'match', 'game', 'win the']
entertainment_kw = ['movie', 'oscar', 'grammy', 'album', 'song', 'show', 'reality', 'streaming']
economics_kw = ['gdp', 'inflation', 'fed', 'interest rate', 'unemployment', 'recession', 'tariff', 'trade deal']
tech_kw = ['ai', 'apple', 'google', 'meta', 'microsoft', 'tesla']

for cid, info in resolved.items():
    q = info['question'].lower()
    if cid in crypto_markets or cid in price_pattern_markets:
        categories['crypto_price'].append(info)
    elif any(k in q for k in politics_kw):
        categories['politics'].append(info)
    elif any(k in q for k in sports_kw):
        categories['sports'].append(info)
    elif any(k in q for k in entertainment_kw):
        categories['entertainment'].append(info)
    elif any(k in q for k in economics_kw):
        categories['economics'].append(info)
    elif any(k in q for k in tech_kw):
        categories['tech'].append(info)
    else:
        categories['other'].append(info)

for cat, items in categories.items():
    total_records = sum(i['records'] for i in items)
    print(f"  {cat:20s}: {len(items):3d} markets, {total_records:>12,} records")

print("\nScript v4 completado.")
