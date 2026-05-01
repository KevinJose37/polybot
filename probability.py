"""
probability.py — MÓDULO 2: Cálculo de probabilidad externa.
Combina 3 fuentes: GBM (Black-Scholes), Deribit options, Fear & Greed Index.
"""

import math
import logging
from datetime import datetime, timezone

import numpy as np
import requests
from scipy import stats as sp_stats

from config import (
    BINANCE_PRICE_URL,
    BINANCE_KLINES_URL,
    DERIBIT_INSTRUMENTS_URL,
    DERIBIT_TICKER_URL,
    FNG_URL,
    WEIGHT_GBM,
    WEIGHT_DERIBIT,
    WEIGHT_SENTIMENT,
)
from utils import retry_with_backoff

logger = logging.getLogger("polybot.probability")


# ═══════════════════════════════════════════════════════════════════
# 2a. Modelo estadístico GBM — Geometric Brownian Motion
# ═══════════════════════════════════════════════════════════════════


@retry_with_backoff(max_retries=3)
def get_binance_price(symbol: str) -> float:
    """
    Obtiene el precio spot actual de un par desde Binance.
    symbol: 'BTCUSDT' o 'ETHUSDT'
    """
    resp = requests.get(BINANCE_PRICE_URL, params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    price = float(data["price"])
    logger.info("Precio Binance %s: $%.2f", symbol, price)
    return price


@retry_with_backoff(max_retries=3)
def get_binance_volatility(symbol: str, days: int = 30) -> float:
    """
    Calcula la volatilidad anualizada a partir de velas diarias de Binance.
    Usa los log-returns del precio de cierre.
    """
    resp = requests.get(
        BINANCE_KLINES_URL,
        params={
            "symbol": symbol,
            "interval": "1d",
            "limit": days + 1,
        },
        timeout=10,
    )
    resp.raise_for_status()
    klines = resp.json()

    # Extraer precios de cierre (index 4 en el array de kline)
    closes = [float(k[4]) for k in klines]

    if len(closes) < 2:
        logger.warning("Datos insuficientes para volatilidad de %s", symbol)
        return 0.6  # fallback: 60% anualizado

    # Log-returns diarios
    log_returns = np.diff(np.log(closes))
    daily_vol = np.std(log_returns, ddof=1)
    annualized_vol = daily_vol * np.sqrt(365)

    logger.info(
        "Volatilidad %s (%dd): diaria=%.4f, anual=%.4f",
        symbol, days, daily_vol, annualized_vol
    )
    return float(annualized_vol)


def calc_gbm_probability(
    spot: float,
    strike: float,
    vol: float,
    days_to_expiry: float,
    direction: str = "above",
    risk_free_rate: float = 0.05,
) -> float:
    """
    Modelo Black-Scholes / log-normal (GBM) para estimar la
    probabilidad de que el precio supere/baje del strike antes
    del vencimiento.

    P(S_T > K) = N(d2) donde:
      d2 = [ln(S/K) + (r - σ²/2)·T] / (σ·√T)

    Para "below", retorna 1 - P(above).
    """
    if days_to_expiry <= 0:
        # Ya venció: comparar spot vs strike
        if direction == "above":
            return 1.0 if spot > strike else 0.0
        return 1.0 if spot < strike else 0.0

    T = days_to_expiry / 365.0
    sigma = vol

    if sigma <= 0:
        sigma = 0.01  # evitar división por cero

    d2 = (math.log(spot / strike) + (risk_free_rate - 0.5 * sigma**2) * T) / (
        sigma * math.sqrt(T)
    )

    # N(d2) = probabilidad de terminar above strike bajo medida real
    prob_above = float(sp_stats.norm.cdf(d2))

    if direction == "below":
        return 1.0 - prob_above

    return prob_above


def get_prob_gbm(asset: str, strike: float, days_to_expiry: float, direction: str) -> float | None:
    """
    Calcula la probabilidad GBM completa para un asset dado.
    Retorna None si falla.
    """
    symbol_map = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
    symbol = symbol_map.get(asset)
    if not symbol:
        logger.warning("Asset no soportado para GBM: %s", asset)
        return None

    try:
        spot = get_binance_price(symbol)
        vol = get_binance_volatility(symbol, days=30)
        prob = calc_gbm_probability(spot, strike, vol, days_to_expiry, direction)
        logger.info(
            "GBM %s: spot=$%.2f, strike=$%.2f, vol=%.2f, T=%.1fd, dir=%s → prob=%.3f",
            asset, spot, strike, vol, days_to_expiry, direction, prob
        )
        return prob
    except Exception as e:
        logger.error("Error calculando GBM para %s: %s", asset, e)
        return None


# ═══════════════════════════════════════════════════════════════════
# 2b. Deribit Options — Probabilidad implícita via delta
# ═══════════════════════════════════════════════════════════════════


@retry_with_backoff(max_retries=3)
def get_deribit_instruments(currency: str = "BTC") -> list[dict]:
    """Obtiene todos los instrumentos de opciones activos de Deribit."""
    resp = requests.get(
        DERIBIT_INSTRUMENTS_URL,
        params={"currency": currency, "kind": "option", "expired": "false"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", [])


@retry_with_backoff(max_retries=3)
def get_deribit_ticker(instrument_name: str) -> dict:
    """Obtiene el ticker (con greeks) de un instrumento de Deribit."""
    resp = requests.get(
        DERIBIT_TICKER_URL,
        params={"instrument_name": instrument_name},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", {})


def find_closest_option(
    instruments: list[dict],
    target_strike: float,
    target_expiry_ts: float,
    direction: str = "above",
) -> str | None:
    """
    Encuentra el contrato de opciones más cercano al strike y
    vencimiento objetivo. Busca calls para "above", puts para "below".
    """
    option_type = "call" if direction == "above" else "put"

    best_instrument = None
    best_score = float("inf")

    for inst in instruments:
        inst_name = inst.get("instrument_name", "")
        inst_strike = inst.get("strike", 0)
        inst_expiry = inst.get("expiration_timestamp", 0) / 1000  # ms → s

        # Filtrar por tipo (C=call, P=put en el nombre)
        if option_type == "call" and "-C" not in inst_name:
            continue
        if option_type == "put" and "-P" not in inst_name:
            continue

        # Score: distancia normalizada en strike + distancia en tiempo
        strike_diff = abs(inst_strike - target_strike) / max(target_strike, 1)
        time_diff = abs(inst_expiry - target_expiry_ts) / 86400  # en días

        score = strike_diff * 100 + time_diff  # ponderar más el strike

        if score < best_score:
            best_score = score
            best_instrument = inst_name

    # Solo aceptar si el score es razonable
    if best_score > 50:  # strike >50% off o tiempo >50 días off
        logger.debug(
            "No se encontró opción cercana (score=%.1f) para strike=%.0f",
            best_score, target_strike
        )
        return None

    return best_instrument


def get_prob_deribit(
    asset: str, strike: float, expiry_timestamp: float, direction: str
) -> float | None:
    """
    Obtiene la probabilidad implícita desde Deribit options.
    Usa la delta del option como proxy de probabilidad.
    Delta de call ≈ P(ITM) = P(S>K), delta de put ≈ -P(S<K).
    """
    currency = asset  # "BTC" o "ETH"
    try:
        instruments = get_deribit_instruments(currency)
        if not instruments:
            logger.warning("No se encontraron instrumentos Deribit para %s", currency)
            return None

        closest = find_closest_option(instruments, strike, expiry_timestamp, direction)
        if not closest:
            logger.info("No hay opción Deribit cercana para %s@$%.0f", asset, strike)
            return None

        ticker = get_deribit_ticker(closest)
        greeks = ticker.get("greeks", {})
        delta = greeks.get("delta")

        if delta is None:
            logger.warning("No se obtuvo delta para %s", closest)
            return None

        # Delta de call ≈ P(above), delta de put es negativa
        prob = abs(float(delta))

        # Si es un put y queremos "above", invertir
        if "-P" in closest and direction == "above":
            prob = 1.0 - prob
        elif "-C" in closest and direction == "below":
            prob = 1.0 - prob

        prob = max(0.0, min(1.0, prob))  # clamp

        logger.info(
            "Deribit %s: instrumento=%s, delta=%.4f → prob_%s=%.3f",
            asset, closest, float(delta), direction, prob
        )
        return prob

    except Exception as e:
        logger.error("Error obteniendo prob de Deribit para %s: %s", asset, e)
        return None


# ═══════════════════════════════════════════════════════════════════
# 2c. Sentimiento macro — Fear & Greed Index
# ═══════════════════════════════════════════════════════════════════


@retry_with_backoff(max_retries=3)
def get_fear_greed_index() -> int:
    """
    Obtiene el valor actual del Crypto Fear & Greed Index (0-100).
    """
    resp = requests.get(FNG_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    value = int(data["data"][0]["value"])
    classification = data["data"][0].get("value_classification", "")
    logger.info("Fear & Greed Index: %d (%s)", value, classification)
    return value


def calc_sentiment_adjustment(fng_value: int) -> float:
    """
    Mapea el Fear & Greed Index a un ajuste de probabilidad:
      - Fear (<30)  → -0.03 (mercado pesimista, reduce prob de subir)
      - Greed (>70) → +0.03 (mercado optimista, aumenta prob de subir)
      - Neutral     →  0.00
    """
    if fng_value < 30:
        return -0.03
    elif fng_value > 70:
        return 0.03
    return 0.0


def get_prob_sentiment(direction: str) -> float | None:
    """
    Obtiene la probabilidad base ajustada por sentimiento.
    Para "above": 0.50 + ajuste
    Para "below": 0.50 - ajuste
    El sentimiento solo aplica como offset sobre un prior de 50%.
    """
    try:
        fng = get_fear_greed_index()
        adjustment = calc_sentiment_adjustment(fng)

        if direction == "above":
            prob = 0.50 + adjustment
        else:
            prob = 0.50 - adjustment

        logger.info(
            "Sentimiento: FNG=%d, ajuste=%.2f%%, prob_%s=%.3f",
            fng, adjustment * 100, direction, prob
        )
        return prob
    except Exception as e:
        logger.error("Error obteniendo sentimiento: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
# Combinación final de las 3 fuentes
# ═══════════════════════════════════════════════════════════════════


def calc_real_probability(
    asset: str,
    strike: float,
    days_to_expiry: float,
    expiry_timestamp: float,
    direction: str,
) -> tuple[float | None, dict]:
    """
    Combina las 3 fuentes de probabilidad con pesos ponderados:
      prob_real = w1*GBM + w2*Deribit + w3*Sentimiento

    Si una fuente falla, redistribuye su peso entre las restantes.

    Retorna:
      - prob_real (float o None si todo falla)
      - details (dict con cada componente para logging)
    """
    details = {
        "prob_gbm": None,
        "prob_deribit": None,
        "prob_sentiment": None,
        "weights_used": {},
    }

    # ── Obtener cada fuente ──
    prob_gbm = get_prob_gbm(asset, strike, days_to_expiry, direction)
    prob_deribit = get_prob_deribit(asset, strike, expiry_timestamp, direction)
    prob_sentiment = get_prob_sentiment(direction)

    details["prob_gbm"] = prob_gbm
    details["prob_deribit"] = prob_deribit
    details["prob_sentiment"] = prob_sentiment

    # ── Construir pesos dinámicos ──
    sources = {}
    if prob_gbm is not None:
        sources["gbm"] = (prob_gbm, WEIGHT_GBM)
    if prob_deribit is not None:
        sources["deribit"] = (prob_deribit, WEIGHT_DERIBIT)
    if prob_sentiment is not None:
        sources["sentiment"] = (prob_sentiment, WEIGHT_SENTIMENT)

    if not sources:
        logger.error("Ninguna fuente de probabilidad disponible para %s@$%.0f", asset, strike)
        return None, details

    # Redistribuir pesos si alguna fuente falta
    total_weight = sum(w for _, w in sources.values())
    weighted_sum = sum(p * (w / total_weight) for p, w in sources.values())

    details["weights_used"] = {
        name: round(w / total_weight, 3) for name, (_, w) in sources.items()
    }

    prob_real = max(0.01, min(0.99, weighted_sum))  # clamp entre 1%-99%

    logger.info(
        "Probabilidad real %s@$%.0f (%s): %.3f | GBM=%.3f, Deribit=%s, Sent=%.3f | Pesos=%s",
        asset, strike, direction, prob_real,
        prob_gbm or 0,
        f"{prob_deribit:.3f}" if prob_deribit else "N/A",
        prob_sentiment or 0.5,
        details["weights_used"],
    )

    return prob_real, details
