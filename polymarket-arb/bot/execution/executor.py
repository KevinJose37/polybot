"""
Executor Protocol.
"""
from typing import Protocol
from bot.api.schemas import OrderRequest, OrderAck
from bot.arbitrage.opportunity import ArbOpportunity

class ExecutorProtocol(Protocol):
    """
    Protocol for order execution. Both live and paper trading implement this.
    """
    async def execute_opportunity(self, opportunity: ArbOpportunity) -> list[OrderAck]:
        """Execute a full arbitrage opportunity."""
        ...
        
    async def place_order(self, order: OrderRequest, opp: ArbOpportunity | None = None) -> OrderAck:
        """Place a single order."""
        ...
        
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order."""
        ...
