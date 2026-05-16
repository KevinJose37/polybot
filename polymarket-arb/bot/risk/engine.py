"""
Risk Engine for enforcing global and per-asset exposure limits.
Implements: kill switch (file-persisted), daily drawdown, per-asset exposure,
portfolio exposure cap, and stale-feed circuit breaker.
"""
import json
import structlog
from pathlib import Path
from typing import Optional

from bot.settings import Settings
from bot.execution.position_manager import PositionManager
from bot.utils.clocks import current_timestamp_ms

logger = structlog.get_logger(__name__)

# Rate-limit window for exposure-breach warnings (milliseconds).
# Within this window, only the first warning per token is logged.
_RATE_LIMIT_WINDOW_MS = 5_000


class RiskKillSwitchTriggered(Exception):
    """Raised when hard limits are breached."""
    pass


class RiskEngine:
    def __init__(self, settings: Settings, position_manager: PositionManager):
        self.settings = settings
        self.position_manager = position_manager
        self.kill_switch_active = self._load_kill_switch()
        # Rate-limiter state: token_id -> last_warning_timestamp_ms
        self._last_warn_ts: dict[str, int] = {}
        self.inflight_exposure = 0.0

    def get_total_exposure(self) -> float:
        return sum(
            abs(p.size) * (p.avg_price if p.avg_price > 0 else 0.5)
            for p in self.position_manager.positions.values()
        )

    def reserve_exposure(self, amount: float) -> bool:
        """Atomically check and reserve portfolio exposure for inflight trades."""
        if self.get_total_exposure() + self.inflight_exposure + amount > self.settings.risk.max_portfolio_exposure:
            if self._should_warn("portfolio"):
                logger.warning(
                    "portfolio_exposure_breached",
                    total_exposure=self.get_total_exposure(),
                    inflight=self.inflight_exposure,
                    new_order_notional=amount,
                    limit=self.settings.risk.max_portfolio_exposure
                )
            return False
        self.inflight_exposure += amount
        return True

    def release_exposure(self, amount: float) -> None:
        """Release previously reserved exposure."""
        self.inflight_exposure = max(0.0, self.inflight_exposure - amount)

    def _kill_switch_path(self) -> Path:
        """Return the path to the kill switch persistence file."""
        return Path(self.settings.risk.kill_switch_file)

    def _load_kill_switch(self) -> bool:
        """Load kill switch state from disk on startup."""
        path = self._kill_switch_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if data.get("active", False):
                    logger.critical("kill_switch_restored_from_disk", reason=data.get("reason", "unknown"))
                    return True
            except (json.JSONDecodeError, OSError):
                pass
        return False

    def _persist_kill_switch(self, reason: str) -> None:
        """Persist kill switch activation to disk."""
        path = self._kill_switch_path()
        try:
            path.write_text(json.dumps({
                "active": True,
                "reason": reason,
                "timestamp": current_timestamp_ms()
            }))
        except OSError as e:
            logger.error("kill_switch_persist_failed", error=str(e))

    def activate_kill_switch(self, reason: str) -> None:
        """Activate the kill switch and persist to disk."""
        self.kill_switch_active = True
        self._persist_kill_switch(reason)
        logger.critical("kill_switch_activated", reason=reason)

    def clear_kill_switch(self) -> None:
        """Clear the kill switch (operator action)."""
        self.kill_switch_active = False
        path = self._kill_switch_path()
        if path.exists():
            path.unlink()
        logger.warning("kill_switch_cleared")

    def _should_warn(self, key: str) -> bool:
        """Rate-limit warnings to at most once per _RATE_LIMIT_WINDOW_MS per key."""
        now = current_timestamp_ms()
        last = self._last_warn_ts.get(key, 0)
        if now - last >= _RATE_LIMIT_WINDOW_MS:
            self._last_warn_ts[key] = now
            return True
        return False

    def validate_order(
        self,
        token_id: str,
        size: float,
        price: float = 0.5,
        orderbooks: Optional[dict] = None,
        check_portfolio: bool = True
    ) -> bool:
        """
        Validates if an order is safe to place.
        Returns False if rejected, raises RiskKillSwitchTriggered if kill switch triggered.

        Args:
            token_id: The token to trade.
            size: The order size (number of shares).
            price: The order price (used for notional = size × price).
            orderbooks: Optional dict of token_id -> LocalOrderBook for stale-feed checks.
        """
        # 1. Kill switch check
        if self.kill_switch_active:
            raise RiskKillSwitchTriggered("Kill switch is active. Halting execution.")

        # 2. Check total daily drawdown
        total_pnl = self.position_manager.total_realized_pnl + self.position_manager.total_unrealized_pnl
        if total_pnl < -self.settings.risk.max_daily_drawdown:
            self.activate_kill_switch(f"Max daily drawdown breached: PnL={total_pnl:.2f}")
            raise RiskKillSwitchTriggered("Max daily drawdown breached. Kill switch activated.")

        # 3. Stale feed circuit breaker
        if orderbooks is not None:
            book = orderbooks.get(token_id)
            if book is not None and book.is_stale():
                logger.warning("stale_feed_rejected", token_id=token_id)
                return False

        # 4. Per-asset exposure check
        #    Use actual price for accurate notional estimation
        order_notional = size * price
        pos = self.position_manager.get_position(token_id)
        current_exposure = abs(pos.size) * (pos.avg_price if pos.avg_price > 0 else 0.5)
        new_exposure = current_exposure + order_notional

        if new_exposure > self.settings.risk.max_exposure_per_asset:
            if self._should_warn(f"asset_{token_id}"):
                logger.warning(
                    "max_exposure_breached",
                    token_id=token_id,
                    new_exposure=new_exposure,
                    limit=self.settings.risk.max_exposure_per_asset
                )
            return False

        # 5. Portfolio exposure cap
        if check_portfolio:
            total_exposure = self.get_total_exposure() + self.inflight_exposure
            if total_exposure + order_notional > self.settings.risk.max_portfolio_exposure:
                if self._should_warn("portfolio"):
                    logger.warning(
                        "portfolio_exposure_breached",
                        total_exposure=total_exposure,
                        new_order_notional=order_notional,
                        limit=self.settings.risk.max_portfolio_exposure
                    )
                return False

        return True
