"""
data/feed_health.py — Feed staleness monitoring and health checks.
"""

import time
from loguru import logger


class FeedHealthMonitor:
    """
    Monitors the health (freshness) of all data feeds.
    Triggers alerts and circuit breaker actions when feeds become stale.
    """

    def __init__(self, stale_threshold_ms: int = 500):
        self.stale_threshold_ms = stale_threshold_ms
        self._timestamps: dict[str, int] = {}  # feed_name -> last_update_ms

    def update(self, feed_name: str, timestamp_ms: int = 0):
        """Record a feed heartbeat."""
        if timestamp_ms == 0:
            timestamp_ms = int(time.time() * 1000)
        self._timestamps[feed_name] = timestamp_ms

    def is_stale(self, feed_name: str) -> bool:
        """Check if a feed is stale."""
        if feed_name not in self._timestamps:
            return True
        age_ms = int(time.time() * 1000) - self._timestamps[feed_name]
        return age_ms > self.stale_threshold_ms

    def get_age_ms(self, feed_name: str) -> int:
        """Get age of the last update."""
        if feed_name not in self._timestamps:
            return 999999
        return int(time.time() * 1000) - self._timestamps[feed_name]

    def get_status(self) -> dict[str, dict]:
        """Get health status of all feeds."""
        now_ms = int(time.time() * 1000)
        status = {}
        for name, ts in self._timestamps.items():
            age = now_ms - ts
            status[name] = {
                "age_ms": age,
                "stale": age > self.stale_threshold_ms,
                "status": "LIVE" if age <= self.stale_threshold_ms else "STALE",
            }
        return status
