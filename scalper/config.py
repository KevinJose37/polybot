"""
scalper/config.py — Configuración específica para el bot HFT de 5 minutos.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Trade sizing ────────────────────────────────────────────────
HFT_STAKE = float(os.getenv("HFT_STAKE", "10.0"))          # USD per trade
HFT_CAPITAL = float(os.getenv("HFT_CAPITAL", "1000.0"))     # Starting capital
HFT_MAX_CONCURRENT = int(os.getenv("HFT_MAX_CONCURRENT", "4"))  # Max positions

# ── Signal thresholds ──────────────────────────────────────────
HFT_SIGNAL_THRESHOLD = float(os.getenv("HFT_SIGNAL_THRESHOLD", "0.40"))

# ── Timing (seconds) ──────────────────────────────────────────
HFT_POLL_INTERVAL = int(os.getenv("HFT_POLL_INTERVAL", "10"))
HFT_ENTRY_WINDOW_MIN = 60      # Enter at least 60s before eventStartTime
HFT_ENTRY_WINDOW_MAX = 180     # Enter at most 180s before eventStartTime

# ── Exit rules ─────────────────────────────────────────────────
HFT_EARLY_EXIT_PROFIT = 0.15   # Take profit at 15% gain on position
HFT_STOP_LOSS = 0.30           # Cut loss at 30% drop on position
HFT_EARLY_EXIT_REVERSAL = 0.60 # Exit if signal reverses past this
HFT_SESSION_STOP_LOSS = 0.20   # Stop trading at 20% session loss

# ── REST orderbook filters ─────────────────────────────────────
HFT_MAX_SPREAD = 0.03          # Max bid-ask spread for entry (skip if wider)
HFT_TRADEABLE_ASSETS = ["BTC", "ETH", "SOL", "XRP"]  # REST spread filter handles liquidity

# ── Simulation flags ───────────────────────────────────────────
HOLD_ONLY = False              # When True, skip all sells → hold to resolution

# ── Persistence ────────────────────────────────────────────────
HFT_TRADES_FILE = os.getenv("HFT_TRADES_FILE", "hft_trades.json")

# ── API endpoints ──────────────────────────────────────────────
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# ── Asset definitions ──────────────────────────────────────────
HFT_ASSETS = {
    "BTC": {
        "series_slug": "btc-up-or-down",
        "binance_symbol": "BTCUSDT",
        "name": "Bitcoin",
    },
    "ETH": {
        "series_slug": "eth-up-or-down",
        "binance_symbol": "ETHUSDT",
        "name": "Ethereum",
    },
    "SOL": {
        "series_slug": "sol-up-or-down",
        "binance_symbol": "SOLUSDT",
        "name": "Solana",
    },
    "XRP": {
        "series_slug": "xrp-up-or-down",
        "binance_symbol": "XRPUSDT",
        "name": "XRP",
    },
}
