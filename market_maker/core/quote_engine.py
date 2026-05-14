"""
core/quote_engine.py — Central bid/ask quote generator.
Implements spread calculation with inventory skew, tau adaptation,
toxicity adjustment, and boundary enforcement.
"""

import math
import time
from typing import Optional

from loguru import logger

from config.settings import config
from core.tau import TauCalculator
from utils.schemas import (
    QuotePair, SpreadParams, InventoryState,
    ToxicityMetrics, ToxicityLevel, MarketOdds,
)


class SuspendQuoting(Exception):
    """Raised when quoting should be suspended for a market."""
    pass


class QuoteEngine:
    """
    Generates bid/ask quotes around a fair value estimate.

    Quote = fair_value +/- half_spread (adjusted for inventory skew)

    Spread is determined by:
    1. Minimum half-spread floor
    2. Volatility-based spread
    3. Competition floor (from current book)
    4. Tau-based multiplier (wider near expiry)
    5. Toxicity-based multiplier (wider during informed flow)
    6. Inventory skew (shift quotes to reduce inventory)
    """

    def __init__(self):
        self.min_half_spread = config.min_half_spread_bps / 10000  # Convert bps to probability units
        self.vol_multiplier = config.spread_volatility_multiplier
        self.skew_sensitivity = config.skew_sensitivity
        self.min_spread_abs = config.min_spread_abs
        self.min_quote_price = config.min_quote_price
        self.max_quote_price = config.max_quote_price
        self.competition_floor_pct = config.spread_competition_floor_pct

    def compute_quotes(
        self,
        fair_value: float,
        inventory: InventoryState,
        tau_seconds: float,
        volatility: float,
        toxicity: Optional[ToxicityMetrics] = None,
        book_spread: Optional[float] = None,
    ) -> QuotePair:
        """
        Compute bid/ask quotes given current market conditions.

        Args:
            fair_value: Binary option probability [0, 1]
            inventory: Current position state
            tau_seconds: Time to expiry in seconds
            volatility: Annualized volatility
            toxicity: Current order flow toxicity (optional)
            book_spread: Current best spread on the Polymarket book (optional)

        Returns:
            QuotePair with bid and ask prices

        Raises:
            SuspendQuoting: If quoting should be suspended (tau too low, etc.)
        """
        # ── Check if quoting should be suspended ──
        if not TauCalculator.should_quote(tau_seconds, config.min_tau_to_quote):
            raise SuspendQuoting(
                f"Tau too low: {TauCalculator.describe_tau(tau_seconds)} < "
                f"{config.min_tau_to_quote}s"
            )

        # Check extreme toxicity
        if toxicity and toxicity.level == ToxicityLevel.EXTREME:
            raise SuspendQuoting(
                f"Extreme toxicity: imbalance={toxicity.order_imbalance:.2f}"
            )

        # ── 1. Base half-spread ──
        half_spread_base = self._compute_base_half_spread(
            volatility, tau_seconds, book_spread
        )

        # ── 2. Tau-based spread multiplier ──
        tau_mult = TauCalculator.get_spread_multiplier(tau_seconds)
        if tau_mult == float("inf"):
            raise SuspendQuoting(
                f"Tau bracket requires suspension: {TauCalculator.describe_tau(tau_seconds)}"
            )

        # ── 3. Toxicity-based spread multiplier ──
        tox_mult = 1.0
        size_mult = 1.0
        if toxicity:
            tox_mult = toxicity.spread_multiplier
            size_mult = toxicity.size_multiplier

        # ── 4. Build spread params ──
        spread_params = SpreadParams(
            half_spread_base=half_spread_base,
            tau_multiplier=tau_mult,
            toxicity_multiplier=tox_mult,
        )

        effective_half_spread = spread_params.effective_half_spread

        # ── 5. Inventory skew ──
        skew = self._compute_inventory_skew(inventory, effective_half_spread)
        spread_params.inventory_skew = skew

        # ── 6. Compute raw bid/ask ──
        bid_price = fair_value - effective_half_spread - skew
        ask_price = fair_value + effective_half_spread - skew

        # ── 7. Boundary enforcement ──
        bid_price = max(bid_price, self.min_quote_price)
        ask_price = min(ask_price, self.max_quote_price)
        # Enforce minimum spread
        ask_price = max(ask_price, bid_price + self.min_spread_abs)
        # Re-clamp after spread enforcement
        ask_price = min(ask_price, self.max_quote_price)

        # If boundary enforcement makes quoting impossible
        if bid_price >= ask_price or bid_price < self.min_quote_price:
            raise SuspendQuoting(
                f"Boundary enforcement: bid={bid_price:.4f}, ask={ask_price:.4f}"
            )

        # ── 8. Compute sizes ──
        base_size = 1  # Minimum: 1 contract
        bid_size = max(1, int(base_size * size_mult))
        ask_size = max(1, int(base_size * size_mult))

        # One-sided quoting at hard inventory limit
        if inventory.utilization >= config.hard_inventory_pct:
            if inventory.is_long:
                bid_size = 0  # Stop buying
            elif inventory.is_short:
                ask_size = 0  # Stop selling

        now_ms = int(time.time() * 1000)

        return QuotePair(
            bid_price=round(bid_price, 4),
            ask_price=round(ask_price, 4),
            bid_size=bid_size,
            ask_size=ask_size,
            fair_value=fair_value,
            spread_params=spread_params,
            timestamp_ms=now_ms,
        )

    def _compute_base_half_spread(
        self,
        volatility: float,
        tau_seconds: float,
        book_spread: Optional[float] = None,
    ) -> float:
        """
        Base half-spread from strategy doc Section B.2:
        half_spread = max(
            MIN_HALF_SPREAD,
            VOL_MULTIPLIER * sigma * sqrt(tau_hours / HOURS_PER_YEAR),
            COMPETITION_FLOOR
        )
        """
        # Volatility-based spread
        tau_hours = tau_seconds / 3600
        hours_per_year = 365.25 * 24
        vol_spread = self.vol_multiplier * volatility * math.sqrt(
            max(tau_hours, 0.001) / hours_per_year
        )

        # Competition floor (if book data available)
        competition_floor = 0.0
        if book_spread is not None and book_spread > 0:
            # Cap the competition floor at 0.05 (10% spread) so we don't follow empty books
            competition_floor = min((book_spread / 2) * self.competition_floor_pct, 0.05)

        half_spread = max(
            self.min_half_spread,
            vol_spread,
            competition_floor,
        )

        return half_spread

    def _compute_inventory_skew(
        self, inventory: InventoryState, half_spread: float
    ) -> float:
        """
        Inventory skew from strategy doc Section B.2:
        skew = (position / max_position) * SKEW_SENSITIVITY * half_spread

        Positive skew (long inventory): lower bid, raise ask → discourage buys
        Negative skew (short inventory): raise bid, lower ask → encourage buys
        """
        if inventory.max_position == 0:
            return 0.0

        # Imbalance: [-1.0, +1.0]
        imbalance = inventory.net_position / inventory.max_position

        # Progressive skew: increase faster near limits
        utilization = inventory.utilization
        if utilization >= config.soft_inventory_pct:
            # Aggressive skew above soft limit
            skew_boost = 1.0 + (utilization - config.soft_inventory_pct) * 3.0
        else:
            skew_boost = 1.0

        skew = imbalance * self.skew_sensitivity * half_spread * skew_boost

        # Cap skew to never cross fair value (leave at least 10% of spread as theoretical edge)
        # This prevents the bot from willingly paying worse than FV to flatten inventory
        max_skew_abs = 0.9 * half_spread
        if skew > max_skew_abs:
            skew = max_skew_abs
        elif skew < -max_skew_abs:
            skew = -max_skew_abs

        return skew
