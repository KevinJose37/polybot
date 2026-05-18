"""Abstract base classes for market data adapters."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass
class TradeEvent:
    market_id: str
    price: float
    size: float
    timestamp: float

@dataclass
class OrderBook:
    market_id: str
    bids: list[tuple[float, float]]  # [(price, size), ...] sorted desc
    asks: list[tuple[float, float]]  # [(price, size), ...] sorted asc

    @property
    def mid_price(self) -> float | None:
        bid = self.bids[0][0] if self.bids else None
        ask = self.asks[0][0] if self.asks else None
        
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        elif bid is not None:
            return bid
        elif ask is not None:
            return ask
        return None

    def depth_at(self, price: float, side: str) -> float:
        """Get the total size resting at exactly this price level."""
        levels = self.bids if side == "BID" else self.asks
        for p, s in levels:
            if p == price:
                return s
        return 0.0


class OrderBookAdapter(Protocol):
    """Interface for subscribing to L2 order books."""

    async def connect_and_run(self) -> None: ...
    async def close(self) -> None: ...
    def set_callback(self, callback: Callable[[OrderBook], Awaitable[None]]) -> None: ...
    def set_trade_callback(self, callback: Callable[[TradeEvent], Awaitable[None]]) -> None: ...
    def subscribe(self, market_ids: list[str]) -> None: ...


class SpotReferenceAdapter(Protocol):
    """Interface for subscribing to external spot references (e.g. Binance)."""

    async def connect_and_run(self) -> None: ...
    async def close(self) -> None: ...
    def set_callback(self, callback: Callable[[str, float], Awaitable[None]]) -> None: ...
    def subscribe(self, assets: list[str]) -> None: ...
