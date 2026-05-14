"""
Exhaustive exploration v2 - fixes encoding and BLOB market column.
"""
import duckdb
import sys
import io

# Fix Windows encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PARQUET = r"D:\Proyectos\polystudio\polystudio\data\parquet\polymarket_orderbook_2026-05-13T22.parquet"
con = duckdb.connect()
con.execute("SET memory_limit='512MB'")

print("=" * 80)
print("PASO 1 - INVENTARIO (continuacion)")
print("=" * 80)

# Temporal range - avoid pytz, use raw SQL
print("\n--- Rango temporal ---")
ts = con.execute(f"""
    SELECT 
        MIN(timestamp)::VARCHAR, MAX(timestamp)::VARCHAR,
        MIN(timestamp_received)::VARCHAR, MAX(timestamp_received)::VARCHAR
    FROM read_parquet('{PARQUET}')
""").fetchone()
print(f"  timestamp:          min={ts[0]}, max={ts[1]}")
print(f"  timestamp_received: min={ts[2]}, max={ts[3]}")

# Duration
dur = con.execute(f"""
    SELECT DATEDIFF('hour', MIN(timestamp), MAX(timestamp)) as hours,
           DATEDIFF('minute', MIN(timestamp), MAX(timestamp)) as minutes
    FROM read_parquet('{PARQUET}')
""").fetchone()
print(f"  Duration: {dur[0]} hours ({dur[1]} minutes)")

# Market column is BLOB - decode it
print("\n--- Market column (BLOB -> text) ---")
sample_markets = con.execute(f"""
    SELECT DISTINCT CAST(market AS VARCHAR) as market_hex, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY market_hex
    ORDER BY cnt DESC
    LIMIT 20
""").fetchall()
print(f"  Top 20 markets by record count:")
for m in sample_markets:
    # Try to decode the hex/blob
    raw = m[0]
    print(f"    market_raw={raw[:80]}... | records={m[1]:,}")

# Actually decode: the BLOB stores ASCII bytes of the hex condition_id
print("\n--- Decoding market BLOB to condition_id ---")
decoded = con.execute(f"""
    SELECT DISTINCT ENCODE(market) as market_decoded, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY market_decoded
    ORDER BY cnt DESC
    LIMIT 30
""").fetchall()
print(f"  Top 30 decoded markets:")
for d in decoded:
    print(f"    {d[0]} | {d[1]:,} records")

# Let's try a different decode approach  
print("\n--- Alternative decode of market ---")
alt_decoded = con.execute(f"""
    SELECT DISTINCT CAST(market AS TEXT) as market_text, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY market_text
    ORDER BY cnt DESC  
    LIMIT 10
""").fetchall()
for d in alt_decoded:
    val = str(d[0])
    print(f"    {val[:100]} | {d[1]:,} records")

# Event types
print("\n--- Event types ---")
events = con.execute(f"""
    SELECT event_type, COUNT(*) as cnt,
           ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) as pct
    FROM read_parquet('{PARQUET}')
    GROUP BY event_type
    ORDER BY cnt DESC
""").fetchall()
for e in events:
    print(f"  {e[0]:30s}: {e[1]:>12,} ({e[2]}%)")

# Side distribution
print("\n--- Side distribution ---")
sides = con.execute(f"""
    SELECT side, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY side
    ORDER BY cnt DESC
""").fetchall()
for s in sides:
    print(f"  {str(s[0]):10s}: {s[1]:>12,}")

# Asset_id: how many unique and sample
print("\n--- Asset IDs ---")
n_assets = con.execute(f"""
    SELECT COUNT(DISTINCT asset_id) FROM read_parquet('{PARQUET}')
""").fetchone()[0]
print(f"  Unique asset_ids: {n_assets:,}")

top_assets = con.execute(f"""
    SELECT asset_id, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY asset_id
    ORDER BY cnt DESC
    LIMIT 20
""").fetchall()
print(f"\n  Top 20 asset_ids by count:")
for a in top_assets:
    print(f"    {a[0]}: {a[1]:,}")

print("\n" + "=" * 80)
print("PASO 3 - CALIDAD DE DATOS")
print("=" * 80)

# Null analysis
print("\n--- Porcentaje de NULLs ---")
col_names = ['timestamp_received', 'timestamp', 'market', 'event_type', 'asset_id', 
             'bids', 'asks', 'price', 'size', 'side', 'best_bid', 'best_ask', 
             'fee_rate_bps', 'transaction_hash', 'old_tick_size', 'new_tick_size']

null_queries = []
for col in col_names:
    null_queries.append(f'ROUND(100.0 * SUM(CASE WHEN "{col}" IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2)')

null_result = con.execute(f"""
    SELECT {', '.join(null_queries)} 
    FROM read_parquet('{PARQUET}')
""").fetchone()

for i, col in enumerate(col_names):
    pct = null_result[i]
    flag = " ** >20% NULLS **" if pct > 20 else ""
    print(f"  {col:40s}: {pct:6.2f}%{flag}")

