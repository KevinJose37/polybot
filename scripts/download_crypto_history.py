"""
Script robusto para descargar, filtrar y guardar 4 semanas de mercados Crypto de 5 minutos
desde archive.pmxt.dev usando DuckDB (httpfs) y resolución de API.
"""
import duckdb
import os
import sys
import json
import time
import urllib.request
from datetime import datetime, timedelta

# Asegurar encoding correcto
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Configuraciones
OUTPUT_DIR = r"D:\Proyectos\polystudio\polystudio\data\parquet\crypto_filtered"
CACHE_FILE = os.path.join(OUTPUT_DIR, "known_crypto_markets.json")
STATE_FILE = os.path.join(OUTPUT_DIR, "processed_days.json")
DAYS_TO_DOWNLOAD = 28
START_DATE = datetime(2026, 5, 13) # La fecha más reciente disponible

CRYPTO_KEYWORDS = ['bitcoin', 'btc', 'xbt', 'ethereum', 'eth', 'solana', 'sol', 
                   'crypto', 'doge', 'xrp', 'ada', 'avax', 'matic', 'link',
                   'up or down']

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# Inicializar DuckDB
con = duckdb.connect()
con.execute("INSTALL httpfs;")
con.execute("LOAD httpfs;")
con.execute("SET memory_limit='4GB';") # Asignamos más RAM para operaciones remotas seguras

# Cargar cachés
resolved_cache = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        resolved_cache = json.load(f)

processed_days = []
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        processed_days = json.load(f)

def save_cache():
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(resolved_cache, f, indent=2)

def save_state():
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(processed_days, f, indent=2)

def resolve_market_api(cid):
    """Consulta la API de Polymarket para saber si un condition_id es Crypto"""
    question = "Unknown"
    is_crypto = False
    
    # 1. Intentar API CLOB
    try:
        url = f"https://clob.polymarket.com/markets/{cid}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            question = data.get('question', data.get('description', 'Unknown'))
    except:
        # 2. Intentar API Gamma
        try:
            url = f"https://gamma-api.polymarket.com/markets?condition_ids={cid}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if data and len(data) > 0:
                    question = data[0].get('question', 'Unknown')
        except:
            return question, False
            
    q_lower = question.lower()
    
    # Lógica de filtro estricta
    has_crypto_kw = any(kw in q_lower for kw in CRYPTO_KEYWORDS)
    is_weather = "temperature" in q_lower or "weather" in q_lower or "rain" in q_lower or "°" in q_lower
    is_sports = " vs " in q_lower or " win on " in q_lower or "score" in q_lower
    
    if has_crypto_kw and not is_weather and not is_sports:
        is_crypto = True
        
    return question, is_crypto


print("=" * 80)
print(f"INICIANDO DESCARGA Y FILTRADO MASIVO: {DAYS_TO_DOWNLOAD} DÍAS")
print("=" * 80)
print(f"Directorio de destino: {OUTPUT_DIR}")

# Iterar día por día
for day_offset in range(DAYS_TO_DOWNLOAD):
    current_date = START_DATE - timedelta(days=day_offset)
    day_str = current_date.strftime("%Y-%m-%d")
    
    if day_str in processed_days:
        print(f"[-] Día {day_str} ya fue procesado. Saltando...")
        continue
        
    print(f"\n[+] Procesando Día: {day_str}")
    
    # Generar URLs de las 24 horas del día
    urls = []
    for hour in range(24):
        url = f"https://r2v2.pmxt.dev/polymarket_orderbook_{day_str}T{hour:02d}.parquet"
        urls.append(url)
        
    urls_str = ", ".join([f"'{u}'" for u in urls])
    
    # 1. Encontrar los condition_ids más activos del día sin descargar todo
    print("  -> Consultando mercados activos (leyendo metadata remota)...")
    try:
        # Usamos IGNORE_ERRORS por si alguna URL (alguna hora) no existe (ej. mantenimiento)
        top_markets = con.execute(f"""
            SELECT CAST(market AS VARCHAR) as cid, COUNT(*) as cnt
            FROM read_parquet([{urls_str}])
            WHERE event_type = 'price_change'
            GROUP BY cid
            HAVING cnt > 2000
            ORDER BY cnt DESC
            LIMIT 500
        """).fetchall()
    except Exception as e:
        print(f"  -> Error leyendo metadata del día {day_str}: {e}")
        print("  -> Posiblemente no hay datos para este día. Saltando.")
        processed_days.append(day_str)
        save_state()
        continue

    print(f"  -> {len(top_markets)} mercados candidatos encontrados.")
    
    # 2. Resolver via API los desconocidos
    crypto_cids_today = set()
    new_resolutions = 0
    
    for cid, cnt in top_markets:
        if cid not in resolved_cache:
            question, is_crypto = resolve_market_api(cid)
            resolved_cache[cid] = {
                'question': question,
                'is_crypto': is_crypto,
                'records_seen': cnt
            }
            new_resolutions += 1
            time.sleep(0.1) # Respetar rate limits de la API
            
        if resolved_cache[cid].get('is_crypto', False):
            crypto_cids_today.add(cid)
            
    if new_resolutions > 0:
        print(f"  -> Se resolvieron {new_resolutions} nuevos mercados en la API.")
        save_cache()
        
    print(f"  -> Identificados {len(crypto_cids_today)} mercados Crypto para el {day_str}.")
    
    if len(crypto_cids_today) == 0:
        print("  -> No hay mercados crypto significativos. Marcando como completado.")
        processed_days.append(day_str)
        save_state()
        continue
        
    # 3. Descargar y Filtrar a disco local
    output_file = os.path.join(OUTPUT_DIR, f"crypto_{day_str}.parquet")
    cids_sql_list = ", ".join([f"'{c}'" for c in crypto_cids_today])
    
    print(f"  -> Extrayendo datos remotos a {output_file}...")
    t0 = time.time()
    
    extract_query = f"""
        COPY (
            SELECT * FROM read_parquet([{urls_str}])
            WHERE CAST(market AS VARCHAR) IN ({cids_sql_list})
        ) TO '{output_file}' (FORMAT PARQUET, COMPRESSION ZSTD);
    """
    
    try:
        con.execute(extract_query)
        t1 = time.time()
        file_size = os.path.getsize(output_file) / (1024 * 1024)
        print(f"  -> ¡Completado en {t1-t0:.1f} segs! Tamaño guardado: {file_size:.2f} MB")
        
        # Guardar progreso
        processed_days.append(day_str)
        save_state()
    except Exception as e:
        print(f"  -> Error crìtico durante la extracción: {e}")
        # No guardamos en el estado para que se reintente la próxima vez
        
print("\n" + "=" * 80)
print("PROCESO MASIVO COMPLETADO")
print(f"Tus datos limpios están en: {OUTPUT_DIR}")
print("Puedes leerlos todos juntos con: pd.read_parquet('ruta/crypto_filtered/*.parquet')")
print("=" * 80)
