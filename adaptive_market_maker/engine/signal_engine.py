"""Signal Engine for Volatility and Probabilities."""

import math
from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

MAX_DELTA_T_SECONDS = 60.0

@dataclass
class EWMAState:
    variance: float
    last_mid: float
    last_timestamp: float  # unix seconds, float precision


def update_ewma(state: EWMAState, mid: float, now: float, tau: float) -> EWMAState:
    """Update EWMA variance with time-decay weighting."""
    delta_t = min(now - state.last_timestamp, MAX_DELTA_T_SECONDS)
    # If time went backwards (e.g. out of order ticks), clamp to 0
    delta_t = max(delta_t, 0.0)
    
    delta_price = mid - state.last_mid

    lambda_eff = math.exp(-delta_t / tau)
    variance = lambda_eff * state.variance + (1 - lambda_eff) * (delta_price ** 2)

    return EWMAState(variance=variance, last_mid=mid, last_timestamp=now)


def get_volatility(state: EWMAState) -> float:
    """Return volatility (standard deviation) from EWMA variance."""
    # Guard against precision errors resulting in very small negative numbers
    return math.sqrt(max(state.variance, 0.0))


def get_implied_probability(bid: float, ask: float) -> float:
    """
    Calculate mid-price and strictly clamp to [0.001, 0.999] bounds.
    """
    mid = (bid + ask) / 2.0
    return max(0.001, min(0.999, mid))


class SignalEngine:
    """
    Tracks and updates market volatility signals per market.
    """
    def __init__(
        self,
        tau: float,
        min_spread: float,
        warm_up_seconds: float = 300.0,
        warm_up_min_obs: int = 60
    ):
        self.tau = tau
        self.min_spread = min_spread
        self.warm_up_seconds = warm_up_seconds
        self.warm_up_min_obs = warm_up_min_obs
        
        # Internal state: market_id -> EWMAState
        self._states: dict[str, EWMAState] = {}
        # Tracking variables for warm up: market_id -> (start_time, observation_count)
        self._warm_up_trackers: dict[str, dict[str, float]] = {}

    def update_market(self, market_id: str, mid: float, timestamp: float) -> None:
        """Update market state with a new mid price observation."""
        if market_id not in self._states:
            # Cold start seeding
            initial_variance = (self.min_spread / 2.0) ** 2
            self._states[market_id] = EWMAState(
                variance=initial_variance,
                last_mid=mid,
                last_timestamp=timestamp
            )
            self._warm_up_trackers[market_id] = {
                "start_time": timestamp,
                "observation_count": 1.0
            }
            logger.info("signal_engine_cold_start", market_id=market_id, initial_var=initial_variance)
        else:
            state = self._states[market_id]
            
            # Avoid updates for duplicate timestamps
            if timestamp <= state.last_timestamp:
                return

            # Update EWMA
            new_state = update_ewma(state, mid, timestamp, self.tau)
            self._states[market_id] = new_state
            
            self._warm_up_trackers[market_id]["observation_count"] += 1.0

    def is_market_ready(self, market_id: str, current_time: float) -> bool:
        """
        Check if market has passed the warm-up criteria.
        """
        if market_id not in self._states:
            return False
            
        tracker = self._warm_up_trackers[market_id]
        state = self._states[market_id]
        
        elapsed_seconds = current_time - tracker["start_time"]
        observation_count = tracker["observation_count"]
        
        # Primary: enough time elapsed
        # Secondary: enough observations
        # Tertiary: variance > 0
        is_ready = (
            elapsed_seconds >= self.warm_up_seconds
            and observation_count >= self.warm_up_min_obs
            and state.variance > 0
        )
        return is_ready

    def get_market_volatility(self, market_id: str, current_time: float) -> Optional[float]:
        """
        Get the current volatility for a market, or None if it's not ready.
        """
        if not self.is_market_ready(market_id, current_time):
            return None
            
        return get_volatility(self._states[market_id])
