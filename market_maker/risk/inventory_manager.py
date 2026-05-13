"""
risk/inventory_manager.py — Position limits, inventory reduction logic.
Implements soft/hard inventory limits with progressive skew adjustment.
"""

import time
from loguru import logger

from config.settings import config
from utils.schemas import InventoryState, MarketState, FillRecord


class InventoryManager:
    """
    Manages inventory (position) tracking across all markets.

    Limits:
    - Per-market: MAX_INVENTORY_PER_MARKET
    - Per-asset: MAX_NET_ASSET_INVENTORY
    - Total portfolio: MAX_NET_TOTAL_INVENTORY

    Behaviors by utilization:
    - < soft_limit (60%): Normal quoting with standard skew
    - soft_limit to hard_limit (60-85%): Aggressive skew increase
    - > hard_limit (85%): One-sided quoting only
    - >= max (100%): Circuit breaker, emergency exit
    """

    def __init__(self):
        # Per-market inventory: market_key -> InventoryState
        self._inventories: dict[str, InventoryState] = {}

        # Per-asset aggregate tracking
        self._asset_net: dict[str, int] = {}  # asset -> total net position across markets

    def get_or_create(
        self, market_key: str, asset: str, window_minutes: int, market_id: str = ""
    ) -> InventoryState:
        """Get existing inventory state or create a new one."""
        if market_key not in self._inventories:
            self._inventories[market_key] = InventoryState(
                market_id=market_id or market_key,
                asset=asset,
                window_minutes=window_minutes,
                net_position=0,
                max_position=config.max_inventory_per_market,
            )
        return self._inventories[market_key]

    def get(self, market_key: str) -> InventoryState | None:
        """Get inventory state for a market, or None."""
        return self._inventories.get(market_key)

    def record_fill(self, market_key: str, fill: FillRecord):
        """
        Update inventory from a fill.
        BUY  -> increase net position (more long)
        SELL -> decrease net position (more short)
        """
        inv = self._inventories.get(market_key)
        if not inv:
            logger.warning(f"[Inv] No inventory state for {market_key}")
            return

        if fill.side == "BUY":
            inv.net_position += fill.size
            inv.total_bought += fill.size
        else:
            inv.net_position -= fill.size
            inv.total_sold += fill.size

        # Update average entry price (simplified)
        if fill.side == "BUY" and inv.net_position > 0:
            # Weighted average for long positions
            total_value = inv.avg_entry_price * (inv.net_position - fill.size) + fill.price * fill.size
            inv.avg_entry_price = total_value / inv.net_position if inv.net_position > 0 else 0
        elif fill.side == "SELL" and inv.net_position < 0:
            # Weighted average for short positions
            total_value = inv.avg_entry_price * (abs(inv.net_position) - fill.size) + fill.price * fill.size
            inv.avg_entry_price = total_value / abs(inv.net_position) if inv.net_position != 0 else 0

        # Update asset-level aggregate
        self._update_asset_net(inv.asset)

        logger.debug(
            f"[Inv] Fill recorded: {market_key} {fill.side} {fill.size}@{fill.price:.4f} "
            f"-> net={inv.net_position}, util={inv.utilization:.1%}"
        )

    def _update_asset_net(self, asset: str):
        """Recompute total net position for an asset across all markets."""
        total = 0
        for inv in self._inventories.values():
            if inv.asset == asset:
                total += inv.net_position
        self._asset_net[asset] = total

    def get_quoting_mode(self, market_key: str) -> MarketState:
        """
        Determine quoting mode based on inventory utilization.

        Returns:
            MarketState indicating what quoting mode to use.
        """
        inv = self._inventories.get(market_key)
        if not inv:
            return MarketState.QUOTING_BOTH

        util = inv.utilization

        if util >= 1.0:
            return MarketState.EMERGENCY
        elif util >= config.hard_inventory_pct:
            return MarketState.ONE_SIDED
        elif util >= config.soft_inventory_pct:
            # Still quoting both sides but with aggressive skew
            return MarketState.QUOTING_BOTH
        else:
            return MarketState.QUOTING_BOTH

    def get_skew_boost(self, market_key: str) -> float:
        """
        Get the inventory skew boost factor.
        Returns 1.0 for normal, >1.0 for soft-limit range, 0 if no inventory exists.
        """
        inv = self._inventories.get(market_key)
        if not inv:
            return 1.0

        util = inv.utilization
        if util >= config.soft_inventory_pct:
            # Progressive boost: 1.0 at soft_limit -> ~4.0 at hard_limit
            excess = (util - config.soft_inventory_pct) / (1.0 - config.soft_inventory_pct)
            return 1.0 + excess * 3.0
        return 1.0

    def should_one_sided_quote(self, market_key: str) -> tuple[bool, str]:
        """
        Check if market should only quote one side.

        Returns:
            (is_one_sided, blocked_side)
            blocked_side is "BUY" or "SELL" — the side that should NOT be quoted.
        """
        inv = self._inventories.get(market_key)
        if not inv:
            return False, ""

        if inv.utilization >= config.hard_inventory_pct:
            if inv.is_long:
                return True, "BUY"   # Stop adding longs
            elif inv.is_short:
                return True, "SELL"  # Stop adding shorts

        return False, ""

    def needs_emergency_exit(self, market_key: str) -> bool:
        """Check if inventory has hit max and needs emergency reduction."""
        inv = self._inventories.get(market_key)
        if not inv:
            return False
        return inv.utilization >= 1.0

    def check_asset_limit(self, asset: str) -> bool:
        """Check if per-asset inventory limit is breached."""
        net = abs(self._asset_net.get(asset, 0))
        return net >= config.max_net_asset_inventory

    def check_total_limit(self) -> bool:
        """Check if total portfolio inventory limit is breached."""
        total = sum(abs(v) for v in self._asset_net.values())
        return total >= config.max_net_total_inventory

    def get_total_net(self) -> dict[str, int]:
        """Get net position per asset."""
        return dict(self._asset_net)

    def get_all_inventories(self) -> dict[str, InventoryState]:
        """Get all inventory states."""
        return dict(self._inventories)

    def reset(self, market_key: str):
        """Reset inventory for a market (e.g., on market rotation)."""
        if market_key in self._inventories:
            asset = self._inventories[market_key].asset
            del self._inventories[market_key]
            self._update_asset_net(asset)
