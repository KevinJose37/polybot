"""
sniper_bot/lifecycle.py — Market temporal state machine.

Tracks where each 5-minute market is in its lifecycle:
  PENDING → ENTRY → HOLD → EXIT → RESOLVED

Answers critical questions:
  - Did we arrive late?
  - Are we in the entry window?
  - How long until resolution?
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger("sniper_bot.lifecycle")


class Phase(Enum):
    PENDING = "PENDING"       # Market not started yet
    ENTRY = "ENTRY"           # First N seconds — signals allowed
    HOLD = "HOLD"             # Between entry close and exit warning
    EXIT = "EXIT"             # Last 30 seconds — prepare for resolution
    RESOLVED = "RESOLVED"     # Market ended


@dataclass
class MarketState:
    """Temporal state of a single market."""
    asset: str
    market_id: str             # slug or condition_id
    start_time: float          # Unix timestamp
    end_time: float            # Unix timestamp
    duration_s: float          # Total duration in seconds
    first_seen: float = 0.0    # When WS first saw data for this market
    entry_made: bool = False   # Whether we entered a position

    entry_window_s: int = 60   # Config: first N seconds allow signals
    exit_window_s: int = 30    # Config: last N seconds = exit zone

    def seconds_elapsed(self) -> float:
        """Seconds since market opened."""
        return max(0.0, time.time() - self.start_time)

    def seconds_remaining(self) -> float:
        """Seconds until resolution."""
        return max(0.0, self.end_time - time.time())

    def phase(self) -> Phase:
        """Current phase of the market lifecycle."""
        now = time.time()
        if now < self.start_time:
            return Phase.PENDING
        if now > self.end_time:
            return Phase.RESOLVED
        elapsed = now - self.start_time
        remaining = self.end_time - now
        if elapsed <= self.entry_window_s:
            return Phase.ENTRY
        if remaining <= self.exit_window_s:
            return Phase.EXIT
        return Phase.HOLD

    def is_in_entry_window(self) -> bool:
        return self.phase() == Phase.ENTRY

    def is_in_exit_window(self) -> bool:
        return self.phase() == Phase.EXIT

    def is_resolved(self) -> bool:
        return self.phase() == Phase.RESOLVED

    def arrived_late(self) -> bool:
        """True if we first saw this market after 45s and haven't entered."""
        if self.entry_made:
            return False
        if self.first_seen <= 0:
            return False
        late_threshold = self.start_time + 45
        return self.first_seen > late_threshold

    def progress_pct(self) -> float:
        """0.0 to 1.0 — how far through the market we are."""
        if self.duration_s <= 0:
            return 0.0
        elapsed = self.seconds_elapsed()
        return min(1.0, max(0.0, elapsed / self.duration_s))

    def entry_window_remaining(self) -> float:
        """Seconds left in the entry window. 0 if past entry window."""
        if self.phase() != Phase.ENTRY:
            return 0.0
        return max(0.0, (self.start_time + self.entry_window_s) - time.time())


class MarketLifecycleManager:
    """
    Manages lifecycle state for all active markets.
    One market per asset at a time.
    """

    def __init__(self, entry_window_s: int = 60, exit_window_s: int = 30):
        self._markets: dict[str, MarketState] = {}  # asset → MarketState
        self._entry_window_s = entry_window_s
        self._exit_window_s = exit_window_s

    def register_market(self, asset: str, market_id: str,
                        start_time: datetime, end_time: datetime) -> MarketState:
        """Register a discovered market. Replaces any existing one for this asset."""
        start_ts = start_time.timestamp()
        end_ts = end_time.timestamp()
        duration = end_ts - start_ts

        # Check if this is actually a new market or the same one
        existing = self._markets.get(asset)
        if existing and existing.market_id == market_id:
            return existing

        state = MarketState(
            asset=asset,
            market_id=market_id,
            start_time=start_ts,
            end_time=end_ts,
            duration_s=duration,
            entry_window_s=self._entry_window_s,
            exit_window_s=self._exit_window_s,
        )
        self._markets[asset] = state
        logger.info("Lifecycle: %s registered market %s (%.0fs duration)",
                     asset, market_id[:30], duration)
        return state

    def mark_first_data(self, asset: str) -> None:
        """Called when WS first receives data for this market's tokens."""
        state = self._markets.get(asset)
        if state and state.first_seen <= 0:
            state.first_seen = time.time()

    def mark_entry(self, asset: str) -> None:
        """Called when an entry is made for this asset."""
        state = self._markets.get(asset)
        if state:
            state.entry_made = True

    def get(self, asset: str) -> MarketState | None:
        return self._markets.get(asset)

    def all_states(self) -> dict[str, MarketState]:
        return dict(self._markets)

    def active_assets(self) -> list[str]:
        """Assets with markets in ENTRY or HOLD phase."""
        return [
            asset for asset, state in self._markets.items()
            if state.phase() in (Phase.ENTRY, Phase.HOLD, Phase.EXIT)
        ]

    def cleanup_resolved(self) -> list[str]:
        """Remove resolved markets. Returns list of removed asset keys."""
        resolved = [
            asset for asset, state in self._markets.items()
            if state.is_resolved()
        ]
        for asset in resolved:
            del self._markets[asset]
        return resolved
