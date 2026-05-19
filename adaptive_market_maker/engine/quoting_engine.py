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
    bid = max(tick_size, min(1.0 - tick_size, bid))
    ask = max(tick_size, min(1.0 - tick_size, ask))
    
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
        max_position_usdc: float,
        skew_factor: float,
        emergency_factor: float
    ):
        self.min_spread = min_spread
        self.vol_mult = vol_mult
        self.max_position_usdc = max_position_usdc
        self.skew_factor = skew_factor
        self.emergency_factor = emergency_factor

    def get_quotes(self, mid: float, vol: float, inventory: float, tick_size: float) -> QuoteResult:
        """
        Generate active quotes for the current market state.
        
        INVARIANT: `mid` is expected to be pre-clamped to [tick_size, 1.0 - tick_size]
        by the caller (bot.on_pm_book). No redundant clamping is performed here.
        """
        # [M-5] Dynamically calculate max_inventory (shares) from USDC limit
        # This ensures our position limits scale correctly with market price.
        dynamic_max_inv = self.max_position_usdc / mid

        quotes = calculate_quotes(
            mid=mid,
            vol=vol,
            inventory=inventory,
            min_spread=self.min_spread,
            vol_mult=self.vol_mult,
            max_inventory=dynamic_max_inv,
            skew_factor=self.skew_factor,
            tick_size=tick_size
        )
        
        # 8. Emergency stop
        # Lives in the caller/orchestration layer to keep calculate_quotes pure.
        abs_inv = abs(inventory)
        
        # F-13: Two-tier inventory protection:
        # - Hard cut at emergency_factor × dynamic_max_inv: null the accumulating side.
        # - Soft-disable at 1.0× dynamic_max_inv: widen the accumulating side by 2×
        #   half_spread, making it much harder (but not impossible) to accumulate.
        if abs_inv >= dynamic_max_inv * self.emergency_factor:
            if inventory > 0:
                logger.warning("quoting_engine_emergency_long", inventory=inventory, max_inv=dynamic_max_inv)
                quotes.bid = None
            elif inventory < 0:
                logger.warning("quoting_engine_emergency_short", inventory=inventory, max_inv=dynamic_max_inv)
                quotes.ask = None
        elif abs_inv >= dynamic_max_inv:
            # Soft-disable: widen the offending side by doubling half_spread
            if inventory > 0 and quotes.bid is not None:
                widened_bid = mid - 2.0 * quotes.half_spread - quotes.skew
                widened_bid = math.floor((widened_bid / tick_size) + 1e-9) * tick_size
                widened_bid = max(tick_size, min(1.0 - tick_size, widened_bid))
                if widened_bid >= mid:
                    widened_bid = math.floor(((mid - tick_size) / tick_size) + 1e-9) * tick_size
                quotes.bid = widened_bid
                logger.info("quoting_engine_soft_disable_long", inventory=inventory, widened_bid=widened_bid)
            elif inventory < 0 and quotes.ask is not None:
                widened_ask = mid + 2.0 * quotes.half_spread - quotes.skew
                widened_ask = math.ceil((widened_ask / tick_size) - 1e-9) * tick_size
                widened_ask = max(tick_size, min(1.0 - tick_size, widened_ask))
                if widened_ask <= mid:
                    widened_ask = math.ceil(((mid + tick_size) / tick_size) - 1e-9) * tick_size
                quotes.ask = widened_ask
                logger.info("quoting_engine_soft_disable_short", inventory=inventory, widened_ask=widened_ask)
                
        return quotes
