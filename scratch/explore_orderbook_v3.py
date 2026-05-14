"""
Exhaustive exploration v3 - Decodes BLOB, completes all 5 steps.
"""
import duckdb
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PARQUET = r"D:\Proyectos\polystudio\polystudio\data\parquet\polymarket_orderbook_2026-05-13T22.parquet"
con = duckdb.connect()
con.execute("SET memory_limit='512MB'")

# ====================================================================
# Decode market BLOB -> condition_id hex string
# ====================================================================
print("=" * 80)
print("MARKET BLOB DECODE")
print("=" * 80)

# The BLOB stores raw bytes of the hex string chars
# BLOB -> VARCHAR conversion
decoded = con.execute(f"""
    SELECT DISTINCT CAST(market AS VARCHAR) as market_cid, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY market_cid
    ORDER BY cnt DESC
    LIMIT 30
""").fetchall()

print(f"\nTop 30 decoded market condition_ids (total unique: 57,373):")
for d in decoded:
    cid = str(d[0])[:66]
    print(f"  {cid} | {d[1]:,} records")

# How many unique markets
n_markets = con.execute(f"""
    SELECT COUNT(DISTINCT CAST(market AS VARCHAR))
    FROM read_parquet('{PARQUET}')
""").fetchone()[0]
print(f"\nTotal unique markets (condition_ids): {n_markets:,}")

# ====================================================================
# PASO 2 - Crypto market identification
# ====================================================================
print("\n" + "=" * 80)
print("PASO 2 - CRYPTO MARKET IDENTIFICATION")
print("=" * 80)

# The dataset is orderbook data - it uses condition_ids and token_ids (asset_id)
# There's no human-readable market name in the dataset itself
# We need to check if the condition_ids map to known Polymarket crypto markets

# Let's look at a different angle: price ranges
# Polymarket prices are 0-1 (probabilities), NOT actual crypto prices
print("\n--- Price distribution by event_type ---")
price_dist = con.execute(f"""
    SELECT event_type,
        MIN(price), MAX(price), AVG(price),
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) as p25,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY price) as p50,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) as p75,
        COUNT(*)
    FROM read_parquet('{PARQUET}')
    WHERE price IS NOT NULL
    GROUP BY event_type
""").fetchall()
for p in price_dist:
    print(f"  {str(p[0]):30s}: min={p[1]}, max={p[2]}, avg={p[3]:.4f}, Q25={p[4]}, med={p[5]}, Q75={p[6]}, n={p[7]:,}")

# Price histogram
print("\n--- Price histogram (0.1 buckets) ---")
hist = con.execute(f"""
    SELECT 
        FLOOR(price * 10) / 10 as price_bucket,
        COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    WHERE price IS NOT NULL
    GROUP BY price_bucket
    ORDER BY price_bucket
""").fetchall()
for h in hist:
    print(f"  [{h[0]:.1f} - {h[0]+0.1:.1f}): {h[1]:>12,}")

# ====================================================================
# PASO 3 - CALIDAD DE DATOS (completion)
# ====================================================================
print("\n" + "=" * 80)
print("PASO 3 - DATA QUALITY")
print("=" * 80)

# Null analysis
print("\n--- NULL percentages ---")
col_names = ['timestamp_received', 'timestamp', 'market', 'event_type', 'asset_id', 
             'bids', 'asks', 'price', 'size', 'side', 'best_bid', 'best_ask', 
             'fee_rate_bps', 'transaction_hash', 'old_tick_size', 'new_tick_size']

for col in col_names:
    pct = con.execute(f"""
        SELECT ROUND(100.0 * SUM(CASE WHEN "{col}" IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2)
        FROM read_parquet('{PARQUET}')
    """).fetchone()[0]
    flag = " ** >20% NULLS **" if pct > 20 else ""
    print(f"  {col:40s}: {pct:6.2f}%{flag}")

# Price stats
print("\n--- Price statistics ---")
price_stats = con.execute(f"""
    SELECT 
        MIN(price), MAX(price), AVG(price)::DECIMAL(9,4), STDDEV(price)::DECIMAL(9,4),
        SUM(CASE WHEN price = 0 THEN 1 ELSE 0 END),
        SUM(CASE WHEN price < 0 THEN 1 ELSE 0 END),
        COUNT(price)
    FROM read_parquet('{PARQUET}')
    WHERE price IS NOT NULL
""").fetchone()
print(f"  min={price_stats[0]}, max={price_stats[1]}, avg={price_stats[2]}, std={price_stats[3]}")
print(f"  zeros={price_stats[4]:,}, negatives={price_stats[5]:,}, non-null={price_stats[6]:,}")

