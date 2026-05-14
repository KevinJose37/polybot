"""
ETL Pipeline para resamplear el Order Flow Imbalance de Polymarket.
Reconstruye el Orderbook L2 a partir de deltas y genera velas de 5s.
"""
import duckdb
import pandas as pd
import numpy as np
import os
from collections import defaultdict
import time

# Configuraciones
INPUT_PARQUET = r"D:\Proyectos\polystudio\polystudio\data\parquet\filtered_daily\crypto_2026-05-13.parquet"
OUTPUT_DIR = r"D:\Proyectos\polystudio\polystudio\data\ml_features"
OUTPUT_PARQUET = os.path.join(OUTPUT_DIR, "etl_resampled_5s.parquet")

os.makedirs(OUTPUT_DIR, exist_ok=True)


def reconstruct_l5_sizes(df):
    records = []
    books = defaultdict(lambda: defaultdict(float))
    
    for row in df.itertuples(index=False):
        cid = row.cid
        evt = row.event_type
        
        if evt == 'price_change':
            if pd.notna(row.price) and pd.notna(row.size):
                p_key = round(float(row.price), 4)
                books[cid][p_key] = float(row.size)
        
        # Calculate L5 sizes
        bid_size_l5 = 0.0
        ask_size_l5 = 0.0
        
        if pd.notna(row.best_bid):
            bb = round(float(row.best_bid), 4)
            # Sum up to 5 levels (e.g. bb, bb-0.01, bb-0.02, bb-0.03, bb-0.04)
            # In PM, prices are in cents or fractions. We just sum the 5 highest bids available in the book
            bids_available = sorted([p for p in books[cid].keys() if p <= bb and books[cid][p] > 0], reverse=True)[:5]
            bid_size_l5 = sum(books[cid][p] for p in bids_available)
            best_bid_size = books[cid][bb] if bb in books[cid] else 0.0
        else:
            best_bid_size = np.nan
            
        if pd.notna(row.best_ask):
            ba = round(float(row.best_ask), 4)
            asks_available = sorted([p for p in books[cid].keys() if p >= ba and books[cid][p] > 0])[:5]
            ask_size_l5 = sum(books[cid][p] for p in asks_available)
            best_ask_size = books[cid][ba] if ba in books[cid] else 0.0
        else:
            best_ask_size = np.nan
                        
        records.append({
            'cid': cid,
            'timestamp': row.timestamp,
            'best_bid': row.best_bid,
            'best_ask': row.best_ask,
            'best_bid_size': best_bid_size,
            'best_ask_size': best_ask_size,
            'bid_size_l5': bid_size_l5,
            'ask_size_l5': ask_size_l5,
            'mid_price': (row.best_bid + row.best_ask) / 2.0 if (pd.notna(row.best_bid) and pd.notna(row.best_ask)) else np.nan
        })
        
    return pd.DataFrame(records)

import json

def get_btc_cids():
    cache_file = r"D:\Proyectos\polystudio\polystudio\data\parquet\known_crypto_markets.json"
    if not os.path.exists(cache_file):
        print(f"Error: No se encontró {cache_file}")
        return []
    
    with open(cache_file, "r", encoding="utf-8") as f:
        cache = json.load(f)
        
    btc_cids = []
    for cid, data in cache.items():
        if data.get("is_crypto"):
            q = data.get("question", "").lower()
            if "bitcoin" in q or "btc" in q:
                btc_cids.append(cid)
    return btc_cids

def run_etl():
    print("=" * 80)
    print("INICIANDO ETL PIPELINE (Exclusivo BTC)")
    print("=" * 80)
    
    btc_cids = get_btc_cids()
    print(f"[*] Filtrando {len(btc_cids)} mercados conocidos de Bitcoin...")
    
    if not btc_cids:
        print("No hay mercados de BTC para analizar.")
        return
        
    # Formatear para consulta SQL
    cids_sql = ",".join([f"'{c}'" for c in btc_cids])
    
    con = duckdb.connect()
    
    print("[1/3] Cargando deltas desde DuckDB...")
    t0 = time.time()
    query = f"""
        SELECT 
            CAST(market AS VARCHAR) as cid,
            CAST(asset_id AS VARCHAR) as asset_id,
            timestamp,
            event_type,
            best_bid,
            best_ask,
            CAST(price AS DOUBLE) as price,
            CAST(size AS DOUBLE) as size
        FROM read_parquet('{INPUT_PARQUET}')
        WHERE event_type = 'price_change' 
          AND CAST(market AS VARCHAR) IN ({cids_sql})
        ORDER BY cid, timestamp ASC
    """
    df_raw = con.execute(query).df()
    print(f"  -> {len(df_raw):,} eventos cargados en {time.time()-t0:.2f}s")
    
    print("\n[2/3] Reconstruyendo estado del Orderbook para obtener L5 sizes...")
    t0 = time.time()
    df_l1 = reconstruct_l5_sizes(df_raw)
    print(f"  -> L5 reconstruido en {time.time()-t0:.2f}s")
    
    # Remover filas donde no hay liquidez
    df_l1 = df_l1.dropna(subset=['best_bid', 'best_ask'])
    
    print("\n[3/3] Resampleando a velas de 5 Segundos (Forward Fill)...")
    df_l1['timestamp'] = pd.to_datetime(df_l1['timestamp'], utc=True)
    df_l1.set_index('timestamp', inplace=True)
    
    resampled_dfs = []
    market_count = df_l1['cid'].nunique()
    
    print(f"  -> Procesando {market_count} mercados independientes...")
    
    for cid, group in df_l1.groupby('cid'):
        # Resamplear a 5s
        res_group = group.resample('5s').last()
        tick_counts = group.resample('5s').size()
        
        res_group = res_group.ffill()
        res_group = res_group.dropna()
        res_group['cid'] = cid
        res_group['tick_count'] = tick_counts
        
        if len(res_group) > 0:
            first_time = res_group.index[0]
            res_group['seconds_since_start'] = (res_group.index - first_time).total_seconds()
            resampled_dfs.append(res_group)
            
    final_df = pd.concat(resampled_dfs)
    final_df.reset_index(inplace=True)
    
    final_df = final_df[['cid', 'timestamp', 'seconds_since_start', 'tick_count', 'best_bid', 'best_ask', 
                         'best_bid_size', 'best_ask_size', 'bid_size_l5', 'ask_size_l5', 'mid_price']]
                         
    final_df.to_parquet(OUTPUT_PARQUET, index=False)
    
    size_mb = os.path.getsize(OUTPUT_PARQUET) / (1024 * 1024)
    print(f"\n  -> Guardado en: {OUTPUT_PARQUET}")
    print(f"  -> Filas finales: {len(final_df):,}")
    print(f"  -> Tamaño: {size_mb:.2f} MB")
    print("=" * 80)
    print("ETL COMPLETADO.")

if __name__ == "__main__":
    run_etl()
