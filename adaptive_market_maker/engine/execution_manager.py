"""Execution Manager for translating quotes into actions."""

from dataclasses import dataclass
from typing import Literal

from .quoting_engine import QuoteResult

Side = Literal["BID", "ASK"]


@dataclass
class LiveOrder:
    id: str
    market_id: str
    side: Side
    price: float
    size: float
    created_at: float
    status: Literal["live", "pending_cancel", "cancelled", "filled"]


class Action:
    """Base class for execution actions."""
    pass


@dataclass
class PlaceOrder(Action):
    market_id: str
    side: Side
    price: float
    size: float


@dataclass
class CancelOrder(Action):
    order_id: str
    market_id: str


def should_requote(live: float, target: float, side: Side, threshold: float) -> bool:
    """
    Asymmetric requote threshold logic.
    Protects against adverse selection by bypassing the threshold if our live quote
    is overly aggressive compared to the new target.
    """
    if side == "BID":
        if target < live:   # quote got worse (we're overbidding) — urgent
            return True     # bypass threshold, cancel immediately
        else:               # quote got better — apply threshold
            return (target - live) > threshold
    else:  # ASK
        if target > live:   # quote got worse (we're underasking) — urgent
            return True
        else:
            return (live - target) > threshold


class ExecutionManager:
    """
    Manages translation of theoretical quotes into API order actions.
    Enforces dwell times, requote thresholds, and max open orders.
    """
    def __init__(
        self,
        requote_threshold: float,
        dwell_min_seconds: float,
        max_open_orders: int,
        order_size_usdc: float
    ):
        self.requote_threshold = requote_threshold
        self.dwell_min_seconds = dwell_min_seconds
        self.max_open_orders = max_open_orders
        self.order_size_usdc = order_size_usdc
        
        # market_id -> list of live orders
        self.live_orders: dict[str, list[LiveOrder]] = {}

    def get_live_count(self) -> int:
        """Count only strictly 'live' orders (excluding pending_cancel)."""
        count = 0
        for orders in self.live_orders.values():
            count += sum(1 for o in orders if o.status == "live")
        return count

    def update_order_status(self, order_id: str, market_id: str, new_status: Literal["live", "pending_cancel", "cancelled", "filled"]) -> None:
        """Update status or remove if terminal."""
        if market_id in self.live_orders:
            for o in self.live_orders[market_id]:
                if o.id == order_id:
                    o.status = new_status
                    break
            
            # Clean up terminal states
            self.live_orders[market_id] = [
                o for o in self.live_orders[market_id] 
                if o.status not in ("cancelled", "filled")
            ]

    def add_live_order(self, order: LiveOrder) -> None:
        """Track a new live order."""
        if order.market_id not in self.live_orders:
            self.live_orders[order.market_id] = []
        self.live_orders[order.market_id].append(order)

    def process_quotes(self, market_id: str, quotes: QuoteResult, current_time: float) -> list[Action]:
        """
        Compare target quotes against live orders and generate required actions.
        Returns a list of Actions (CancelOrder, PlaceOrder).
        """
        actions: list[Action] = []
        market_orders = self.live_orders.get(market_id, [])
        
        # Find active orders on each side
        live_bid = next((o for o in market_orders if o.side == "BID" and o.status == "live"), None)
        live_ask = next((o for o in market_orders if o.side == "ASK" and o.status == "live"), None)
        
        actions.extend(self._process_side(market_id, "BID", quotes.bid, live_bid, current_time))
        actions.extend(self._process_side(market_id, "ASK", quotes.ask, live_ask, current_time))
        
        return actions

    def _process_side(
        self,
        market_id: str,
        side: Side,
        target_price: float | None,
        live_order: LiveOrder | None,
        current_time: float
    ) -> list[Action]:
        actions: list[Action] = []
        
        # 1. No target quote (e.g. emergency halt)
        if target_price is None:
            if live_order:
                # Cancel immediately, ignoring dwell time (emergency)
                actions.append(CancelOrder(order_id=live_order.id, market_id=market_id))
                live_order.status = "pending_cancel"
            return actions

        # 2. Target quote exists, live order exists
        if live_order:
            # Check dwell time block (preserve rebates unless emergency)
            if current_time - live_order.created_at < self.dwell_min_seconds:
                # Still within dwell period, do not replace
                return actions
                
            # Check requote threshold
            if should_requote(live_order.price, target_price, side, self.requote_threshold):
                actions.append(CancelOrder(order_id=live_order.id, market_id=market_id))
                live_order.status = "pending_cancel"
                
                # Check max open orders before placing new one
                if self.get_live_count() < self.max_open_orders:
                    size_shares = self.order_size_usdc / target_price
                    actions.append(PlaceOrder(market_id=market_id, side=side, price=target_price, size=size_shares))
            return actions

        # 3. Target quote exists, no live order
        if self.get_live_count() < self.max_open_orders:
            size_shares = self.order_size_usdc / target_price
            actions.append(PlaceOrder(market_id=market_id, side=side, price=target_price, size=size_shares))
            
        return actions