# Price stats
print("\n--- Price statistics ---")
price_stats = con.execute(f"""
    SELECT 
        MIN(price), MAX(price), AVG(price), STDDEV(price),
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY price),
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price),
        SUM(CASE WHEN price = 0 THEN 1 ELSE 0 END),
        SUM(CASE WHEN price < 0 THEN 1 ELSE 0 END),
        COUNT(price)
    FROM read_parquet('{PARQUET}')
    WHERE price IS NOT NULL
""").fetchone()
print(f"  min={price_stats[0]}, max={price_stats[1]}, avg={price_stats[2]:.4f}, std={price_stats[3]:.4f}")
print(f"  Q25={price_stats[4]}, median={price_stats[5]}, Q75={price_stats[6]}")
print(f"  zeros={price_stats[7]:,}, negatives={price_stats[8]:,}, non-null count={price_stats[9]:,}")

# Size stats
print("\n--- Size statistics ---")
size_stats = con.execute(f"""
    SELECT 
        MIN(size), MAX(size), AVG(size), STDDEV(size),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY size),
        SUM(CASE WHEN size = 0 THEN 1 ELSE 0 END),
        SUM(CASE WHEN size < 0 THEN 1 ELSE 0 END),
        COUNT(size)
    FROM read_parquet('{PARQUET}')
    WHERE size IS NOT NULL
""").fetchone()
print(f"  min={size_stats[0]}, max={size_stats[1]}, avg={size_stats[2]:.4f}, std={size_stats[3]:.4f}")
print(f"  median={size_stats[4]}, zeros={size_stats[5]:,}, negatives={size_stats[6]:,}, non-null count={size_stats[7]:,}")

# Best bid/ask stats
print("\n--- Best bid/ask statistics ---")
ba_stats = con.execute(f"""
    SELECT 
        MIN(best_bid), MAX(best_bid), AVG(best_bid), STDDEV(best_bid), COUNT(best_bid),
        MIN(best_ask), MAX(best_ask), AVG(best_ask), STDDEV(best_ask), COUNT(best_ask)
    FROM read_parquet('{PARQUET}')
""").fetchone()
print(f"  best_bid: min={ba_stats[0]}, max={ba_stats[1]}, avg={ba_stats[2]:.4f}, std={ba_stats[3]:.4f}, count={ba_stats[4]:,}")
print(f"  best_ask: min={ba_stats[5]}, max={ba_stats[6]}, avg={ba_stats[7]:.4f}, std={ba_stats[8]:.4f}, count={ba_stats[9]:,}")

# Spread analysis
print("\n--- Spread (best_ask - best_bid) ---")
spread = con.execute(f"""
    SELECT 
        MIN(best_ask - best_bid), MAX(best_ask - best_bid), 
        AVG(best_ask - best_bid), STDDEV(best_ask - best_bid),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY (best_ask - best_bid)),
        SUM(CASE WHEN best_ask < best_bid THEN 1 ELSE 0 END) as crossed
    FROM read_parquet('{PARQUET}')
    WHERE best_bid IS NOT NULL AND best_ask IS NOT NULL
""").fetchone()
print(f"  min={spread[0]}, max={spread[1]}, avg={spread[2]:.4f}, std={spread[3]:.4f}")
print(f"  median={spread[4]}, crossed_markets(ask<bid)={spread[5]:,}")

# Fee rates
print("\n--- Fee rate distribution ---")
fees = con.execute(f"""
    SELECT fee_rate_bps, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    WHERE fee_rate_bps IS NOT NULL
    GROUP BY fee_rate_bps
    ORDER BY cnt DESC
    LIMIT 10
""").fetchall()
for f in fees:
    print(f"  {f[0]} bps: {f[1]:,}")

# Temporal gaps for top markets
print("\n--- Granularidad temporal (top 5 markets) ---")
top5_markets = con.execute(f"""
    SELECT asset_id, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY asset_id
    ORDER BY cnt DESC
    LIMIT 5
""").fetchall()

for asset in top5_markets:
    aid = asset[0]
    gap_stats = con.execute(f"""
        WITH ordered AS (
            SELECT timestamp, 
                   LAG(timestamp) OVER (ORDER BY timestamp) as prev_ts
            FROM read_parquet('{PARQUET}')
            WHERE asset_id = '{aid}'
        )
        SELECT 
            MIN(DATEDIFF('second', prev_ts, timestamp)) as min_gap_sec,
            MAX(DATEDIFF('second', prev_ts, timestamp)) as max_gap_sec,
            AVG(DATEDIFF('second', prev_ts, timestamp)) as avg_gap_sec,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY DATEDIFF('second', prev_ts, timestamp)) as median_gap_sec
        FROM ordered
        WHERE prev_ts IS NOT NULL
    """).fetchone()
    print(f"\n  asset_id={aid[:30]}...")
    print(f"    records={asset[1]:,}, gap_sec: min={gap_stats[0]}, max={gap_stats[1]}, avg={gap_stats[2]:.1f}, median={gap_stats[3]}")

print("\n--- Bids/Asks columns (JSON inspection) ---")
bids_sample = con.execute(f"""
    SELECT bids, asks
    FROM read_parquet('{PARQUET}')
    WHERE bids IS NOT NULL AND asks IS NOT NULL
    LIMIT 3
""").fetchall()
for i, row in enumerate(bids_sample):
    bids_str = str(row[0])[:200] if row[0] else "NULL"
    asks_str = str(row[1])[:200] if row[1] else "NULL"
    print(f"  Row {i}: bids={bids_str}")
    print(f"          asks={asks_str}")

print("\nScript v2 completado.")
