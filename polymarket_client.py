"""
polymarket_client.py — MÓDULO 1: Conexión a Polymarket.
Usa la API de Gamma (frontend) para descubrir mercados crypto activos con volumen,
y el CLOB para obtener precios del orderbook.
"""

import logging
from datetime import datetime, timezone

import requests

from config import (
    POLYMARKET_CLOB_URL,
    CRYPTO_KEYWORDS,
)
from utils import retry_with_backoff, parse_question, parse_date

logger = logging.getLogger("polybot.polymarket")

# ── API de Gamma: fuente principal de mercados ──────────────────
GAMMA_API_URL = "https://gamma-api.polymarket.com"


@retry_with_backoff(max_retries=3)
def _fetch_gamma_events(tag: str = "crypto", limit: int = 50) -> list[dict]:
    """
    Obtiene eventos activos desde la API de Gamma.
    Los 'events' de Polymarket agrupan sub-mercados (markets).
    """
    url = f"{GAMMA_API_URL}/events"
    params = {
        "tag": tag,
        "active": "true",
        "closed": "false",
        "limit": limit,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    logger.info("Gamma API /events (tag=%s): %d eventos obtenidos.", tag, len(data))
    return data


@retry_with_backoff(max_retries=3)
def _fetch_gamma_markets(limit: int = 100, offset: int = 0, tag: str = "") -> list[dict]:
    """
    Obtiene mercados individuales desde Gamma API.
    """
    url = f"{GAMMA_API_URL}/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "offset": offset,
    }
    if tag:
        params["tag"] = tag
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    logger.info("Gamma API /markets (tag=%s): %d mercados obtenidos.", tag, len(data))
    return data


@retry_with_backoff(max_retries=3)
def _fetch_orderbook(token_id: str) -> dict:
    """Obtiene el orderbook de un token desde el CLOB."""
    url = f"{POLYMARKET_CLOB_URL}/book"
    params = {"token_id": token_id}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _extract_best_prices(book: dict) -> tuple[float, float]:
    """
    Extrae best_bid y best_ask del orderbook.
    Retorna (best_bid, best_ask) en rango [0,1].
    """
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    best_bid = 0.0
    best_ask = 1.0

    if bids:
        best_bid = max(float(b.get("price", 0)) for b in bids)
    if asks:
        best_ask = min(float(a.get("price", 1)) for a in asks)

    return best_bid, best_ask


def _is_crypto_price_market(question: str) -> bool:
    """
    Verifica si la pregunta del mercado es sobre precio de crypto.
    Usa keywords más estrictos para evitar falsos positivos.
    """
    q_lower = question.lower()
    # Requiere mención de asset + contexto de precio
    has_asset = any(kw in q_lower for kw in ["btc", "bitcoin", "eth", "ethereum"])
    has_price = any(kw in q_lower for kw in ["price", "$", "above", "below", "reach", "hit", "exceed"])
    return has_asset and has_price


def _extract_markets_from_events(events: list[dict]) -> list[dict]:
    """
    Extrae los sub-mercados individuales desde los eventos de Gamma.
    Cada evento tiene un array 'markets' con los mercados reales.
    """
    all_markets = []
    for event in events:
        event_title = event.get("title", event.get("slug", ""))
        event_volume = float(event.get("volume", 0) or 0)
        sub_markets = event.get("markets", [])

        logger.debug(
            "Evento: %s | Mercados: %d | Vol total: $%.0f",
            event_title[:50], len(sub_markets), event_volume
        )

        for m in sub_markets:
            # Inyectar volumen del evento si el mercado individual no tiene
            if not m.get("volume"):
                m["_event_volume"] = event_volume
            m["_event_title"] = event_title
            all_markets.append(m)

    return all_markets


