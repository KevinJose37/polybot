"""
strategy.py — MÓDULOS 3 y 4: Filtro de oportunidades + Sizing con Kelly fraccionado.
"""

import logging
from datetime import datetime, timezone

from config import (
    MIN_EDGE,
    MIN_VOLUME_24H,
    MIN_DAYS_EXPIRY,
    MAX_DAYS_EXPIRY,
    MAX_STAKE_PCT,
    MIN_STAKE,
    KELLY_FRACTION,
    PAPER_CAPITAL,
)
from probability import calc_real_probability

logger = logging.getLogger("polybot.strategy")


# ═══════════════════════════════════════════════════════════════════
# MÓDULO 3 — Filtro de oportunidades
# ═══════════════════════════════════════════════════════════════════


def calc_days_to_expiry(end_date: datetime | None) -> float:
    """Calcula los días restantes hasta el vencimiento."""
    if not end_date:
        return -1
    now = datetime.now(timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    delta = end_date - now
    return max(0, delta.total_seconds() / 86400)


def filter_market(market: dict) -> bool:
    """
    Aplica los filtros de oportunidad a un mercado:
    - Pregunta contiene precio numérico parseable (ya filtrado antes)
    - Volumen total > MIN_VOLUME (nota: Gamma API da volumen lifetime)
    - Días para vencer: entre MIN_DAYS y MAX_DAYS
    - Si no tiene end_date, se acepta igualmente (algunos mercados no la tienen)
    """
    question_short = market.get("question", "")[:60]

    # Volumen (lifetime en Polymarket, no 24h)
    volume = market.get("volume_24h", 0)
    if volume < MIN_VOLUME_24H:
        logger.info(
            "Filtrado por volumen ($%.0f < $%.0f): %s",
            volume, MIN_VOLUME_24H, question_short
        )
        return False

    # Días para vencer
    days = calc_days_to_expiry(market.get("end_date"))
    if days == -1:
        # Sin fecha de vencimiento — aceptar (muchos mercados crypto no la tienen)
        logger.info("Sin end_date, aceptando: %s", question_short)
        return True

    if days < MIN_DAYS_EXPIRY:
        logger.info(
            "Filtrado por expiración cercana (%.1f < %d días): %s",
            days, MIN_DAYS_EXPIRY, question_short
        )
        return False

    if days > MAX_DAYS_EXPIRY:
        logger.info(
            "Filtrado por expiración lejana (%.1f > %d días): %s",
            days, MAX_DAYS_EXPIRY, question_short
        )
        return False

    logger.info("Mercado pasa filtros: %s (vol=$%.0f, días=%.0f)", question_short, volume, days)
    return True


# ═══════════════════════════════════════════════════════════════════
# MÓDULO 4 — Sizing con Kelly fraccionado
# ═══════════════════════════════════════════════════════════════════


def calc_kelly_stake(
    prob_real: float,
    prob_poly: float,
    capital: float,
) -> tuple[float, float, str]:
    """
    Calcula el stake usando Kelly fraccionado.

    kelly = max(0, (p*b - q) / b)
    stake = capital * kelly * KELLY_FRACTION

    Donde:
      p = prob_real (nuestra estimación de probabilidad real)
      q = 1 - p
      b = (1/prob_poly) - 1  (odds implícitas del mercado)

    Retorna: (stake, kelly_full, side)
      - side: 'YES' si prob_real > prob_poly (comprar YES barato)
              'NO' si prob_real < prob_poly (comprar NO barato)
    """
    # Determinar dirección del trade
    if prob_real > prob_poly:
        # El mercado subestima la probabilidad → comprar YES
        p = prob_real
        market_p = prob_poly
        side = "YES"
    else:
        # El mercado sobreestima → comprar NO
        # Invertir: prob de NO ganar = 1 - prob_real
        p = 1.0 - prob_real
        market_p = 1.0 - prob_poly
        side = "NO"

    q = 1.0 - p

    # Odds del mercado para el lado que compramos
    if market_p <= 0 or market_p >= 1:
        return 0.0, 0.0, side

    b = (1.0 / market_p) - 1.0  # decimal odds - 1

    if b <= 0:
        return 0.0, 0.0, side

    # Kelly criterion
    kelly_full = max(0.0, (p * b - q) / b)

    # Kelly fraccionado (cuarto de Kelly)
    stake = capital * kelly_full * KELLY_FRACTION

    # Aplicar límites
    max_stake = capital * MAX_STAKE_PCT
    stake = min(stake, max_stake)

    if stake < MIN_STAKE:
        stake = 0.0  # No vale la pena

    logger.info(
        "Kelly: p=%.3f, q=%.3f, b=%.3f, kelly=%.4f, stake=$%.2f, side=%s",
        p, q, b, kelly_full, stake, side
    )

    return stake, kelly_full, side


# ═══════════════════════════════════════════════════════════════════
# Pipeline: analizar mercados y generar oportunidades
# ═══════════════════════════════════════════════════════════════════


def analyze_market(market: dict, capital: float) -> dict | None:
    """
    Analiza un mercado individual:
    1. Aplica filtros
    2. Calcula probabilidad real
    3. Calcula edge
    4. Si edge > MIN_EDGE, calcula sizing y retorna la oportunidad

    Retorna dict con toda la info o None si no es operable.
    """
    if not filter_market(market):
        return None

    parsed = market.get("parsed", {})
    asset = parsed.get("asset")
    strike = parsed.get("strike")
    direction = parsed.get("direction", "above")

    if not asset or not strike:
        return None

    days_to_expiry = calc_days_to_expiry(market.get("end_date"))
    end_date = market.get("end_date")
    expiry_ts = end_date.timestamp() if end_date else 0

    # Calcular probabilidad real combinada
    prob_real, prob_details = calc_real_probability(
        asset, strike, days_to_expiry, expiry_ts, direction
    )

    if prob_real is None:
        return None

    prob_poly = market.get("prob_poly", 0.5)

    # Edge: diferencia entre nuestra estimación y la del mercado
    edge = prob_real - prob_poly

    # Solo considerar si el edge supera el umbral mínimo
    if abs(edge) < MIN_EDGE:
        logger.debug(
            "Edge insuficiente (%.1f%% < %.1f%%): %s",
            abs(edge) * 100, MIN_EDGE * 100, market.get("question", "")[:50]
        )
        return None

    # Calcular sizing
    stake, kelly, side = calc_kelly_stake(prob_real, prob_poly, capital)

    if stake <= 0:
        logger.debug("Stake = 0 para: %s", market.get("question", "")[:50])
        return None

    entry_price = prob_poly if side == "YES" else (1 - prob_poly)

    opportunity = {
        "condition_id": market.get("condition_id"),
        "gamma_id": market.get("gamma_id", ""),
        "question": market.get("question"),
        "end_date": market.get("end_date"),
        "end_date_str": market.get("end_date_str", ""),
        "asset": asset,
        "strike": strike,
        "direction": direction,
        "prob_poly": prob_poly,
        "prob_real": prob_real,
        "edge": edge,
        "abs_edge": abs(edge),
        "side": side,
        "entry_price": entry_price,
        "stake": stake,
        "kelly": kelly,
        "volume_24h": market.get("volume_24h", 0),
        "prob_details": prob_details,
        "days_to_expiry": days_to_expiry,
    }

    logger.info(
        "✅ Oportunidad: %s | Edge=%.1f%% | Side=%s | Stake=$%.2f",
        market.get("question", "")[:50], edge * 100, side, stake
    )

    return opportunity


def scan_opportunities(markets: list[dict], capital: float) -> list[dict]:
    """
    Escanea todos los mercados y retorna las oportunidades
    ordenadas por abs(edge) descendente.
    """
    opportunities = []
    for m in markets:
        opp = analyze_market(m, capital)
        if opp:
            opportunities.append(opp)

    # Ordenar por edge absoluto descendente
    opportunities.sort(key=lambda x: x["abs_edge"], reverse=True)

    logger.info(
        "Scan completo: %d oportunidades de %d mercados",
        len(opportunities), len(markets)
    )
    return opportunities
