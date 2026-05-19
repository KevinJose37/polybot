"""Interfaces for the Core Bot."""

from typing import Protocol, Any
from dataclasses import dataclass
from datetime import datetime

@dataclass
class MarketContext:
    condition_id: str
    tick_size: float
    min_order_size: float
    expiry_utc: datetime
    chainlink_feed: str
    strike_price: float | None
    token_id_yes: str
    token_id_no: str

class PolymarketClientProtocol(Protocol):
    """Protocol defining the API client interaction with the exchange."""

    async def fetch_inventory(self, market_id: str) -> float:
        """Fetch the canonical inventory from the exchange (REST)."""
        ...

    async def get_clob_market_info(self, condition_id: str) -> Any:
        """Fetch CLOB market info (mts, mos, tokens)."""
        ...
        
    async def get_market(self, condition_id: str) -> Any:
        """Fetch general market info (question, end date)."""
        ...

    def get_inventory(self, market_id: str) -> float:
        """Get the cached inventory for quoting calculations (Sync)."""
        ...

    async def place_order(self, market_id: str, side: str, price: float, size: float) -> str:
        """Place an order and return the order_id."""
        ...

    async def cancel_order(self, order_id: str, market_id: str) -> bool:
        """Cancel an order by ID."""
        ...
