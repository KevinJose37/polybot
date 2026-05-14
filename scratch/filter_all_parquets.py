"""
Script to filter ALL parquet files for crypto markets and perform WR analysis.
It dynamically identifies crypto condition_ids across all files via the API,
saves a consolidated filtered parquet, and runs the microstructural analysis.
"""
import duckdb
import os
import sys
import glob
import urllib.request
import json
import time

# Asegurar encoding correcto
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PARQUET_DIR = r"D:\Proyectos\polystudio\polystudio\data\parquet"
# List all original parquets (exclude the ones we already filtered)
all_parquets = glob.glob(os.path.join(PARQUET_DIR, "polymarket_orderbook_2026-*.parquet"))

CONSOLIDATED_PARQUET = os.path.join(PARQUET_DIR, "polymarket_crypto_consolidated.parquet")
KNOWN_MARKETS_FILE = os.path.join(PARQUET_DIR, "known_crypto_markets.json")

con = duckdb.connect()
con.execute("SET memory_limit='2GB'")

print("=" * 80)
print(f"1. ANALIZANDO {len(all_parquets)} ARCHIVOS PARQUET")
print("=" * 80)

# Build a view of all parquets
files_str = ", ".join([f"'{f}'" for f in all_parquets])
con.execute(f"CREATE OR REPLACE VIEW all_data AS SELECT * FROM read_parquet([{files_str}])")

print("Calculando mercados más activos en todo el dataset...")
top_markets = con.execute("""
    SELECT CAST(market AS VARCHAR) as cid, COUNT(*) as cnt
    FROM all_data
    GROUP BY cid
    HAVING cnt > 10000
    ORDER BY cnt DESC
    LIMIT 1000
""").fetchall()

print(f"Top mercados encontrados (>10k ticks): {len(top_markets)}")

# Load or resolve condition_ids via API
crypto_keywords = ['bitcoin', 'btc', 'xbt', 'ethereum', 'eth', 'solana', 'sol', 
                   'crypto', 'doge', 'xrp', 'ada', 'avax', 'matic', 'link']

crypto_cids = set()
resolved_cache = {}

if os.path.exists(KNOWN_MARKETS_FILE):
    with open(KNOWN_MARKETS_FILE, 'r') as f:
        resolved_cache = json.load(f)
        for cid, info in resolved_cache.items():
            if info.get('is_crypto', False):
                crypto_cids.add(cid)
    print(f"Caché cargado: {len(resolved_cache)} mercados resueltos, {len(crypto_cids)} son crypto.")

to_resolve = [m[0] for m in top_markets if m[0] not in resolved_cache]

if to_resolve:
    print(f"\nResolviendo {len(to_resolve)} nuevos mercados via Polymarket API...")
    errors = 0
    for i, cid in enumerate(to_resolve):
        if i % 50 == 0 and i > 0:
            print(f"  Progreso: {i}/{len(to_resolve)}...")
        
        is_crypto = False
        question = "Unknown"
        try:
            url = f"https://clob.polymarket.com/markets/{cid}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                question = data.get('question', data.get('description', 'Unknown'))
                q_lower = question.lower()
                is_crypto = any(kw in q_lower for kw in crypto_keywords)
                # Exclude weather explicitly
                if "temperature" in q_lower or "weather" in q_lower:
                    is_crypto = False
        except Exception as e:
            errors += 1
            # Try gamma
            try:
                url = f"https://gamma-api.polymarket.com/markets?condition_ids={cid}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = json.loads(resp.read().decode())
                    if data and len(data) > 0:
                        question = data[0].get('question', 'Unknown')
                        q_lower = question.lower()
                        is_crypto = any(kw in q_lower for kw in crypto_keywords)
                        if "temperature" in q_lower or "weather" in q_lower:
                            is_crypto = False
            except:
                pass
                
        resolved_cache[cid] = {'question': question, 'is_crypto': is_crypto}
        if is_crypto:
            crypto_cids.add(cid)
            
    print(f"API Resolution errors: {errors}")
    with open(KNOWN_MARKETS_FILE, 'w') as f:
        json.dump(resolved_cache, f, indent=2)

print(f"\nTotal de mercados crypto identificados: {len(crypto_cids)}")

