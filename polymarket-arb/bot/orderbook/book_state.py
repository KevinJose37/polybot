"""
Orderbook state definitions.
"""
from enum import Enum


class BookState(Enum):
    """Lifecycle state of a local orderbook."""
    PENDING = "pending"           # Waiting for snapshot
    ACTIVE = "active"             # Snapshot received, applying deltas
    STALE = "stale"               # No updates within threshold
    DISCONNECTED = "disconnected" # Feed disconnected, sequence gap, etc.
