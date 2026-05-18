"""Quoting and Skewing Engine."""

import math
from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class QuoteResult:
    bid: float | None   # None = do not quote this side
    ask: float | None
    half_spread: float  # useful for logging/metrics
    skew: float         # useful for debugging inventory pressure


def calculate_quotes(
    mid: float,
    vol: float,
    inventory: float,
    min_spread: float,
    vol_mult: float,
    max_inventory: float,
    skew_factor: float,
    tick_size: float = 0.001
) -> QuoteResult:
    """
    Pure function to calculate bid/ask quotes based on mid-price, volatility, and inventory.
    """
    # 1. Half-spread
    half_spread = max(min_spread / 2.0, vol * vol_mult)
    
    # 2 & 3. Inventory Ratio and Skew
    # Clamping ratio to [-1.0, 1.0] is right, without the clamp, inventory beyond 
    # max_inventory causes skew to exceed half_spread, which can push a quote past mid 
    # or even negative. The clamp is a safety net for the case where the emergency stop 
    # failed to fire or was bypassed.
    ratio = max(-1.0, min(1.0, inventory / max_inventory))
    skew = ratio * skew_factor * half_spread
    
    # 4. Raw quotes
    bid_raw = mid - half_spread - skew
    ask_raw = mid + half_spread - skew
    
    # 5. Tick rounding (conservative)
    # Adding a tiny epsilon to prevent pure float precision errors (like 0.948 / 0.001 = 947.9999...)
    # from causing an extra unintended tick of widening.
    bid = math.floor((bid_raw / tick_size) + 1e-9) * tick_size
    ask = math.ceil((ask_raw / tick_size) - 1e-9) * tick_size
    
    # 6. Bound checks
    # Deterministic correction to ensure bid < mid and ask > mid.
    # No loops, no float drift.
    if bid >= mid:
        bid = math.floor(((mid - tick_size) / tick_size) + 1e-9) * tick_size
    if ask <= mid:
        ask = math.ceil(((mid + tick_size) / tick_size) - 1e-9) * tick_size
        
    # 7. Bound check constraints
    # Final sanity guard for Polymarket valid prices
    bid = max(0.001, min(0.999, bid))
    ask = max(0.001, min(0.999, ask))
    
    return QuoteResult(
        bid=bid,
        ask=ask,
        half_spread=half_spread,
        skew=skew
    )


class QuotingEngine:
    """
    Orchestration layer for quoting. Handles state-dependent logic like emergency stops.
    """
    def __init__(
        self,
        min_spread: float,
        vol_mult: float,
        max_inventory: float,
        skew_factor: float,
        emergency_factor: float,
        tick_size: float = 0.001
    ):
        self.min_spread = min_spread
        self.vol_mult = vol_mult
        self.max_inventory = max_inventory
        self.skew_factor = skew_factor
        self.emergency_factor = emergency_factor
        self.tick_size = tick_size

    def get_quotes(self, mid: float, vol: float, inventory: float) -> QuoteResult:
        """
        Generate active quotes for the current market state.
        """
        # Clamp mid at ingestion to prevent garbage input propagation
        clamped_mid = max(0.001, min(0.999, mid))
        
        quotes = calculate_quotes(
            mid=clamped_mid,
            vol=vol,
            inventory=inventory,
            min_spread=self.min_spread,
            vol_mult=self.vol_mult,
            max_inventory=self.max_inventory,
            skew_factor=self.skew_factor,
            tick_size=self.tick_size
        )
        
        # 8. Emergency stop
        # Lives in the caller/orchestration layer to keep calculate_quotes pure.
        if abs(inventory) >= self.max_inventory * self.emergency_factor:
            if inventory > 0:
                logger.warning("quoting_engine_emergency_long", inventory=inventory)
                quotes.bid = None
            elif inventory < 0:
                logger.warning("quoting_engine_emergency_short", inventory=inventory)
                quotes.ask = None
                
        return quotes
