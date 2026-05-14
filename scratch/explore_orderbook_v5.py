"""
Exploration v5 - Predictability analysis (Step 4) on strictly filtered crypto markets.
"""
import duckdb
import sys
import io
import json
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PARQUET = r"D:\Proyectos\polystudio\polystudio\data\parquet\polymarket_orderbook_2026-05-13T22.parquet"
con = duckdb.connect()
con.execute("SET memory_limit='1GB'")

print("=" * 80)
print("PASO 4 — ANÁLISIS DE PREDICTIBILIDAD BÁSICA")
print("=" * 80)

# We identified specific crypto markets in the previous run. Let's hardcode the confirmed crypto condition_ids
# based on the output of v4 to ensure we only analyze actual crypto markets.
crypto_markets = {
    '0x21ad7c19fb5512f3f2b2797eeec74f9dc1e88eb06cc3a5ce16e4a131ba434015': 'Bitcoin Up or Down - May 13, 6PM ET',
    '0xf4af07fa18c30fd68c12a8bd317a88d211b534c0ceb8e364e035a2fc60ab4ef6': 'Bitcoin Up or Down - May 13, 6:25PM-6:30PM ET',
    '0xd8605cd27e30445d34228dd1dfa6d04aa7686c894421c66474f848f4ab49784d': 'Bitcoin Up or Down - May 13, 6:50PM-6:55PM ET',
    '0xdeffb7af0704433254dd5ade86d7ad7d599d3f4a2b069b21c9f707b5b90e144b': 'Bitcoin Up or Down - May 13, 6:00PM-6:15PM ET',
    '0x17908d6f20a4a6e0e26c773a62a40e7bfe9f097689d0962764870b76268cb568': 'Bitcoin Up or Down - May 13, 6:30PM-6:35PM ET',
    '0x96cde3ba9e168ba76f1ee9823fb4c81efcb8e75a7b803a4712c5f69f39128644': 'Bitcoin Up or Down - May 13, 6:00PM-6:05PM ET',
    '0x068a32c7d9a6c935c72fc713d9bd56721add97a2cb189afebad56775f0a0d4b2': 'Bitcoin Up or Down - May 13, 6:45PM-7:00PM ET',
    '0x93791003fca0dc13ce4a889ce418b55ada26ebd0b3684a0dccf6bc62141d50c1': 'Bitcoin Up or Down - May 13, 6:55PM-7:00PM ET',
    '0xa7ac6ee85befcc8d70b1c1ff4400a93fd4f2dc18dbb8e19bb5a1b3c95e00b875': 'Ethereum Up or Down - May 13, 6PM ET',
    '0xfa906289d1ced8d2974646e3e44de0fe313b98c19a93f41249964528d25d19bd': 'Bitcoin Up or Down - May 13, 6:35PM-6:40PM ET',
    '0xb9aa7a113f68d2465ed4e64a14699e04f3efa6711ef05dfef610adba0f9302ca': 'Bitcoin Up or Down - May 13, 6:30PM-6:45PM ET',
    '0x984ae49c8b123a1d08406210f3e26d8b7c2743dbce9f705b15b369c9b5f39641': 'Bitcoin Up or Down - May 13, 6:45PM-6:50PM ET',
    '0x97b1ec1a36d7027c88842f72e891e5df8b7135e6162137cd21dc725f48f435fb': 'Bitcoin Up or Down - May 13, 6:20PM-6:25PM ET',
    '0x642d684164be26798b902c7a8e49ca7616d67a127a6f23f86e9e8f6681720d29': 'Ethereum Up or Down - May 13, 6:30PM-6:45PM ET',
    '0xc9be06dc703aba30b01614e9e79fa239809c44766d11b3b24fdbb9101d2cc1f0': 'Ethereum Up or Down - May 13, 6:45PM-7:00PM ET',
    '0x11633a9ef89ce0d5bd62ac605ce6f30d9e08a2872bc0e5728362cd998b4c0262': 'Solana Up or Down - May 13, 6:00PM-6:15PM ET',
    '0x72434d4ee35db13abb0baec947b9a2602370aa74d538f8fbf52c3c98dc66723b': 'XRP Up or Down - May 13, 6PM ET',
    '0x827cfa5e0198fd2dcd62309362143591e7035f8e5603a1158d689620edb550dd': 'Will the price of Bitcoin be above $78,000 on May 14?',
    '0x705c572a186b3a2084bf3d32589f2954224b35e236cecc0090f6b3eab6f63458': 'Solana Up or Down - May 13, 6PM ET',
    '0x7a1746d81ccd64fefc5b4bf3844934372d86ecc77adba0fbc16b8b7ed66fc885': 'Ethereum Up or Down - May 13, 6:00PM-6:15PM ET',
    '0x5f8ce9f66d02dc7ab5808d88af8113009393248383e7426166ce52eb3212c019': 'Will the price of Bitcoin be between $76,000 and $78,000 on May 14?',
    '0xf07353172d4e73c2b51c52c6274c73434391316b2a4d3f3f26ad2a39b343361e': 'Will the price of Bitcoin be above $76,000 on May 15?'
}

cids_str = ", ".join([f"'{cid}'" for cid in crypto_markets.keys()])

print(f"Analyzing {len(crypto_markets)} verified crypto markets...")

