"""
risk/exposure.py — Portfolio-level exposure tracking.
Tracks capital allocation and unrealized PnL across all positions.
"""

from loguru import logger
from config.settings import config
from utils.schemas import InventoryState, FairValueEstimate


class ExposureTracker:
    """
    Tracks capital usage, exposure per asset, and unrealized PnL.
    """

    def __init__(self, initial_capital: float = 0):
        self.initial_capital = initial_capital or config.initial_capital
        self.available_capital = self.initial_capital
        self.realized_pnl = 0.0
        self._capital_in_use = 0.0

    @property
    def total_capital(self) -> float:
        return self.initial_capital + self.realized_pnl

    @property
    def capital_in_use(self) -> float:
        return self._capital_in_use

    def update_capital(self, total_pnl: float, inventories: dict[str, InventoryState]):
        """Update capital tracking accurately using PnL and current inventory."""
        self.realized_pnl = total_pnl  # We use total PnL to represent portfolio growth
        
        capital_locked = 0.0
        for inv in inventories.values():
            if inv.net_position != 0:
                capital_locked += abs(inv.net_position) * inv.avg_entry_price
                
        self._capital_in_use = capital_locked
        self.available_capital = self.total_capital - self._capital_in_use

    def record_buy(self, cost: float):
        """No-op, we use update_capital now."""
        pass

    def record_sell(self, revenue: float):
        """No-op, we use update_capital now."""
        pass

    def record_realized_pnl(self, pnl: float):
        """No-op"""
        pass

    def compute_unrealized_pnl(
        self,
        inventories: dict[str, InventoryState],
        fair_values: dict[str, float],
    ) -> float:
        """
        Compute total unrealized PnL from open positions.
        MTM = sum of (current_fair_value - avg_entry_price) * position_size

        Args:
            inventories: market_key -> InventoryState
            fair_values: market_key -> current fair value probability
        """
        total_unrealized = 0.0

        for key, inv in inventories.items():
            if inv.net_position == 0:
                continue

            current_fv = fair_values.get(key, 0.5)

            if inv.net_position > 0:
                # Long: profit if FV > entry
                unrealized = (current_fv - inv.avg_entry_price) * inv.net_position
            else:
                # Short: profit if FV < entry
                unrealized = (inv.avg_entry_price - current_fv) * abs(inv.net_position)

            total_unrealized += unrealized

        return total_unrealized

    def get_exposure_by_asset(
        self, inventories: dict[str, InventoryState]
    ) -> dict[str, float]:
        """
        Compute exposure (absolute notional) per asset.
        Returns: {asset: total_exposure_usd}
        """
        exposure = {}
        for key, inv in inventories.items():
            asset = inv.asset
            notional = abs(inv.net_position) * inv.avg_entry_price if inv.avg_entry_price > 0 else 0
            exposure[asset] = exposure.get(asset, 0) + notional
        return exposure

    def can_open_position(self, cost: float) -> bool:
        """Check if there's enough available capital for a new position."""
        return cost <= self.available_capital

    def get_summary(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "available_capital": self.available_capital,
            "capital_in_use": self.capital_in_use,
            "realized_pnl": self.realized_pnl,
            "total_capital": self.total_capital,
        }
