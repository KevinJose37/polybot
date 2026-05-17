"""
Local L2 orderbook.
"""
import asyncio
from typing import Optional
import structlog

from bot.api.schemas import OrderBookSnapshot
from bot.orderbook.book_state import BookState
from bot.orderbook.reconciliation import check_sequence, SequenceGapError
from bot.utils.clocks import current_timestamp_ms

logger = structlog.get_logger(__name__)

class LocalOrderBook:
    """
    Maintains the L2 orderbook for a specific token_id.
    """
    def __init__(self, token_id: str, stale_threshold_ms: int = 5000):
        self.token_id = token_id
        self.stale_threshold_ms = stale_threshold_ms
        self.state = BookState.PENDING
        
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_updated_ts: int = 0
        self.last_sequence: int | None = None
        
        self._lock = asyncio.Lock()

    async def apply_snapshot(self, snapshot: OrderBookSnapshot, sequence: int | None = None) -> None:
        """Initialize book from snapshot."""
        async with self._lock:
            self.bids = {price: size for price, size in snapshot.bids}
            self.asks = {price: size for price, size in snapshot.asks}
            self.last_updated_ts = current_timestamp_ms()
            self.last_sequence = sequence
            self.state = BookState.ACTIVE
            logger.debug("book_snapshot_applied", token_id=self.token_id)

    async def apply_delta(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]], sequence: int) -> None:
        """Apply a delta update atomically."""
        async with self._lock:
            if self.state != BookState.ACTIVE:
                # Discard deltas if we are waiting for snapshot or disconnected
                return
                
            # Accept any sequence that is >= our last seen (monotonic ordering).
            # Polymarket uses timestamps, not strict +1 counters.
            if self.last_sequence is not None and sequence > 0 and sequence < self.last_sequence:
                logger.debug("stale_delta_skipped", token_id=self.token_id, 
                           last=self.last_sequence, received=sequence)
                return
            
            for price, size in bids:
                if size == 0.0:
                    self.bids.pop(price, None)
                else:
                    self.bids[price] = size
                    
            for price, size in asks:
                if size == 0.0:
                    self.asks.pop(price, None)
                else:
                    self.asks[price] = size
                    
            self.last_sequence = sequence
            self.last_updated_ts = current_timestamp_ms()

    def is_stale(self) -> bool:
        """Check if the book age exceeds the stale threshold."""
        if self.state != BookState.ACTIVE:
            return True
        
        age = current_timestamp_ms() - self.last_updated_ts
        if age > self.stale_threshold_ms:
            return True
            
        return False

    def best_bid(self) -> Optional[float]:
        if self.is_stale() or not self.bids:
            return None
        return max(self.bids.keys())

    def best_ask(self) -> Optional[float]:
        if self.is_stale() or not self.asks:
            return None
        return min(self.asks.keys())

    def bid_depth(self, levels: int = 3) -> list[tuple[float, float]]:
        """Returns top N levels of bids: [(price, size), ...]"""
        if self.is_stale():
            return []
        sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)
        return sorted_bids[:levels]
        
    def ask_depth(self, levels: int = 3) -> list[tuple[float, float]]:
        """Returns top N levels of asks: [(price, size), ...]"""
        if self.is_stale():
            return []
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])
        return sorted_asks[:levels]

    def mid_price(self) -> Optional[float]:
        """Returns the mid price of the orderbook, or None if bids/asks are missing."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        elif bid is not None:
            return bid
        elif ask is not None:
            return ask
        return None
