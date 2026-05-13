"""
core/volatility.py — Real-time volatility estimation.
Uses Binance kline data with both historical (30d) and EWMA approaches.
"""

import time
import math
from typing import Optional

import numpy as np
import requests
from loguru import logger


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"


class VolatilityEstimator:
    """
    Estimates annualized volatility from Binance historical data.

    Two methods:
    1. Historical: Standard deviation of log-returns over N days
    2. EWMA: Exponentially-weighted moving average for faster regime detection
    """

    def __init__(self, lookback_days: int = 30, ewma_halflife: int = 10):
        self.lookback_days = lookback_days
        self.ewma_halflife = ewma_halflife  # Days for EWMA half-life
        self._cache: dict[str, tuple[float, float, float]] = {}  # symbol -> (hist_vol, ewma_vol, ts)
        self._cache_ttl = 300  # 5 minutes cache

    def get_volatility(self, symbol: str) -> tuple[float, float]:
        """
        Returns (historical_vol, ewma_vol) annualized for the given symbol.
        Uses cached value if fresh enough.
        """
        now = time.time()
        if symbol in self._cache:
            hist, ewma, cached_at = self._cache[symbol]
            if now - cached_at < self._cache_ttl:
                return hist, ewma

        hist_vol, ewma_vol = self._compute_volatility(symbol)
        self._cache[symbol] = (hist_vol, ewma_vol, now)
        return hist_vol, ewma_vol

    def _compute_volatility(self, symbol: str) -> tuple[float, float]:
        """Fetch klines and compute both historical and EWMA volatility."""
        try:
            resp = requests.get(
                BINANCE_KLINES_URL,
                params={
                    "symbol": symbol.upper(),
                    "interval": "1h",  # Hourly klines for better granularity
                    "limit": min(self.lookback_days * 24, 1000),
                },
                timeout=10,
            )
            resp.raise_for_status()
            klines = resp.json()

            if len(klines) < 10:
                logger.warning(f"Insufficient kline data for {symbol}, using default vol")
                return 0.6, 0.6  # Default 60% annualized

            # Extract close prices (index 4 in kline array)
            closes = np.array([float(k[4]) for k in klines])

            # Log returns
            log_returns = np.diff(np.log(closes))

            if len(log_returns) < 2:
                return 0.6, 0.6

            # Historical volatility (standard deviation of hourly returns → annualized)
            hourly_vol = float(np.std(log_returns, ddof=1))
            hist_vol = hourly_vol * math.sqrt(365 * 24)  # Annualize from hourly

            # EWMA volatility (exponentially weighted)
            decay = 1 - math.exp(-math.log(2) / (self.ewma_halflife * 24))  # Hourly decay
            ewma_var = float(log_returns[0] ** 2)
            for r in log_returns[1:]:
                ewma_var = decay * (r ** 2) + (1 - decay) * ewma_var
            ewma_vol = math.sqrt(ewma_var) * math.sqrt(365 * 24)

            # Clamp to reasonable range
            hist_vol = max(0.05, min(5.0, hist_vol))
            ewma_vol = max(0.05, min(5.0, ewma_vol))

            logger.info(
                f"[Vol] {symbol}: hist={hist_vol:.4f}, ewma={ewma_vol:.4f} (annualized)"
            )
            return hist_vol, ewma_vol

        except Exception as e:
            logger.error(f"[Vol] Error computing volatility for {symbol}: {e}")
            return 0.6, 0.6  # Safe fallback


def get_binance_spot_price(symbol: str) -> Optional[float]:
    """
    Fetch current spot price from Binance REST API.
    Returns None on failure.
    """
    try:
        resp = requests.get(
            BINANCE_PRICE_URL,
            params={"symbol": symbol.upper()},
            timeout=10,
        )
        resp.raise_for_status()
        price = float(resp.json()["price"])
        return price
    except Exception as e:
        logger.error(f"[Price] Error fetching {symbol} price: {e}")
        return None
