"""Interfaces for the Core Bot."""

from typing import Protocol


class PolymarketClientProtocol(Protocol):
    """Protocol defining the API client interaction with the exchange."""

    async def fetch_inventory(self, market_id: str) -> float:
        """Fetch the canonical inventory from the exchange (REST)."""
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