# Size stats  
print("\n--- Size statistics ---")
size_stats = con.execute(f"""
    SELECT 
        MIN(size), MAX(size), AVG(size)::DECIMAL(18,4), STDDEV(size)::DECIMAL(18,4),
        SUM(CASE WHEN size = 0 THEN 1 ELSE 0 END),
        SUM(CASE WHEN size < 0 THEN 1 ELSE 0 END),
        COUNT(size)
    FROM read_parquet('{PARQUET}')
    WHERE size IS NOT NULL
""").fetchone()
print(f"  min={size_stats[0]}, max={size_stats[1]}, avg={size_stats[2]}, std={size_stats[3]}")
print(f"  zeros={size_stats[4]:,}, negatives={size_stats[5]:,}, non-null={size_stats[6]:,}")

# Best bid/ask 
print("\n--- Best bid/ask stats ---")
ba = con.execute(f"""
    SELECT 
        MIN(best_bid), MAX(best_bid), AVG(best_bid)::DECIMAL(9,4), COUNT(best_bid),
        MIN(best_ask), MAX(best_ask), AVG(best_ask)::DECIMAL(9,4), COUNT(best_ask)
    FROM read_parquet('{PARQUET}')
""").fetchone()
print(f"  best_bid: min={ba[0]}, max={ba[1]}, avg={ba[2]}, count={ba[3]:,}")
print(f"  best_ask: min={ba[4]}, max={ba[5]}, avg={ba[6]}, count={ba[7]:,}")

# Spread
print("\n--- Spread (best_ask - best_bid) ---")
spread = con.execute(f"""
    SELECT 
        MIN(best_ask - best_bid), MAX(best_ask - best_bid), 
        AVG(best_ask - best_bid)::DECIMAL(9,4),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY (best_ask - best_bid)),
        SUM(CASE WHEN best_ask < best_bid THEN 1 ELSE 0 END)
    FROM read_parquet('{PARQUET}')
    WHERE best_bid IS NOT NULL AND best_ask IS NOT NULL
""").fetchone()
print(f"  min={spread[0]}, max={spread[1]}, avg={spread[2]}, median={spread[3]}")
print(f"  crossed_markets(ask<bid)={spread[4]:,}")

# Fee rates
print("\n--- Fee rate distribution ---")
fees = con.execute(f"""
    SELECT fee_rate_bps, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    WHERE fee_rate_bps IS NOT NULL
    GROUP BY fee_rate_bps
    ORDER BY cnt DESC
""").fetchall()
for f in fees:
    print(f"  {f[0]} bps: {f[1]:,}")

# Bids/Asks JSON inspection
print("\n--- Bids/Asks columns (sample) ---")
bids_sample = con.execute(f"""
    SELECT bids, asks, event_type
    FROM read_parquet('{PARQUET}')
    WHERE bids IS NOT NULL AND bids != '' AND asks IS NOT NULL AND asks != ''
    LIMIT 3
""").fetchall()
if bids_sample:
    for i, row in enumerate(bids_sample):
        print(f"  Row {i} (type={row[2]}):")
        print(f"    bids={str(row[0])[:300]}")
        print(f"    asks={str(row[1])[:300]}")
else:
    # Check if bids/asks are mostly null
    print("  No rows with non-null, non-empty bids AND asks found.")
    ba_check = con.execute(f"""
        SELECT 
            SUM(CASE WHEN bids IS NOT NULL AND bids != '' THEN 1 ELSE 0 END) as bids_notnull,
            SUM(CASE WHEN asks IS NOT NULL AND asks != '' THEN 1 ELSE 0 END) as asks_notnull
        FROM read_parquet('{PARQUET}')
    """).fetchone()
    print(f"  bids non-null non-empty: {ba_check[0]:,}")
    print(f"  asks non-null non-empty: {ba_check[1]:,}")

# Event type breakdown with useful fields
print("\n--- Fields availability by event_type ---")
for evt_type in ['price_change', 'last_trade_price', 'book', 'tick_size_change']:
    cnt = con.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{PARQUET}')
        WHERE event_type = '{evt_type}'
    """).fetchone()[0]
    if cnt > 0:
        print(f"\n  event_type='{evt_type}' ({cnt:,} rows):")
        sample = con.execute(f"""
            SELECT * FROM read_parquet('{PARQUET}')
            WHERE event_type = '{evt_type}'
            LIMIT 2
        """).fetchdf()
        # Show non-null columns
        for col in col_names:
            notnull = con.execute(f"""
                SELECT COUNT("{col}") FROM read_parquet('{PARQUET}')
                WHERE event_type = '{evt_type}'
            """).fetchone()[0]
            pct = 100.0 * notnull / cnt if cnt > 0 else 0
            if pct > 0:
                print(f"    {col}: {pct:.1f}% non-null")

# Temporal granularity for top assets
print("\n--- Temporal granularity (top 5 assets, price_change events) ---")
top5 = con.execute(f"""
    SELECT asset_id, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    WHERE event_type = 'price_change'
    GROUP BY asset_id
    ORDER BY cnt DESC
    LIMIT 5
