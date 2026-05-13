"""
core/fair_value.py — Fair value computation for binary prediction markets.
Uses Black-Scholes-style binary option pricing: P(S_T > K) = N(d2).
"""

import math
import time
from typing import Optional

from scipy import stats as sp_stats
from loguru import logger

from core.volatility import VolatilityEstimator, get_binance_spot_price
from core.tau import TauCalculator
from utils.schemas import FairValueEstimate


class FairValueEngine:
    """
    Computes fair value (probability) for binary prediction markets.

    Fair value = N(d2) where:
        d2 = [ln(S/K) + (r - sigma^2/2) * T] / (sigma * sqrt(T))

    The fair value is updated when:
    - Spot price moves more than FV_UPDATE_THRESHOLD_BPS
    - Fair value estimate is older than MAX_FV_AGE_MS
    """

    # Map trading symbol to Binance symbol
    SYMBOL_MAP = {
        "btcusdt": "BTCUSDT",
        "ethusdt": "ETHUSDT",
        "solusdt": "SOLUSDT",
        "xrpusdt": "XRPUSDT",
    }

    def __init__(
        self,
        fv_update_threshold_bps: float = 5.0,
        max_fv_age_ms: int = 500,
        risk_free_rate: float = 0.05,
    ):
        self.fv_update_threshold_bps = fv_update_threshold_bps
        self.max_fv_age_ms = max_fv_age_ms
        self.risk_free_rate = risk_free_rate
        self.vol_estimator = VolatilityEstimator()

        # Cache per (asset, strike): last spot, last FV
        self._last_spot: dict[str, float] = {}
        self._last_fv: dict[tuple[str, float], FairValueEstimate] = {}

    def compute_fair_value(
        self,
        asset: str,
        strike: float,
        tau_seconds: float,
        spot_override: Optional[float] = None,
        vol_override: Optional[float] = None,
    ) -> Optional[FairValueEstimate]:
        """
        Compute fair value probability for a binary option.

        Args:
            asset: Trading pair (e.g., "btcusdt")
            strike: Strike price
            tau_seconds: Time to expiry in seconds
            spot_override: Use this spot price instead of fetching from Binance
            vol_override: Use this volatility instead of computing from Binance

        Returns:
            FairValueEstimate or None if computation fails
        """
        now_ms = int(time.time() * 1000)

        # Get spot price
        if spot_override is not None:
            spot = spot_override
        else:
            binance_symbol = self.SYMBOL_MAP.get(asset.lower())
            if not binance_symbol:
                logger.warning(f"[FV] Unknown asset: {asset}")
                return None
            spot = get_binance_spot_price(binance_symbol)
            if spot is None:
                return None

        # Check if FV needs recomputation
        cache_key = (asset, strike)
        if not self._needs_update(cache_key, spot, now_ms):
            cached = self._last_fv.get(cache_key)
            if cached:
                return cached

        # Get volatility
        if vol_override is not None:
            vol = vol_override
        else:
            binance_symbol = self.SYMBOL_MAP.get(asset.lower(), asset.upper())
            _, ewma_vol = self.vol_estimator.get_volatility(binance_symbol)
            vol = ewma_vol  # Use EWMA for faster regime detection

        # Compute probability
        prob = self._binary_option_prob(spot, strike, vol, tau_seconds)

        fv = FairValueEstimate(
            probability=prob,
            spot_price=spot,
            strike=strike,
            volatility=vol,
            tau_seconds=tau_seconds,
            timestamp_ms=now_ms,
            is_stale=False,
        )

        # Update cache
        self._last_spot[cache_key] = spot
        self._last_fv[cache_key] = fv

        return fv

    def _binary_option_prob(
        self,
        spot: float,
        strike: float,
        vol: float,
        tau_seconds: float,
    ) -> float:
        """
        Black-Scholes binary option probability: P(S_T > K) = N(d2)

        d2 = [ln(S/K) + (r - sigma^2/2) * T] / (sigma * sqrt(T))

        For "Up" markets (will price be above strike), this gives the probability.
        """
        if tau_seconds <= 0:
            # Expired: deterministic
            return 1.0 if spot > strike else 0.0

        T = TauCalculator.tau_to_years(tau_seconds)

        if vol <= 0:
            vol = 0.01  # Prevent division by zero

        if T <= 0:
            return 1.0 if spot > strike else 0.0

        try:
            d2 = (
                math.log(spot / strike) + (self.risk_free_rate - 0.5 * vol ** 2) * T
            ) / (vol * math.sqrt(T))

            prob = float(sp_stats.norm.cdf(d2))

            # Clamp to [0.01, 0.99] — never quote certainty
            prob = max(0.01, min(0.99, prob))

            return prob

        except (ValueError, ZeroDivisionError) as e:
            logger.error(f"[FV] Math error: spot={spot}, K={strike}, vol={vol}, T={T}: {e}")
            return 0.5  # Uninformative prior

    def _needs_update(self, cache_key: tuple, current_spot: float, now_ms: int) -> bool:
        """Check if fair value needs recomputation."""
        # No previous FV → always update
        if cache_key not in self._last_fv or cache_key not in self._last_spot:
            return True

        cached_fv = self._last_fv[cache_key]

        # Age check
        age_ms = now_ms - cached_fv.timestamp_ms
        if age_ms >= self.max_fv_age_ms:
            return True

        # Spot price change check
        last_spot = self._last_spot[cache_key]
        if last_spot > 0:
            move_bps = abs(current_spot - last_spot) / last_spot * 10000
            if move_bps >= self.fv_update_threshold_bps:
                return True

        return False

    def get_cached_fv(self, asset: str, strike: float = 0) -> Optional[FairValueEstimate]:
        """Get the last computed fair value without recomputing."""
        return self._last_fv.get((asset, strike))

    def mark_stale(self, asset: str):
        """Mark all fair values for an asset as stale (e.g., feed went down)."""
        for key in self._last_fv:
            if key[0] == asset:
                self._last_fv[key].is_stale = True