def _build_market_data(m: dict) -> dict | None:
    """
    Construye el diccionario normalizado de un mercado individual.
    Retorna None si no es parseable.
    """
    question = m.get("question", m.get("groupItemTitle", ""))
    if not question:
        return None

    # Parsear pregunta
    parsed = parse_question(question)
    if not parsed:
        logger.debug("No se pudo parsear: %s", question[:80])
        return None

    # Estado
    active = m.get("active", True)
    closed = m.get("closed", False)
    if not active or closed:
        return None

    # IDs
    condition_id = m.get("conditionId", m.get("condition_id", m.get("id", "")))
    
    # clobTokenIds puede ser un array Python o un string JSON "[...]"
    clob_token_ids = m.get("clobTokenIds", [])
    if isinstance(clob_token_ids, str):
        try:
            import json as _json
            clob_token_ids = _json.loads(clob_token_ids)
        except (ValueError, TypeError):
            clob_token_ids = []
    
    token_id_yes = clob_token_ids[0] if len(clob_token_ids) > 0 else ""
    token_id_no = clob_token_ids[1] if len(clob_token_ids) > 1 else ""

    # También probar campo 'tokens' del CLOB
    if not token_id_yes or len(str(token_id_yes)) < 10:
        tokens = m.get("tokens", [])
        for t in tokens:
            outcome = str(t.get("outcome", "")).upper()
            if outcome == "YES":
                token_id_yes = t.get("token_id", "")
            elif outcome == "NO":
                token_id_no = t.get("token_id", "")

    # Fechas
    end_date_str = m.get("endDate", m.get("end_date_iso", m.get("end_date", "")))
    end_date = parse_date(end_date_str)

    # Precios — desde los datos del mercado (Gamma incluye outcomePrices)
    outcome_prices = m.get("outcomePrices", "")
    best_bid = 0.0
    best_ask = 1.0
    last_trade = None

    if outcome_prices:
        try:
            # outcomePrices viene como string JSON: "[0.45, 0.55]"
            import json
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            if isinstance(prices, list) and len(prices) >= 1:
                yes_price = float(prices[0])
                last_trade = yes_price
                # Aproximar spread
                best_bid = max(0, yes_price - 0.01)
                best_ask = min(1, yes_price + 0.01)
        except (json.JSONDecodeError, ValueError, IndexError) as e:
            logger.debug("Error parseando outcomePrices '%s': %s", outcome_prices, e)

    # Intentar precio desde otros campos
    if last_trade is None:
        for price_field in ["lastTradePrice", "last_trade_price", "price"]:
            val = m.get(price_field)
            if val is not None:
                try:
                    last_trade = float(val)
                    best_bid = max(0, last_trade - 0.01)
                    best_ask = min(1, last_trade + 0.01)
                    break
                except (ValueError, TypeError):
                    pass

    # Intentar orderbook real del CLOB
    if token_id_yes:
        try:
            book = _fetch_orderbook(token_id_yes)
            ob_bid, ob_ask = _extract_best_prices(book)
            if ob_bid > 0 or ob_ask < 1:
                best_bid = ob_bid
                best_ask = ob_ask
                logger.debug(
                    "Orderbook %s: bid=%.4f, ask=%.4f",
                    question[:40], ob_bid, ob_ask
                )
        except Exception as e:
            logger.debug("No se pudo obtener orderbook para %s: %s", token_id_yes[:20], e)

    # Probabilidad implícita del mercado
    prob_poly = (best_bid + best_ask) / 2.0

    # Volumen
    volume_24h = float(m.get("volume", m.get("volume_num", 0)) or 0)
    if volume_24h == 0:
        volume_24h = float(m.get("_event_volume", 0))

    return {
        "condition_id": condition_id,
        "gamma_id": m.get("id", ""),           # Gamma numeric ID for M2M lookup
        "slug": m.get("slug", ""),
        "question": question,
        "end_date": end_date,
        "end_date_str": str(end_date_str or ""),
        "token_id_yes": token_id_yes,
        "token_id_no": token_id_no,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "last_trade_price": last_trade,
        "prob_poly": prob_poly,
        "volume_24h": volume_24h,
        "parsed": parsed,
        "raw": m,
    }


# ── Función principal del módulo ────────────────────────────────


def get_crypto_markets() -> list[dict]:
    """
    Obtiene mercados crypto de precio desde Polymarket.
    
    Estrategia:
    1. Usa Gamma API /events con tag=crypto para obtener eventos
    2. Extrae sub-mercados de cada evento
    3. También obtiene mercados directos con tag=crypto
    4. Filtra por keywords de precio (BTC/ETH + precio)
    5. Para cada mercado, obtiene precios del orderbook CLOB
    """
    crypto_markets = []
    seen_ids = set()

    # ── Fuente 1: Eventos crypto (agrupan sub-mercados) ──
    try:
        events = _fetch_gamma_events(tag="crypto", limit=100)
        sub_markets = _extract_markets_from_events(events)
        logger.info("Eventos crypto: %d eventos → %d sub-mercados", len(events), len(sub_markets))
    except Exception as e:
        logger.error("Error obteniendo eventos Gamma: %s", e)
        sub_markets = []

    # ── Fuente 2: Mercados directos con tag crypto ──
    direct_markets = []
    try:
        direct_markets = _fetch_gamma_markets(tag="crypto", limit=200)
        logger.info("Mercados directos crypto: %d", len(direct_markets))
    except Exception as e:
        logger.error("Error obteniendo mercados Gamma: %s", e)

    # ── Fuente 3: Búsqueda amplia (sin tag, filtramos por keyword) ──
    broad_markets = []
    try:
        broad_markets = _fetch_gamma_markets(limit=200)
        logger.info("Mercados generales: %d", len(broad_markets))
    except Exception as e:
        logger.error("Error obteniendo mercados generales: %s", e)

    # Combinar todas las fuentes
    all_raw = sub_markets + direct_markets + broad_markets

    for m in all_raw:
        question = m.get("question", m.get("groupItemTitle", ""))

        # Filtrar: solo mercados de precio crypto
        if not _is_crypto_price_market(question):
            continue

        # Deduplicar
        uid = m.get("conditionId", m.get("condition_id", m.get("id", question)))
        if uid in seen_ids:
            continue
        seen_ids.add(uid)

        # Construir datos normalizados
        market_data = _build_market_data(m)
        if not market_data:
            continue

        crypto_markets.append(market_data)
        logger.info(
            "Mercado crypto encontrado: %s | Poly=%.1f%% | Vol=$%.0f",
            question[:60], market_data["prob_poly"] * 100, market_data["volume_24h"]
        )

    logger.info("Total mercados crypto de precio filtrados: %d", len(crypto_markets))
    return crypto_markets
