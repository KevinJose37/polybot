"""
core/tau.py — Time-to-expiry calculation and spread adaptation.
Handles the tau-based spread multiplier table from strategy doc Section B.3.
"""

import time
from loguru import logger


class TauCalculator:
    """
    Computes time-to-expiry (tau) and the corresponding spread multiplier.

    Spread multipliers widen as expiry approaches, reflecting
    increasing gamma risk in binary options near resolution.
    """

    # Tau brackets and their spread multipliers (from strategy doc B.3)
    TAU_BRACKETS = [
        # (min_tau_seconds, max_tau_seconds, multiplier)
        (3600, float("inf"), 1.0),    # > 1 hour: normal spread
        (1800, 3600, 1.2),            # 30min - 1hr: slightly wider
        (900, 1800, 1.5),             # 15min - 30min: wider
        (300, 900, 2.5),              # 5min - 15min: much wider
        (120, 300, 5.0),              # 2min - 5min: very wide
        (0, 120, float("inf")),       # < 2min: STOP QUOTING
    ]

    @staticmethod
    def compute_tau(end_date_ts: float) -> float:
        """
        Compute time-to-expiry in seconds.
        Returns 0 if the market has expired.
        """
        if end_date_ts <= 0:
            return 0.0
        tau = end_date_ts - time.time()
        return max(0.0, tau)

    @classmethod
    def get_spread_multiplier(cls, tau_seconds: float) -> float:
        """
        Get the spread multiplier for a given tau.
        Returns float('inf') if quoting should be suspended.
        """
        for min_tau, max_tau, mult in cls.TAU_BRACKETS:
            if min_tau <= tau_seconds < max_tau:
                return mult

        # Fallback: if tau is negative or zero, don't quote
        return float("inf")

    @classmethod
    def should_quote(cls, tau_seconds: float, min_tau_to_quote: int = 120) -> bool:
        """
        Whether the market is eligible for quoting based on time-to-expiry.
        """
        return tau_seconds >= min_tau_to_quote

    @classmethod
    def tau_to_years(cls, tau_seconds: float) -> float:
        """Convert tau in seconds to years (for Black-Scholes formula)."""
        return tau_seconds / (365.25 * 24 * 3600)

    @classmethod
    def describe_tau(cls, tau_seconds: float) -> str:
        """Human-readable description of tau."""
        if tau_seconds < 60:
            return f"{tau_seconds:.0f}s"
        elif tau_seconds < 3600:
            return f"{tau_seconds / 60:.1f}m"
        else:
            return f"{tau_seconds / 3600:.1f}h"