""").fetchall()

for asset in top5:
    aid = asset[0]
    gap = con.execute(f"""
        WITH ordered AS (
            SELECT timestamp, 
                   LAG(timestamp) OVER (ORDER BY timestamp) as prev_ts
            FROM read_parquet('{PARQUET}')
            WHERE asset_id = '{aid}' AND event_type = 'price_change'
        )
        SELECT 
            MIN(DATEDIFF('second', prev_ts, timestamp)),
            MAX(DATEDIFF('second', prev_ts, timestamp)),
            AVG(DATEDIFF('second', prev_ts, timestamp))::DECIMAL(10,1),
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY DATEDIFF('second', prev_ts, timestamp))
        FROM ordered
        WHERE prev_ts IS NOT NULL
    """).fetchone()
    ts_range = con.execute(f"""
        SELECT MIN(timestamp)::VARCHAR, MAX(timestamp)::VARCHAR
        FROM read_parquet('{PARQUET}')
        WHERE asset_id = '{aid}'
    """).fetchone()
    print(f"\n  asset_id={aid[:40]}...")
    print(f"    records={asset[1]:,}")
    print(f"    time range: {ts_range[0]} to {ts_range[1]}")
    print(f"    gap_sec: min={gap[0]}, max={gap[1]}, avg={gap[2]}, median={gap[3]}")

# ====================================================================
# Understanding the data: Polymarket orderbook context
# ====================================================================
print("\n" + "=" * 80)
print("ANALYSIS CONTEXT")
print("=" * 80)

# Verify: timestamp ranges - note timestamp_received is all within 1 hour
# but timestamp spans 2 weeks - this means the parquet is a SNAPSHOT
# that collected recent historical data
print("\n--- Timestamp analysis ---")
ts_detail = con.execute(f"""
    SELECT 
        DATEDIFF('hour', MIN(timestamp), MAX(timestamp)) as ts_hours,
        DATEDIFF('hour', MIN(timestamp_received), MAX(timestamp_received)) as rcv_hours,
        MIN(timestamp)::VARCHAR as ts_min,
        MAX(timestamp)::VARCHAR as ts_max,
        MIN(timestamp_received)::VARCHAR as rcv_min,
        MAX(timestamp_received)::VARCHAR as rcv_max
    FROM read_parquet('{PARQUET}')
""").fetchone()
print(f"  timestamp spans {ts_detail[0]} hours: {ts_detail[2]} to {ts_detail[3]}")
print(f"  timestamp_received spans {ts_detail[1]} hours: {ts_detail[4]} to {ts_detail[5]}")

# Distribution of timestamps by day
print("\n--- Records per day (by timestamp) ---")
daily = con.execute(f"""
    SELECT DATE_TRUNC('day', timestamp)::DATE::VARCHAR as day, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    GROUP BY day
    ORDER BY day
""").fetchall()
for d in daily:
    print(f"  {d[0]}: {d[1]:>10,}")

# Distribution by hour for the most recent day
print("\n--- Records per hour on 2026-05-13 ---")
hourly = con.execute(f"""
    SELECT EXTRACT(HOUR FROM timestamp) as hr, COUNT(*) as cnt
    FROM read_parquet('{PARQUET}')
    WHERE DATE_TRUNC('day', timestamp) = '2026-05-13'
    GROUP BY hr
    ORDER BY hr
""").fetchall()
for h in hourly:
    print(f"  Hour {int(h[0]):02d}: {h[1]:>10,}")

# Market size distribution
print("\n--- Market size distribution (records per market) ---")
market_sizes = con.execute(f"""
    SELECT 
        CASE 
            WHEN cnt < 100 THEN '<100'
            WHEN cnt < 1000 THEN '100-999'
            WHEN cnt < 10000 THEN '1K-9.9K'
            WHEN cnt < 50000 THEN '10K-49.9K'
            WHEN cnt < 100000 THEN '50K-99.9K'
            ELSE '100K+'
        END as bucket,
        COUNT(*) as n_markets,
        SUM(cnt) as total_records
    FROM (
        SELECT CAST(market AS VARCHAR) as mkt, COUNT(*) as cnt
        FROM read_parquet('{PARQUET}')
        GROUP BY mkt
    )
    GROUP BY bucket
    ORDER BY MIN(cnt)
""").fetchall()
for ms in market_sizes:
    print(f"  {ms[0]:15s}: {ms[1]:,} markets, {ms[2]:,} total records")

print("\nScript v3 completado.")
