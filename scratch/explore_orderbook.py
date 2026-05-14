"""
Exhaustive exploration of polymarket_orderbook parquet dataset using DuckDB.
Memory-safe: never loads full dataset into RAM.
"""
import duckdb
import json

PARQUET = r"D:\Proyectos\polystudio\polystudio\data\parquet\polymarket_orderbook_2026-05-13T22.parquet"

con = duckdb.connect()

# Limit DuckDB memory to avoid OOM
con.execute("SET memory_limit='512MB'")

print("=" * 80)
print("PASO 1 — INVENTARIO DEL DATASET")
print("=" * 80)

# 1a. Schema
print("\n--- Schema (columnas, tipos) ---")
schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{PARQUET}')").fetchall()
for col in schema:
    print(f"  {col[0]:40s} | {col[1]:20s} | null={col[2]}")

# 1b. Row count
row_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{PARQUET}')").fetchone()[0]
print(f"\nTotal filas: {row_count:,}")

# 1c. Column names for reference
col_names = [c[0] for c in schema]
print(f"\nColumnas ({len(col_names)}): {col_names}")

# 1d. First 5 rows (safe peek)
print("\n--- Primeras 5 filas ---")
sample = con.execute(f"SELECT * FROM read_parquet('{PARQUET}') LIMIT 5").fetchdf()
print(sample.to_string())

# 1e. Detect timestamp columns and get ranges
print("\n--- Rango temporal ---")
ts_candidates = [c[0] for c in schema if 'time' in c[0].lower() or 'date' in c[0].lower() or 'stamp' in c[0].lower() or 'created' in c[0].lower() or 'updated' in c[0].lower()]
if not ts_candidates:
    # Also check for epoch-like columns
    ts_candidates = [c[0] for c in schema if c[1] in ('TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'BIGINT', 'INTEGER')]
    print(f"  No obvious timestamp columns found. Candidates by type: {ts_candidates}")

for tc in ts_candidates:
    try:
        minmax = con.execute(f"SELECT MIN(\"{tc}\"), MAX(\"{tc}\") FROM read_parquet('{PARQUET}')").fetchone()
        print(f"  {tc}: min={minmax[0]}, max={minmax[1]}")
    except Exception as e:
        print(f"  {tc}: error - {e}")

print("\n" + "=" * 80)
print("PASO 2 — IDENTIFICACIÓN DE MERCADOS")
print("=" * 80)

# 2a. Find market-identifying columns
print("\n--- Valores únicos en columnas clave ---")
# Check which columns might identify markets
for candidate in ['market', 'market_slug', 'market_id', 'question', 'token_id', 'asset_id', 'condition_id', 'slug', 'name', 'symbol', 'ticker']:
    if candidate in col_names:
        nunique = con.execute(f"SELECT COUNT(DISTINCT \"{candidate}\") FROM read_parquet('{PARQUET}')").fetchone()[0]
        print(f"  {candidate}: {nunique:,} valores únicos")
        if nunique <= 200:
            vals = con.execute(f"SELECT DISTINCT \"{candidate}\" FROM read_parquet('{PARQUET}') ORDER BY 1 LIMIT 200").fetchall()
            print(f"    Valores: {[v[0] for v in vals]}")

# 2b. Search for crypto-related markets
print("\n--- Buscando mercados crypto ---")
crypto_keywords = ['bitcoin', 'btc', 'xbt', 'ethereum', 'eth', 'solana', 'sol', 'crypto', 'coin', 'price']

# Try different text columns to find crypto markets
text_cols = [c[0] for c in schema if c[1] in ('VARCHAR', 'TEXT', 'STRING')]
print(f"  Columnas de texto: {text_cols}")

for col in text_cols:
    for kw in crypto_keywords:
        try:
            count = con.execute(f"""
                SELECT COUNT(*) FROM read_parquet('{PARQUET}') 
                WHERE LOWER(\"{col}\") LIKE '%{kw}%'
            """).fetchone()[0]
            if count > 0:
                # Get distinct values
                matches = con.execute(f"""
                    SELECT DISTINCT \"{col}\", COUNT(*) as cnt 
                    FROM read_parquet('{PARQUET}') 
                    WHERE LOWER(\"{col}\") LIKE '%{kw}%'
                    GROUP BY \"{col}\"
                    ORDER BY cnt DESC
                    LIMIT 30
                """).fetchall()
                print(f"\n  [{col}] keyword='{kw}': {count:,} filas")
                for m in matches:
                    print(f"    '{m[0]}': {m[1]:,} registros")
        except Exception as e:
            pass

print("\n" + "=" * 80)
print("PASO 3 — CALIDAD DE DATOS")
print("=" * 80)

# 3a. Null analysis
print("\n--- Porcentaje de NULLs por columna ---")
null_queries = []
for col in col_names:
    null_queries.append(f'ROUND(100.0 * SUM(CASE WHEN "{col}" IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) AS "{col}_null_pct"')

null_result = con.execute(f"""
    SELECT {', '.join(null_queries)} 
    FROM read_parquet('{PARQUET}')
""").fetchone()

for i, col in enumerate(col_names):
    pct = null_result[i]
    flag = " ⚠️ >20%" if pct > 20 else ""
    print(f"  {col:40s}: {pct:6.2f}%{flag}")

# 3b. Numeric stats for price/quantity columns
print("\n--- Stats numéricas para columnas numéricas ---")
numeric_cols = [c[0] for c in schema if c[1] in ('DOUBLE', 'FLOAT', 'DECIMAL', 'INTEGER', 'BIGINT', 'HUGEINT', 'SMALLINT', 'TINYINT')]
for col in numeric_cols[:15]:  # limit to first 15
    try:
        stats = con.execute(f"""
            SELECT 
                MIN("{col}") as min_val,
                MAX("{col}") as max_val,
                AVG("{col}") as avg_val,
                STDDEV("{col}") as std_val,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY "{col}") as median,
                SUM(CASE WHEN "{col}" = 0 THEN 1 ELSE 0 END) as zeros,
                SUM(CASE WHEN "{col}" < 0 THEN 1 ELSE 0 END) as negatives
            FROM read_parquet('{PARQUET}')
        """).fetchone()
        print(f"\n  {col}:")
        print(f"    min={stats[0]}, max={stats[1]}, avg={stats[2]:.4f}, std={stats[3]:.4f}")
        print(f"    median={stats[4]}, zeros={stats[5]:,}, negatives={stats[6]:,}")
    except Exception as e:
        print(f"  {col}: error - {e}")

print("\n\nScript completado exitosamente.")