for cid, name in crypto_markets.items():
    print(f"\n--- Market: {name} ---")
    
    # 1. Obtenemos la serie temporal de precios (usando best_bid + best_ask / 2 como mid price)
    # y miramos los retornos.
    
    # Solo tomamos event_type='price_change' para simplificar, ordenado por timestamp
    # DuckDB soporta lag()
    query = f"""
        WITH market_data AS (
            SELECT 
                timestamp,
                price,
                (best_bid + best_ask) / 2.0 AS mid_price,
                asset_id
            FROM read_parquet('{PARQUET}')
            WHERE CAST(market AS VARCHAR) = '{cid}'
              AND event_type = 'price_change'
              AND best_bid IS NOT NULL AND best_ask IS NOT NULL
            ORDER BY timestamp ASC
        ),
        returns_data AS (
            SELECT 
                timestamp,
                mid_price,
                LAG(mid_price) OVER (ORDER BY timestamp ASC) AS prev_price,
                (mid_price - LAG(mid_price) OVER (ORDER BY timestamp ASC)) AS price_diff
            FROM market_data
        )
        SELECT 
            COUNT(*) as total_ticks,
            SUM(CASE WHEN price_diff > 0 THEN 1 ELSE 0 END) as up_ticks,
            SUM(CASE WHEN price_diff < 0 THEN 1 ELSE 0 END) as down_ticks,
            SUM(CASE WHEN price_diff = 0 THEN 1 ELSE 0 END) as flat_ticks,
            STDDEV(price_diff) as volatility_stddev
        FROM returns_data
        WHERE prev_price IS NOT NULL
    """
    try:
        res = con.execute(query).fetchone()
        if res and res[0] > 0:
            total, ups, downs, flats, stddev = res
            
            # Balance
            moves = ups + downs
            if moves > 0:
                up_pct = (ups / moves) * 100
                down_pct = (downs / moves) * 100
            else:
                up_pct = down_pct = 0
            
            print(f"  Movimientos Totales: {total:,}")
            print(f"  Up Ticks:   {ups:,} ({up_pct:.1f}% de mov. direccionales)")
            print(f"  Down Ticks: {downs:,} ({down_pct:.1f}% de mov. direccionales)")
            print(f"  Flat Ticks: {flats:,}")
            print(f"  Sesgo Direccional: {'Up' if up_pct > 52 else 'Down' if down_pct > 52 else 'Balanceado'}")
            print(f"  Volatilidad (StdDev retornos): {stddev:.6f}" if stddev else "  Volatilidad: N/A")
            
            # WR Naive Baseline
            # Si siempre predicimos "sube" la prob. es up_pct, asumiendo que ignoramos flats.
            print(f"  WR Naive Baseline (Siempre 'Sube'): {up_pct:.2f}%")
            
            # Autocorrelación serial de retornos
            # DuckDB CORR() function
            ac_query = f"""
                WITH returns_data AS (
                    SELECT 
                        (best_bid + best_ask) / 2.0 AS mid_price,
                        ((best_bid + best_ask) / 2.0) - LAG((best_bid + best_ask) / 2.0) OVER (ORDER BY timestamp ASC) AS ret
                    FROM read_parquet('{PARQUET}')
                    WHERE CAST(market AS VARCHAR) = '{cid}'
                      AND event_type = 'price_change'
                      AND best_bid IS NOT NULL AND best_ask IS NOT NULL
                ),
                lagged_returns AS (
                    SELECT 
                        ret as current_ret,
                        LAG(ret) OVER () as prev_ret
                    FROM returns_data
                    WHERE ret IS NOT NULL
                )
                SELECT CORR(current_ret, prev_ret)
                FROM lagged_returns
                WHERE prev_ret IS NOT NULL AND current_ret IS NOT NULL
            """
            ac_res = con.execute(ac_query).fetchone()
            autocorr = ac_res[0] if ac_res and ac_res[0] is not None else 0
            print(f"  Autocorrelación serial (Lag 1): {autocorr:.4f}")
            
        else:
            print("  No hay suficientes datos de price_change válidos.")
    except Exception as e:
        print(f"  Error analizando: {e}")

print("\n" + "=" * 80)
print("PASO 5 — RESUMEN / VIABILIDAD (Generando datos de apoyo)")
print("=" * 80)

# Checar los assets de uno de los mercados de Bitcoin de corto plazo
# "Bitcoin Up or Down - May 13, 6:50PM-6:55PM ET"
sample_cid = '0xd8605cd27e30445d34228dd1dfa6d04aa7686c894421c66474f848f4ab49784d'
print(f"\n--- Análisis de Tokens para el mercado '{crypto_markets[sample_cid]}' ---")

query_tokens = f"""
    SELECT 
        asset_id,
        MIN(price) as min_p,
        MAX(price) as max_p,
        AVG(price) as avg_p,
        COUNT(*) as cnt,
        MIN(timestamp)::VARCHAR,
        MAX(timestamp)::VARCHAR
    FROM read_parquet('{PARQUET}')
    WHERE CAST(market AS VARCHAR) = '{sample_cid}'
      AND event_type = 'price_change'
    GROUP BY asset_id
"""
res_tokens = con.execute(query_tokens).fetchall()
for r in res_tokens:
    print(f"  Token {r[0][:20]}... : Min={r[1]}, Max={r[2]}, Avg={r[3]:.4f}, Count={r[4]:,}")
    print(f"    Rango: {r[5]} -> {r[6]}")

print("\nExploration v5 completed.")
