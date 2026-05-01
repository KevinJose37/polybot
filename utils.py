"""
utils.py — Utilidades compartidas: retry con backoff, parsing de preguntas.
"""

import re
import time
import logging
import functools
from datetime import datetime

logger = logging.getLogger("polybot.utils")


def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
    """
    Decorador que reintenta una función hasta max_retries veces
    con backoff exponencial ante cualquier excepción.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        logger.error(
                            "Función %s falló tras %d intentos: %s",
                            func.__name__, max_retries + 1, e
                        )
                        raise
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "Intento %d/%d de %s falló (%s). Reintentando en %.1fs…",
                        attempt + 1, max_retries + 1, func.__name__, e, delay
                    )
                    time.sleep(delay)
        return wrapper
    return decorator


# ── Regex patterns para parsear preguntas de Polymarket ─────────

# Ejemplos de preguntas que debemos parsear:
#   "Will BTC be above $100,000 on June 30?"
#   "Will the price of Bitcoin be below $80000 by Dec 31, 2025?"
#   "Will ETH exceed $5,000 before July 2025?"

# Pattern para extraer el precio strike de la pregunta
# Soporta: $100,000  $150k  $1m  $1b  $5,000.50  $80000
PRICE_PATTERN = re.compile(
    r'\$\s?([\d,]+(?:\.\d+)?)\s*([kmbtKMBT])?',
    re.IGNORECASE
)

# Multiplicadores para sufijos
SUFFIX_MULTIPLIERS = {
    'k': 1_000,
    'm': 1_000_000,
    'b': 1_000_000_000,
    't': 1_000_000_000_000,
}

# Pattern para detectar dirección (above/below)
DIRECTION_PATTERNS = {
    "above": re.compile(
        r'\b(above|over|exceed|higher than|reach|hit|surpass|more than|at least|hold)\b',
        re.IGNORECASE
    ),
    "below": re.compile(
        r'\b(below|under|lower than|less than|fall below|drop below|beneath)\b',
        re.IGNORECASE
    ),
}

# Pattern para detectar asset
ASSET_PATTERNS = {
    "BTC": re.compile(r'\b(BTC|bitcoin)\b', re.IGNORECASE),
    "ETH": re.compile(r'\b(ETH|ethereum|ether)\b', re.IGNORECASE),
}


def parse_question(question: str) -> dict | None:
    """
    Parsea la pregunta de un mercado de Polymarket para extraer:
      - asset: 'BTC' o 'ETH'
      - strike: precio numérico (float), soporta sufijos k/m/b
      - direction: 'above' o 'below'
    
    Retorna None si no puede parsear alguno de los campos.
    """
    result = {}

    # Detectar asset
    for asset, pattern in ASSET_PATTERNS.items():
        if pattern.search(question):
            result["asset"] = asset
            break
    else:
        logger.debug("No se detectó asset en: %s", question)
        return None

    # Extraer strike price (con soporte para sufijos k/m/b)
    price_match = PRICE_PATTERN.search(question)
    if not price_match:
        logger.debug("No se detectó precio strike en: %s", question)
        return None
    
    price_str = price_match.group(1).replace(",", "")
    base_price = float(price_str)
    
    suffix = price_match.group(2)
    if suffix:
        multiplier = SUFFIX_MULTIPLIERS.get(suffix.lower(), 1)
        base_price *= multiplier
    
    result["strike"] = base_price
    logger.debug("Precio parseado de '%s': $%s%s → $%.2f", 
                 question[:50], price_str, suffix or "", base_price)

    # Detectar dirección
    for direction, pattern in DIRECTION_PATTERNS.items():
        if pattern.search(question):
            result["direction"] = direction
            break
    else:
        # Si no se detecta dirección explícita, asumir "above"
        result["direction"] = "above"
        logger.debug("Dirección no detectada, asumiendo 'above' para: %s", question)

    return result


def parse_date(date_str: str) -> datetime | None:
    """Intenta parsear una fecha ISO o timestamp."""
    if not date_str:
        return None
    try:
        # Manejar timestamps numéricos
        if isinstance(date_str, (int, float)):
            return datetime.fromtimestamp(date_str)
        # ISO format
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            logger.debug("No se pudo parsear fecha: %s", date_str)
            return None


def format_pct(value: float) -> str:
    """Formatea un valor como porcentaje con 1 decimal."""
    return f"{value * 100:.1f}%"


def format_usd(value: float) -> str:
    """Formatea un valor como USD."""
    return f"${value:,.2f}"
