"""
Script to run the final metrics on the consolidated crypto parquet.
"""
import duckdb
import sys
import io
import json
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PARQUET_DIR = r"D:\Proyectos\polystudio\polystudio\data\parquet"
CONSOLIDATED_PARQUET = os.path.join(PARQUET_DIR, "polymarket_crypto_consolidated.parquet")
KNOWN_MARKETS_FILE = os.path.join(PARQUET_DIR, "known_crypto_markets.json")

resolved_cache = {}
if os.path.exists(KNOWN_MARKETS_FILE):
    with open(KNOWN_MARKETS_FILE, 'r') as f:
        resolved_cache = json.load(f)

con = duckdb.connect()

print("=" * 80)
print("3. ANÁLISIS TICK-BY-TICK GLOBAL (Todos los parquets crypto consolidados)")
print("=" * 80)

# Global stats over the consolidated parquet. Fixed the window function error.
stats_query = f"""
    WITH market_data AS (
        SELECT 
            CAST(market AS VARCHAR) as cid,
            timestamp,
            (best_bid + best_ask) / 2.0 AS mid_price
        FROM read_parquet('{CONSOLIDATED_PARQUET}')
        WHERE event_type = 'price_change'
          AND best_bid IS NOT NULL AND best_ask IS NOT NULL
    ),
    returns_data AS (
        SELECT 
            cid,
            timestamp,
            mid_price,
            LAG(mid_price) OVER (PARTITION BY cid ORDER BY timestamp ASC) AS prev_price,
            (mid_price - LAG(mid_price) OVER (PARTITION BY cid ORDER BY timestamp ASC)) AS price_diff
        FROM market_data
    ),
    returns_with_lag AS (
        SELECT 
            price_diff,
            LAG(price_diff) OVER (PARTITION BY cid ORDER BY timestamp ASC) as prev_diff
        FROM returns_data
        WHERE prev_price IS NOT NULL
    )
    SELECT 
        COUNT(*) as total_ticks,
        SUM(CASE WHEN price_diff > 0 THEN 1 ELSE 0 END) as up_ticks,
        SUM(CASE WHEN price_diff < 0 THEN 1 ELSE 0 END) as down_ticks,
        SUM(CASE WHEN price_diff = 0 THEN 1 ELSE 0 END) as flat_ticks,
        STDDEV(price_diff) as vol,
        CORR(price_diff, prev_diff) as autocorr
    FROM returns_with_lag
"""

res = con.execute(stats_query).fetchone()

if res and res[0] > 0:
    total, ups, downs, flats, vol, autocorr = res
    moves = ups + downs
    up_pct = (ups / moves * 100) if moves > 0 else 0
    down_pct = (downs / moves * 100) if moves > 0 else 0
    
    print("\n--- MÉTRICAS GLOBALES ACUMULADAS (22.7 Millones de ticks) ---")
    print(f"  Total Ticks Analizados: {total:,}")
    print(f"  Up Ticks:   {ups:,} ({up_pct:.2f}%)")
    print(f"  Down Ticks: {downs:,} ({down_pct:.2f}%)")
    print(f"  Flat Ticks: {flats:,}")
    print(f"  Volatilidad Promedio (StdDev): {vol:.6f}")
    print(f"  Autocorrelación Lag-1 Global: {autocorr:.4f}")
    print(f"  Win Rate Naive (Siempre Sube): {up_pct:.2f}%")

print("\n" + "=" * 80)
print("4. TOP 10 MERCADOS MÁS ACTIVOS EN EL DATASET COMPLETO")
print("=" * 80)

top_crypto = con.execute(f"""
    SELECT CAST(market AS VARCHAR) as cid, COUNT(*) as cnt
    FROM read_parquet('{CONSOLIDATED_PARQUET}')
    GROUP BY cid
    ORDER BY cnt DESC
    LIMIT 10
""").fetchall()

for cid, cnt in top_crypto:
    name = resolved_cache.get(cid, {}).get('question', 'Unknown')
    print(f"\nMercado: {name} ({cnt:,} ticks)")
    
    mq = f"""
        WITH market_data AS (
            SELECT timestamp, (best_bid + best_ask) / 2.0 AS mid_price
            FROM read_parquet('{CONSOLIDATED_PARQUET}')
            WHERE CAST(market AS VARCHAR) = '{cid}'
              AND event_type = 'price_change' AND best_bid IS NOT NULL AND best_ask IS NOT NULL
        ),
        returns_data AS (
            SELECT 
                timestamp,
                mid_price - LAG(mid_price) OVER (ORDER BY timestamp ASC) AS price_diff
            FROM market_data
        ),
        returns_lag AS (
            SELECT 
                price_diff,
                LAG(price_diff) OVER (ORDER BY timestamp ASC) as prev_diff
            FROM returns_data
            WHERE price_diff IS NOT NULL
        )
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN price_diff > 0 THEN 1 ELSE 0 END) as up_ticks,
            SUM(CASE WHEN price_diff < 0 THEN 1 ELSE 0 END) as down_ticks,
            CORR(price_diff, prev_diff) as autocorr
        FROM returns_lag
    """
    m_res = con.execute(mq).fetchone()
    if m_res and m_res[0]:
        t, u, d, ac = m_res
        mvs = u + d
        up_p = (u/mvs*100) if mvs > 0 else 0
        print(f"  Up: {u:,} ({up_p:.1f}%), Down: {d:,}")
        print(f"  Autocorr: {ac:.4f}" if ac else "  Autocorr: N/A")

print("\nAnálisis Multiparquet completado.")
