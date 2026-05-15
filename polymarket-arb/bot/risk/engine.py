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


class RiskKillSwitchTriggered(Exception):
    """Raised when hard limits are breached."""
    pass


class RiskEngine:
    def __init__(self, settings: Settings, position_manager: PositionManager):
        self.settings = settings
        self.position_manager = position_manager
        self.kill_switch_active = self._load_kill_switch()

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

    def validate_order(
        self,
        token_id: str,
        size: float,
        orderbooks: Optional[dict] = None
    ) -> bool:
        """
        Validates if an order is safe to place.
        Returns False if rejected, raises RiskKillSwitchTriggered if kill switch triggered.

        Args:
            token_id: The token to trade.
            size: The order size.
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
        pos = self.position_manager.get_position(token_id)
        current_exposure = abs(pos.size) * (pos.avg_price if pos.avg_price > 0 else 0.5)
        new_exposure = current_exposure + (size * 0.5)  # estimate added notional

        if new_exposure > self.settings.risk.max_exposure_per_asset:
            logger.warning(
                "max_exposure_breached",
                token_id=token_id,
                new_exposure=new_exposure,
                limit=self.settings.risk.max_exposure_per_asset
            )
            return False

        # 5. Portfolio exposure cap
        total_exposure = sum(
            abs(p.size) * (p.avg_price if p.avg_price > 0 else 0.5)
            for p in self.position_manager.positions.values()
        )
        if total_exposure + (size * 0.5) > self.settings.risk.max_portfolio_exposure:
            logger.warning(
                "portfolio_exposure_breached",
                total_exposure=total_exposure,
                new_order_size=size,
                limit=self.settings.risk.max_portfolio_exposure
            )
            return False

        return True