if not crypto_cids:
    print("No se encontraron mercados crypto. Saliendo.")
    sys.exit(0)

print("\n" + "=" * 80)
print("2. FILTRANDO Y CONSOLIDANDO PARQUETS")
print("=" * 80)

cids_str = ", ".join([f"'{cid}'" for cid in crypto_cids])

print(f"Exportando a {CONSOLIDATED_PARQUET}...")
t0 = time.time()
export_query = f"""
    COPY (
        SELECT * FROM all_data
        WHERE CAST(market AS VARCHAR) IN ({cids_str})
    ) TO '{CONSOLIDATED_PARQUET}' (FORMAT PARQUET, COMPRESSION ZSTD);
"""
con.execute(export_query)
t1 = time.time()
print(f"Exportación completada en {t1-t0:.1f} segundos.")

new_size_mb = os.path.getsize(CONSOLIDATED_PARQUET) / (1024 * 1024)
row_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{CONSOLIDATED_PARQUET}')").fetchone()[0]

print(f"Tamaño del parquet consolidado: {new_size_mb:.2f} MB")
print(f"Filas totales (puro crypto): {row_count:,}")

print("\n" + "=" * 80)
print("3. ANÁLISIS TICK-BY-TICK GLOBAL (Todos los parquets crypto)")
print("=" * 80)

# Global stats over the consolidated parquet
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
    )
    SELECT 
        COUNT(*) as total_ticks,
        SUM(CASE WHEN price_diff > 0 THEN 1 ELSE 0 END) as up_ticks,
        SUM(CASE WHEN price_diff < 0 THEN 1 ELSE 0 END) as down_ticks,
        SUM(CASE WHEN price_diff = 0 THEN 1 ELSE 0 END) as flat_ticks,
        STDDEV(price_diff) as vol,
        CORR(price_diff, LAG(price_diff) OVER (PARTITION BY cid ORDER BY timestamp ASC)) as autocorr
    FROM returns_data
    WHERE prev_price IS NOT NULL
"""
res = con.execute(stats_query).fetchone()

if res and res[0] > 0:
    total, ups, downs, flats, vol, autocorr = res
    moves = ups + downs
    up_pct = (ups / moves * 100) if moves > 0 else 0
    down_pct = (downs / moves * 100) if moves > 0 else 0
    
    print("\n--- MÉTRICAS GLOBALES ACUMULADAS ---")
    print(f"  Total Ticks Analizados: {total:,}")
    print(f"  Up Ticks:   {ups:,} ({up_pct:.2f}%)")
    print(f"  Down Ticks: {downs:,} ({down_pct:.2f}%)")
    print(f"  Flat Ticks: {flats:,}")
    print(f"  Volatilidad Promedio (StdDev): {vol:.6f}")
    print(f"  Autocorrelación Lag-1 Global: {autocorr:.4f}")
    print(f"  Win Rate Naive (Siempre Sube): {up_pct:.2f}%")

print("\n" + "=" * 80)
print("4. TOP 5 MERCADOS MÁS ACTIVOS EN EL DATASET COMPLETO")
print("=" * 80)

top_crypto = con.execute(f"""
    SELECT CAST(market AS VARCHAR) as cid, COUNT(*) as cnt
    FROM read_parquet('{CONSOLIDATED_PARQUET}')
    GROUP BY cid
    ORDER BY cnt DESC
    LIMIT 5
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
                mid_price - LAG(mid_price) OVER (ORDER BY timestamp ASC) AS price_diff
            FROM market_data
        )
        SELECT 
            SUM(CASE WHEN price_diff > 0 THEN 1 ELSE 0 END) as up_ticks,
            SUM(CASE WHEN price_diff < 0 THEN 1 ELSE 0 END) as down_ticks,
            CORR(price_diff, LAG(price_diff) OVER (ORDER BY timestamp ASC)) as autocorr
        FROM returns_data
        WHERE price_diff IS NOT NULL
    """
    m_res = con.execute(mq).fetchone()
    if m_res:
        u, d, ac = m_res
        mvs = u + d
        up_p = (u/mvs*100) if mvs > 0 else 0
        print(f"  Up: {u:,} ({up_p:.1f}%), Down: {d:,}")
        print(f"  Autocorr: {ac:.4f}")

print("\nAnálisis Multiparquet completado.")
