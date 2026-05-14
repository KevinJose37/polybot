"""
Script robusto para descargar, filtrar y guardar mercados Crypto de 5 minutos
desde archive.pmxt.dev usando DuckDB (httpfs), iterando HORA por HORA.

Mejoras sobre la versión original:
  - Resolución de mercados en BATCH (100x más rápido en primera corrida)
  - Distinción entre error 404 (archivo no existe) y error de red (reintentable)
  - Threshold de registros reducido a 50 (no pierde mercados de poco tiempo)
  - Memory limit configurable
  - Keywords con word-boundary para evitar falsos positivos
  - Retry automático en errores de red transitorios
"""

import duckdb
import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
import asyncio
import aiohttp
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
# Se eliminó el re-envoltorio de sys.stdout porque rompe la consola de Windows en Python 3.12

# ==============================================================================
# CONFIGURACIÓN
# ==============================================================================

OUTPUT_DIR      = r"D:\Proyectos\polystudio\polystudio\data\parquet\crypto_hourly"
CACHE_FILE      = os.path.join(OUTPUT_DIR, "known_crypto_markets.json")
STATE_FILE      = os.path.join(OUTPUT_DIR, "processed_hours.json")
ERRORS_FILE     = os.path.join(OUTPUT_DIR, "network_errors.json")

DAYS_TO_DOWNLOAD  = 28
START_DATE        = datetime(2026, 5, 13, 23)   # Última hora disponible

DUCKDB_MEMORY     = "4GB"   # Subir si tienes RAM disponible (recomendado 4-8GB)
DUCKDB_THREADS    = 4       # Ajustar según núcleos disponibles

MIN_RECORDS       = 50      # Mínimo de registros para considerar un mercado activo
MAX_MARKETS_CHECK = 500     # Cuántos mercados revisar por hora

MAX_RETRIES       = 3       # Reintentos en errores de red
RETRY_WAIT_S      = 5       # Segundos entre reintentos

# Keywords de texto completo (seguros para substring)
CRYPTO_KEYWORDS_FULL = [
    "bitcoin", "ethereum", "solana", "dogecoin", "cardano",
    "avalanche", "polygon", "chainlink", "ripple", "polkadot",
    "uniswap", "shiba", "litecoin", "stellar", "monero",
    "up or down",   # El más confiable para mercados de precio Polymarket
    "crypto",
]

# Keywords cortas — solo se comparan como palabras completas
CRYPTO_KEYWORDS_WORD = [
    "btc", "eth", "sol", "xrp", "ada", "avax", "xbt",
    "matic", "link", "dot", "doge", "ltc", "xlm", "xmr",
]

# Exclusiones — si la pregunta contiene esto, NO es crypto
EXCLUSION_KEYWORDS = [
    "weather", "temperature", "rain", "°f", "°c", "humidity",
    " vs ", "score", "election", "president", "senator", "congress",
    "nfl", "nba", "mlb", "nhl", "fifa", "epl", "ufc", "mma",
    "movie", "oscars", "grammy", "emmy", "box office",
    "unemployment", "gdp", "cpi", "inflation rate",  # Macro OK si no es crypto
]

# ==============================================================================
# UTILIDADES
# ==============================================================================

