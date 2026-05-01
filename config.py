"""
config.py — Configuración centralizada del bot.
Carga variables de entorno desde .env y define constantes globales.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# ── Polymarket credentials ──────────────────────────────────────
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
POLY_API_SECRET = os.getenv("POLY_API_SECRET", "")
POLY_API_PASSPHRASE = os.getenv("POLY_API_PASSPHRASE", "")

# ── Trading parameters ─────────────────────────────────────────
PAPER_CAPITAL = float(os.getenv("PAPER_CAPITAL", "1000"))
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.06"))
MAX_STAKE_PCT = 0.05          # Máximo 5% del capital por trade
MIN_STAKE = 5.0               # Mínimo $5 por trade
KELLY_FRACTION = 0.25         # Kelly fraccionado (cuarto de Kelly)

# ── Filtros de oportunidad ──────────────────────────────────────
MIN_VOLUME_24H = 1_000        # Volumen mínimo en USD (lifetime, no 24h)
MIN_DAYS_EXPIRY = 1
MAX_DAYS_EXPIRY = 365          # Mercados crypto suelen tener vencimientos largos

# ── Pesos de las fuentes de probabilidad ────────────────────────
WEIGHT_GBM = 0.4
WEIGHT_DERIBIT = 0.4
WEIGHT_SENTIMENT = 0.2

# ── API endpoints ───────────────────────────────────────────────
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"
POLYMARKET_CHAIN_ID = 137
BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
DERIBIT_INSTRUMENTS_URL = "https://www.deribit.com/api/v2/public/get_instruments"
DERIBIT_TICKER_URL = "https://www.deribit.com/api/v2/public/ticker"
FNG_URL = "https://api.alternative.me/fng/"

# ── Archivos locales ────────────────────────────────────────────
PAPER_TRADES_FILE = "paper_trades.json"
LOG_FILE = "bot.log"

# ── Crypto keywords para filtrar mercados ───────────────────────
CRYPTO_KEYWORDS = ["BTC", "ETH", "bitcoin", "ethereum", "price"]

# ── Logging setup ───────────────────────────────────────────────
def setup_logging():
    """Configura logging a archivo y consola."""
    logger = logging.getLogger("polybot")
    logger.setLevel(logging.DEBUG)

    # File handler (INFO+)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    ))

    # Console handler (WARNING+)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
