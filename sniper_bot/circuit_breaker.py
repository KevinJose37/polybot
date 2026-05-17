"""
sniper_bot/circuit_breaker.py — Safety halts for anomalous conditions.

Prevents runaway losses from bugs or extreme market conditions.
"""
import time
import logging
from dataclasses import dataclass

logger = logging.getLogger("sniper_bot.circuit_breaker")


@dataclass
class TradingState:
    """Snapshot of current trading state for circuit breaker evaluation."""
    consecutive_losses: int = 0
    total_pnl: float = 0.0
    starting_capital: float = 500.0
    open_positions: int = 0
    last_signal_time: float = 0.0


class CircuitBreaker:
    """
    Safety system. Checks multiple halt conditions before allowing trades.
    """

    def __init__(
        self,
        max_consecutive_losses: int = 5,
        max_drawdown_pct: float = 0.10,
        max_drawdown_usd: float = 50.0,
        min_signal_interval_s: float = 2.0,
        max_open_positions: int = 4,
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.max_drawdown_pct = max_drawdown_pct
        self.max_drawdown_usd = max_drawdown_usd
        self.min_signal_interval_s = min_signal_interval_s
        self.max_open_positions = max_open_positions
        self._halted = False
        self._halt_reason = ""

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def should_halt(self, state: TradingState) -> tuple[bool, str]:
        """Check if trading should be halted. Returns (should_halt, reason)."""
        if state.consecutive_losses >= self.max_consecutive_losses:
            reason = f"HALT: {state.consecutive_losses} consecutive losses"
            self._halted = True
            self._halt_reason = reason
            logger.warning(reason)
            return True, reason

        drawdown_pct = abs(state.total_pnl) / state.starting_capital if state.total_pnl < 0 else 0.0
        if drawdown_pct >= self.max_drawdown_pct:
            reason = f"HALT: drawdown {drawdown_pct:.1%} >= {self.max_drawdown_pct:.1%}"
            self._halted = True
            self._halt_reason = reason
            logger.warning(reason)
            return True, reason

        if state.total_pnl < 0 and abs(state.total_pnl) >= self.max_drawdown_usd:
            reason = f"HALT: loss ${abs(state.total_pnl):.2f} >= max ${self.max_drawdown_usd:.2f}"
            self._halted = True
            self._halt_reason = reason
            logger.warning(reason)
            return True, reason

        return False, ""

    def can_signal(self, last_signal_time: float) -> bool:
        """Anti-spam gate: enough time since last signal?"""
        return time.time() - last_signal_time >= self.min_signal_interval_s

    def can_open(self, open_positions: int) -> bool:
        """Check if we can open another position."""
        return open_positions < self.max_open_positions and not self._halted

    def reset(self) -> None:
        """Manual reset after halt."""
        self._halted = False
        self._halt_reason = ""
        logger.info("Circuit breaker RESET")

    def status_summary(self) -> dict:
        """Status for dashboard."""
        return {
            "halted": self._halted,
            "reason": self._halt_reason,
            "max_losses": self.max_consecutive_losses,
            "max_dd_pct": self.max_drawdown_pct,
            "max_dd_usd": self.max_drawdown_usd,
        }