os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_json(path: str, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_crypto_question(question: str) -> bool:
    """
    Determina si una pregunta de mercado es de precio crypto.
    Usa word-boundary para keywords cortas para evitar falsos positivos.
    """
    q = question.lower()

    # 1. Excluir primero
    if any(ex in q for ex in EXCLUSION_KEYWORDS):
        return False

    # 2. Keywords largas — substring seguro
    if any(kw in q for kw in CRYPTO_KEYWORDS_FULL):
        return True

    # 3. Keywords cortas — solo como palabras completas
    words = set(q.replace("-", " ").replace("/", " ").split())
    if any(kw in words for kw in CRYPTO_KEYWORDS_WORD):
        return True

    return False


def http_get(url: str, timeout: int = 10) -> bytes:
    """Hace GET con User-Agent. Lanza urllib.error.HTTPError si falla."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 pmxt-downloader"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ==============================================================================
# RESOLUCIÓN DE MERCADOS EN BATCH
# ==============================================================================

def resolve_markets_batch(cids: list) -> dict:
    """
    Resuelve hasta 100 condition_ids en una sola llamada a Gamma API.
    Devuelve dict: { condition_id -> question_text }

    Mucho más eficiente que resolver uno por uno.
    """
    results = {}
    chunk_size = 100

    for i in range(0, len(cids), chunk_size):
        chunk = cids[i : i + chunk_size]
        ids_param = "&condition_ids=".join(chunk)
        url = f"https://gamma-api.polymarket.com/markets?condition_ids={ids_param}"

        for attempt in range(MAX_RETRIES):
            try:
                raw = http_get(url, timeout=15)
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, list):
                    for market in data:
                        cid  = market.get("conditionId", "")
                        q    = market.get("question", market.get("description", "Unknown"))
                        if cid:
                            results[cid] = q
                
                # Imprimir progreso cada 5 batches (500 mercados) para no ensuciar tanto la consola
                current_batch = (i // chunk_size) + 1
                total_batches = (len(cids) // chunk_size) + 1
                if current_batch % 5 == 0 or current_batch == total_batches:
                    print(f"    [Progreso API] Lote {current_batch}/{total_batches} resuelto ({(current_batch/total_batches)*100:.1f}%)", flush=True)
                    
                break  # éxito

            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = RETRY_WAIT_S * (attempt + 1)
                    print(f"    [Rate limit] Esperando {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"    [HTTP {e.code}] en batch {i//chunk_size + 1}: {e}")
                    break

            except Exception as e:
                print(f"    [Error batch intento {attempt+1}] {e}")
                time.sleep(RETRY_WAIT_S)

        time.sleep(0.3)  # Un sleep por batch, no por mercado

    return results


async def fetch_single_clob(session, cid, sem):
    async with sem:
        url = f"https://clob.polymarket.com/markets/{cid}"
        for attempt in range(3):
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 404:
                        return cid, "Unknown"
                    if resp.status == 200:
                        data = await resp.json()
                        return cid, data.get("question", data.get("description", "Unknown"))
                    if resp.status == 429:
                        await asyncio.sleep(1 * (attempt + 1))
                        continue
            except Exception:
                await asyncio.sleep(1)
        return cid, "Unknown"

async def resolve_missing_cids_async(cids: list) -> dict:
    """
    Resuelve miles de CIDs en la API profunda (CLOB) concurrentemente a 50 req/s.
    """
    results = {}
    sem = asyncio.Semaphore(50)
    total = len(cids)
    done = 0
    found = 0
    
    async with aiohttp.ClientSession(headers={"User-Agent": "pmxt-downloader"}) as session:
        tasks = [fetch_single_clob(session, cid, sem) for cid in cids]
        
        for coro in asyncio.as_completed(tasks):
            cid, q = await coro
            results[cid] = q
            done += 1
            if q != "Unknown":
                found += 1
                
            if done % 100 == 0 or done == total:
                print(f"    [CLOB Fallback Async] {done}/{total} analizados | Vivos: {found} | Borrados de PM: {done - found}    ", end='\\r', flush=True)
                
    print()
    return results

def resolve_single_fallback(cid: str) -> str:
    # Retenida por compatibilidad con código viejo, pero no usar masivamente
    for attempt in range(2):
        try:
            raw  = http_get(f"https://clob.polymarket.com/markets/{cid}", timeout=8)
            data = json.loads(raw.decode("utf-8"))
            return data.get("question", data.get("description", "Unknown"))
        except Exception:
            import time
            time.sleep(1)
    return "Unknown"


# ==============================================================================
# DESCARGA Y FILTRADO POR HORA
# ==============================================================================

def process_hour(
    con: duckdb.DuckDBPyConnection,
    dt_str: str,
    resolved_cache: dict,
    network_errors: list,
) -> str:
    """
    Procesa una hora. Devuelve:
      'ok'       — procesado correctamente (aunque sin mercados crypto)
      'notfound' — archivo 404 (no existe)
      'neterror' — error de red transitorio (no marcar como procesado)
    """
    url         = f"https://r2v2.pmxt.dev/polymarket_orderbook_{dt_str}.parquet"
    output_file = os.path.join(OUTPUT_DIR, f"crypto_{dt_str}.parquet")

    # ── 1. Obtener top markets de esta hora ──────────────────────────────────
    for attempt in range(MAX_RETRIES):
        try:
            top_markets = con.execute(f"""
                SELECT CAST(market AS VARCHAR) as cid, COUNT(*) as cnt
                FROM read_parquet('{url}')
                WHERE event_type = 'price_change'
                GROUP BY cid
                HAVING cnt > {MIN_RECORDS}
                ORDER BY cnt DESC
                LIMIT {MAX_MARKETS_CHECK}
            """).fetchall()
            break  # éxito

        except Exception as e:
            err = str(e).lower()

            if "404" in err or "not found" in err or "no such file" in err:
                print(f"  -> Archivo no encontrado (404). Omitiendo.")
                return "notfound"

            if attempt < MAX_RETRIES - 1:
                print(f"  -> Error de red (intento {attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(RETRY_WAIT_S * (attempt + 1))
            else:
                print(f"  -> Error persistente de red. Se reintentará en la próxima corrida.")
                network_errors.append({"hour": dt_str, "error": str(e), "ts": datetime.utcnow().isoformat()})
                return "neterror"

    # ── 2. Identificar CIDs nuevos que necesitan resolución ──────────────────
    all_cids    = [row[0] for row in top_markets]
    unknown_cids = [c for c in all_cids if c not in resolved_cache]

    if unknown_cids:
        print(f"  -> Resolviendo {len(unknown_cids)} mercados nuevos en batch...")
        batch_results = resolve_markets_batch(unknown_cids)

        resolved_count = 0
        for cid in unknown_cids:
            question = batch_results.get(cid)

            if not question:
                # Fallback individual para los que el batch no encontró
                question = resolve_single_fallback(cid)
                time.sleep(0.2)

            is_crypto = is_crypto_question(question)
            cnt_row   = next((r[1] for r in top_markets if r[0] == cid), 0)

            resolved_cache[cid] = {
                "question":     question,
                "is_crypto":    is_crypto,
                "records_seen": int(cnt_row),
            }
            if is_crypto:
                resolved_count += 1

        print(f"  -> {resolved_count}/{len(unknown_cids)} nuevos son crypto")

    # ── 3. Filtrar CIDs crypto de esta hora ──────────────────────────────────
    crypto_cids = {
        cid for cid in all_cids
        if resolved_cache.get(cid, {}).get("is_crypto", False)
    }

    if not crypto_cids:
        print(f"  -> Sin mercados crypto en esta hora. Omitiendo.")
        return "ok"

    # ── 4. Descargar y guardar solo datos crypto ──────────────────────────────
    cids_sql = ", ".join(f"'{c}'" for c in crypto_cids)

    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.time()
            con.execute(f"""
                COPY (
                    SELECT * FROM read_parquet('{url}')
                    WHERE CAST(market AS VARCHAR) IN ({cids_sql})
                ) TO '{output_file}' (FORMAT PARQUET, COMPRESSION ZSTD);
            """)
            elapsed   = time.time() - t0
            file_size = os.path.getsize(output_file) / 1024
            print(f"  -> Guardado: crypto_{dt_str}.parquet ({file_size:.1f} KB) en {elapsed:.1f}s | {len(crypto_cids)} mercados")
            return "ok"

        except Exception as e:
            err = str(e).lower()
            if "404" in err or "not found" in err:
                return "notfound"
            if attempt < MAX_RETRIES - 1:
                print(f"  -> Error guardando (intento {attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(RETRY_WAIT_S)
            else:
                print(f"  -> Error persistente guardando. Se reintentará.")
                network_errors.append({"hour": dt_str, "error": str(e), "ts": datetime.utcnow().isoformat()})
                return "neterror"

    return "neterror"


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    print("=" * 80)
    print(f"  DESCARGA HORA POR HORA — {DAYS_TO_DOWNLOAD} DÍAS ({DAYS_TO_DOWNLOAD * 24} HORAS)")
    print(f"  Desde: {START_DATE.strftime('%Y-%m-%dT%H')} hacia atrás")
    print(f"  Output: {OUTPUT_DIR}")
    print("=" * 80)

    # Cargar estado previo
    resolved_cache  = load_json(CACHE_FILE, {})
    processed_hours = set(load_json(STATE_FILE, []))
    network_errors  = load_json(ERRORS_FILE, [])

    print(f"\n  Cache cargado: {len(resolved_cache)} mercados conocidos")
    print(f"  Horas ya procesadas: {len(processed_hours)}")

    # Inicializar DuckDB
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"SET memory_limit='{DUCKDB_MEMORY}';")
    con.execute(f"SET threads={DUCKDB_THREADS};")

    total_hours = DAYS_TO_DOWNLOAD * 24
    skipped     = 0
    ok          = 0
    not_found   = 0
    net_errors  = 0

    for hour_offset in range(total_hours):
        current_dt = START_DATE - timedelta(hours=hour_offset)
        dt_str     = current_dt.strftime("%Y-%m-%dT%H")

        # Saltar horas ya procesadas exitosamente
        if dt_str in processed_hours:
            skipped += 1
            continue

        print(f"\n[{hour_offset+1:>4}/{total_hours}] {dt_str}")

        result = process_hour(con, dt_str, resolved_cache, network_errors)

        if result == "ok":
            ok += 1
            processed_hours.add(dt_str)
        elif result == "notfound":
            not_found += 1
            processed_hours.add(dt_str)   # No reintentamos 404s
        elif result == "neterror":
            net_errors += 1
            # NO agregamos a processed_hours — se reintenta en próxima corrida

        # Guardar estado cada 10 horas
        if (hour_offset + 1) % 10 == 0:
            save_json(CACHE_FILE, resolved_cache)
            save_json(STATE_FILE, list(processed_hours))
            save_json(ERRORS_FILE, network_errors)
            print(f"\n  [Checkpoint] Cache guardado. OK={ok} | 404={not_found} | NetErr={net_errors} | Skip={skipped}")

    # Guardar estado final
    save_json(CACHE_FILE, resolved_cache)
    save_json(STATE_FILE, list(processed_hours))
    save_json(ERRORS_FILE, network_errors)

    print("\n" + "=" * 80)
    print("  DESCARGA COMPLETADA")
    print(f"  Horas procesadas:   {ok}")
    print(f"  Sin archivo (404):  {not_found}")
    print(f"  Errores de red:     {net_errors}  ← Volver a correr para reintentar")
    print(f"  Ya estaban listas:  {skipped}")
    print(f"  Mercados conocidos: {len(resolved_cache)}")
    crypto_known = sum(1 for v in resolved_cache.values() if v.get("is_crypto"))
    print(f"  Mercados crypto:    {crypto_known}")
    print("=" * 80)


if __name__ == "__main__":
    main()