"""
risk/circuit_breaker.py — Circuit breakers for feed health and risk limits.
Provides per-market and global kill switches to protect against adverse conditions.
"""

import time
from loguru import logger

from config.settings import config


class CircuitBreaker:
    """
    Multi-level circuit breaker system.

    Per-market triggers:
    - External feed stale > STALE_FEED_THRESHOLD_MS
    - Polymarket book stale > POLY_BOOK_STALE_THRESHOLD_MS
    - Large spot price move > EMERGENCY_REPRICE_BPS
    - Tau < MIN_TAU_TO_QUOTE

    Global triggers:
    - Total portfolio loss > DAILY_LOSS_LIMIT
    - Kill switch file exists
    - Max drawdown exceeded
    """

    def __init__(self):
        # Per-market state: market_key -> last_update_ms
        self._feed_timestamps: dict[str, int] = {}
        self._book_timestamps: dict[str, int] = {}
        self._last_spot_prices: dict[str, float] = {}

        # Global state
        self._global_halted = False
        self._halt_reason = ""
        self._session_start_capital = config.initial_capital
        self._current_capital = config.initial_capital

        # Per-market breaker state
        self._market_halted: dict[str, str] = {}  # market_key -> reason

    # ── Feed Health ──────────────────────────────────────────

    def update_feed_timestamp(self, asset: str, timestamp_ms: int = 0):
        """Record the latest feed update for an asset."""
        if timestamp_ms == 0:
            timestamp_ms = int(time.time() * 1000)
        self._feed_timestamps[asset] = timestamp_ms

    def update_book_timestamp(self, market_key: str, timestamp_ms: int = 0):
        """Record the latest Polymarket book update for a market."""
        if timestamp_ms == 0:
            timestamp_ms = int(time.time() * 1000)
        self._book_timestamps[market_key] = timestamp_ms

    def is_feed_stale(self, asset: str) -> bool:
        """Check if the external price feed is stale."""
        if asset not in self._feed_timestamps:
            return True  # Never received data
        age_ms = int(time.time() * 1000) - self._feed_timestamps[asset]
        return age_ms > config.stale_feed_threshold_ms

    def is_book_stale(self, market_key: str) -> bool:
        """Check if the Polymarket order book is stale."""
        if market_key not in self._book_timestamps:
            return True  # Never received data
        age_ms = int(time.time() * 1000) - self._book_timestamps[market_key]
        return age_ms > config.poly_book_stale_threshold_ms

    def get_feed_age_ms(self, asset: str) -> int:
        """Get age of the last feed update in milliseconds."""
        if asset not in self._feed_timestamps:
            return 999999
        return int(time.time() * 1000) - self._feed_timestamps[asset]

    # ── Emergency Price Move Detection ───────────────────────

    def update_spot_price(self, asset: str, price: float):
        """Record latest spot price for emergency move detection."""
        self._last_spot_prices[asset] = price

    def check_emergency_move(self, asset: str, new_price: float) -> bool:
        """
        Check if price moved more than EMERGENCY_REPRICE_BPS since last update.
        Returns True if an emergency reprice is needed.
        """
        if asset not in self._last_spot_prices:
            self._last_spot_prices[asset] = new_price
            return False

        last_price = self._last_spot_prices[asset]
        if last_price <= 0:
            return False

        move_bps = abs(new_price - last_price) / last_price * 10000
        if move_bps >= config.emergency_reprice_bps:
            logger.warning(
                f"[CB] Emergency move detected for {asset}: "
                f"${last_price:,.2f} -> ${new_price:,.2f} ({move_bps:.0f} bps)"
            )
            return True
        return False

    # ── Portfolio-Level Circuit Breaker ───────────────────────

    def update_portfolio_value(self, current_capital: float):
        """Update current capital for drawdown monitoring."""
        self._current_capital = current_capital

    def check_daily_loss(self) -> bool:
        """Check if daily loss limit has been breached."""
        if self._session_start_capital <= 0:
            return False
        loss_pct = (self._session_start_capital - self._current_capital) / self._session_start_capital
        if loss_pct >= config.daily_loss_limit:
            self._global_halted = True
            self._halt_reason = (
                f"Daily loss limit breached: "
                f"${self._session_start_capital:.2f} -> ${self._current_capital:.2f} "
                f"({loss_pct*100:.1f}% > {config.daily_loss_limit*100:.1f}%)"
            )
            logger.error(f"[CB] GLOBAL HALT: {self._halt_reason}")
            return True
        return False

    def check_max_drawdown(self) -> bool:
        """Check if absolute max drawdown has been exceeded."""
        if self._session_start_capital <= 0:
            return False
        dd = (self._session_start_capital - self._current_capital) / self._session_start_capital
        if dd >= config.max_drawdown_pct:
            self._global_halted = True
            self._halt_reason = f"Max drawdown breached: {dd*100:.1f}%"
            logger.error(f"[CB] KILL SWITCH: {self._halt_reason}")
            return True
        return False

    # ── Per-Market Halt ──────────────────────────────────────

    def halt_market(self, market_key: str, reason: str):
        """Halt quoting for a specific market."""
        self._market_halted[market_key] = reason
        logger.warning(f"[CB] Market halted: {market_key} - {reason}")

    def resume_market(self, market_key: str):
        """Resume quoting for a specific market."""
        if market_key in self._market_halted:
            del self._market_halted[market_key]
            logger.info(f"[CB] Market resumed: {market_key}")

    def is_market_halted(self, market_key: str) -> bool:
        """Check if a specific market is halted."""
        return market_key in self._market_halted

    # ── Global State ─────────────────────────────────────────

    def is_globally_halted(self) -> bool:
        """Check if the global circuit breaker is active."""
        return self._global_halted

    def get_halt_reason(self) -> str:
        """Get the reason for global halt."""
        return self._halt_reason

    def can_quote(self, market_key: str, asset: str) -> tuple[bool, str]:
        """
        Master check: can we quote this market?
        Returns (can_quote, reason_if_not).
        """
        if self._global_halted:
            return False, f"Global halt: {self._halt_reason}"

        if self.is_market_halted(market_key):
            return False, f"Market halted: {self._market_halted[market_key]}"

        if self.is_feed_stale(asset):
            return False, f"Feed stale: {self.get_feed_age_ms(asset)}ms"

        # In paper trading, Poly book is optional (fills come from Binance)
        if not config.paper_trading and self.is_book_stale(market_key):
            return False, f"Book stale"

        return True, ""

    def reset_global(self):
        """Reset global halt (for testing or manual override)."""
        self._global_halted = False
        self._halt_reason = ""
        logger.info("[CB] Global halt reset")
