"""Portfolio Manager for cross-market risk."""

from dataclasses import dataclass
import structlog

logger = structlog.get_logger(__name__)

class PortfolioManager:
    """Tracks global capital deployment across all active markets."""
    
    def __init__(self, max_capital_deployed_pct: float, total_capital: float = 30.0):
        self.max_capital_deployed_pct = max_capital_deployed_pct
        self.total_capital = total_capital

    def is_capacity_exceeded(
        self, 
        inventories: dict[str, float], 
        open_orders_usdc: float, 
        current_prices: dict[str, float]
    ) -> bool:
        """
        Check if total deployed capital (inventory + open orders) exceeds the allowed limit.
        """
        deployed_inventory_usdc = 0.0
        for market_id, shares in inventories.items():
            price = current_prices.get(market_id, 0.5)
            # F-20: For binary outcome tokens, the cost per share is the price
            # paid. Long positions cost `price`, short (NO) positions cost
            # `1 - price`. Use the actual cost basis for accurate capital tracking.
            cost_per_share = price if shares >= 0 else (1.0 - price)
            deployed_inventory_usdc += abs(shares) * cost_per_share
            
        total_deployed = deployed_inventory_usdc + open_orders_usdc
        allowed = self.total_capital * self.max_capital_deployed_pct
        
        if total_deployed >= allowed:
            logger.warning(
                "portfolio_capacity_exceeded", 
                total_deployed=total_deployed, 
                allowed=allowed
            )
            return True
            
        return False
