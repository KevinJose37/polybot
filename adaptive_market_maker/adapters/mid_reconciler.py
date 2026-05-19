"""Reconciler to flag divergence between Polymarket and external references."""

import re
import math
import time
import scipy.stats
import structlog
from dataclasses import dataclass

logger = structlog.get_logger(__name__)

@dataclass
class ReconcilerResult:
    diverged: bool
    reason: str = ""
    magnitude: float = 0.0

def reconcile(
    poly_mid: float,
    binance_spot: float,
    strike: float | None,
    sigma: float,           # from your EWMA vol engine, tick-level (NOT annualized)
    time_to_expiry_years: float,
    divergence_threshold: float = 0.06   # 6 probability points
) -> ReconcilerResult:
    if strike is None:
        return ReconcilerResult(diverged=False)
    # Layer 1: directional sanity check (cheap, catches gross errors)
    spot_above_strike = binance_spot > strike
    if spot_above_strike and poly_mid < 0.40:
        return ReconcilerResult(diverged=True, reason="spot_above_strike_but_poly_bearish")
    if not spot_above_strike and poly_mid > 0.60:
        return ReconcilerResult(diverged=True, reason="spot_below_strike_but_poly_bullish")

    # Layer 2: quantitative check near the strike (when it matters)
    # F-15: Annualize sigma — EWMA vol is tick-level (~1 tick/second).
    # seconds_per_year ≈ 365.25 * 24 * 3600 = 31,557,600
    # annualized_sigma = sigma * sqrt(seconds_per_year)
    SECONDS_PER_YEAR = 365.25 * 24 * 3600
    sigma_annual = sigma * math.sqrt(SECONDS_PER_YEAR)
    sigma_t = sigma_annual * math.sqrt(time_to_expiry_years)
    if sigma_t > 1e-6:      # avoid division by zero at expiry
        d = math.log(binance_spot / strike) / sigma_t
        theoretical_prob = scipy.stats.norm.cdf(d)
        divergence = abs(poly_mid - theoretical_prob)
        if divergence > divergence_threshold:
            return ReconcilerResult(diverged=True, reason="prob_divergence", magnitude=divergence)

    return ReconcilerResult(diverged=False)

def parse_strike_from_question(question: str) -> float | None:
    # "Will ETH be above $3,200 at..." or "Will BTC exceed $95,000..."
    match = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)', question)
    if not match:
        return None
    return float(match.group(1).replace(',', ''))

@dataclass
class ReconcilerConfig:
    divergence_threshold: float = 0.06

class MidReconciler:
    def __init__(self, config: ReconcilerConfig):
        self.config = config
        self.spot_mids: dict[str, float] = {}
        self.spot_timestamps: dict[str, float] = {}

    def update_spot_mid(self, asset: str, mid: float, timestamp: float | None = None) -> None:
        self.spot_mids[asset] = mid
        self.spot_timestamps[asset] = timestamp if timestamp is not None else time.time()

    def update_polymarket_mid(
        self,
        market_id: str,
        pm_mid: float,
        asset: str,
        strike: float | None,
        sigma: float,
        time_to_expiry_years: float
    ) -> bool:
        if asset not in self.spot_mids:
            return False
            
        result = reconcile(
            poly_mid=pm_mid,
            binance_spot=self.spot_mids[asset],
            strike=strike,
            sigma=sigma,
            time_to_expiry_years=time_to_expiry_years,
            divergence_threshold=self.config.divergence_threshold
        )
        return result.diverged
