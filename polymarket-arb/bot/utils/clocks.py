"""
Time and clock utilities with pluggable clock support for deterministic testing.
"""
import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Protocol for pluggable clock implementations."""
    def now_ms(self) -> int: ...


class WallClock:
    """Production clock using real wall time."""
    def now_ms(self) -> int:
        return int(time.time() * 1000)


class SimulatedClock:
    """Test clock with manual time control for deterministic testing."""
    def __init__(self, start_ms: int = 0):
        self._now = start_ms

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> None:
        self._now += ms

    def set(self, ms: int) -> None:
        self._now = ms


# Module-level default clock — replaced in tests via set_clock()
_clock: Clock = WallClock()


def set_clock(clock: Clock) -> None:
    """Replace the global clock (used for testing)."""
    global _clock
    _clock = clock


def reset_clock() -> None:
    """Reset the global clock to the default WallClock."""
    global _clock
    _clock = WallClock()


def current_timestamp_ms() -> int:
    """Return the current UTC timestamp in milliseconds."""
    return _clock.now_ms()


def utc_now() -> datetime:
    """Return the current UTC datetime, timezone aware."""
    return datetime.now(timezone.utc)
