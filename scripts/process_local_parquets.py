"""
Script ultra-rápido para procesar archivos Parquet de Polymarket descargados localmente.
Agrupa los archivos por día, identifica mercados crypto y genera parquets filtrados.
"""
import os
import glob
import duckdb
import time
from collections import defaultdict
import json
import urllib.request

# Usaremos las funciones del script anterior para reutilizar tu excelente lógica
from download_crypto_hourly import (
    load_json, save_json, resolve_markets_batch, resolve_missing_cids_async, is_crypto_question
)
import asyncio

RAW_DIR = r"D:\Proyectos\polystudio\polystudio\data\parquet"
OUT_DIR = os.path.join(RAW_DIR, "filtered_daily")
CACHE_FILE = os.path.join(RAW_DIR, "known_crypto_markets.json")

os.makedirs(OUT_DIR, exist_ok=True)

print("=" * 80)
print("INICIANDO PROCESADOR LOCAL DE PARQUETS")
print("=" * 80)

# 1. Encontrar todos los archivos brutos y agruparlos por día
all_files = glob.glob(os.path.join(RAW_DIR, "polymarket_orderbook_*.parquet"))
days = defaultdict(list)

for f in all_files:
    # f = polymarket_orderbook_2026-05-13T23.parquet
    basename = os.path.basename(f)
    if len(basename) >= 31:
        day_str = basename[21:31] # Extrae YYYY-MM-DD
        days[day_str].append(f)

print(f"Encontrados {len(all_files)} archivos agrupados en {len(days)} días distintos.")

if not days:
    print("No hay archivos locales para procesar. Saliendo.")
    exit()

# 2. Inicializar Caché y DuckDB
resolved_cache = load_json(CACHE_FILE, {})
con = duckdb.connect()
con.execute("SET memory_limit='4GB'")

# 3. Procesar día por día localmente
for day_str, files in sorted(days.items(), reverse=True):
    out_file = os.path.join(OUT_DIR, f"crypto_{day_str}.parquet")
    if os.path.exists(out_file):
        print(f"\n[+] {day_str} ya está procesado. Saltando.")
        continue
        
    print(f"\n[+] Procesando Día: {day_str} ({len(files)} archivos locales)")
    files_sql = ", ".join([f"'{f}'" for f in files])
    
    # Extraer CIDs activos
    t0 = time.time()
    try:
        top_markets = con.execute(f"""
            SELECT CAST(market AS VARCHAR) as cid, COUNT(*) as cnt
            FROM read_parquet([{files_sql}])
            WHERE event_type = 'price_change'
            GROUP BY cid HAVING cnt > 50
        """).fetchall()
    except Exception as e:
        print(f"Error leyendo {day_str}: {e}")
        continue
        
    # Identificar nuevos para resolver
    all_cids = [r[0] for r in top_markets]
    unknown = [c for c in all_cids if c not in resolved_cache]
    
    if unknown:
        print(f"  -> Resolviendo {len(unknown)} mercados nuevos vía API...")
        batch_res = resolve_markets_batch(unknown)
        
        missing_cids = [cid for cid in unknown if cid not in batch_res or batch_res[cid] == "Unknown"]
        if missing_cids:
            print(f"  -> Ejecutando Fallback Asíncrono para {len(missing_cids)} mercados huérfanos...")
            async_res = asyncio.run(resolve_missing_cids_async(missing_cids))
            batch_res.update(async_res)
            
        for cid in unknown:
            q = batch_res.get(cid, "Unknown")
            resolved_cache[cid] = {"question": q, "is_crypto": is_crypto_question(q)}
        save_json(CACHE_FILE, resolved_cache)
        
    # Filtrar solo Crypto
    crypto_cids = {c for c in all_cids if resolved_cache.get(c, {}).get("is_crypto")}
    if not crypto_cids:
        print("  -> Sin mercados crypto este día.")
        continue
        
    # Escribir Parquet Filtrado
    cids_sql = ", ".join([f"'{c}'" for c in crypto_cids])
    con.execute(f"""
        COPY (
            SELECT * FROM read_parquet([{files_sql}])
            WHERE CAST(market AS VARCHAR) IN ({cids_sql})
        ) TO '{out_file}' (FORMAT PARQUET, COMPRESSION ZSTD);
    """)
    
    t1 = time.time()
    size_mb = os.path.getsize(out_file) / (1024*1024)
    print(f"  -> Listo! Guardado {out_file} ({size_mb:.1f} MB) en {t1-t0:.1f}s")

print("\nTodo el procesamiento local completado exitosamente.")
