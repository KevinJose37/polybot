"""Execution Manager for translating quotes into actions."""

from dataclasses import dataclass
from typing import Literal
import structlog

from .quoting_engine import QuoteResult

logger = structlog.get_logger(__name__)

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


def is_adverse_requote(live: float, target: float, side: Side) -> bool:
    """
    True if our live quote is overly aggressive compared to the new target.
    Protects against adverse selection.
    """
    if side == "BID":
        return target < live
    else:
        return target > live

def exceeds_threshold(live: float, target: float, side: Side, threshold: float) -> bool:
    """
    True if the target quote improves upon the live quote by more than threshold.
    """
    if side == "BID":
        return (target - live) > threshold
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
        order_size_usdc: float,
        cancel_cooldown_seconds: float = 0.5,
        requote_cooldown_seconds: float = 1.0
    ):
        self.requote_threshold = requote_threshold
        self.dwell_min_seconds = dwell_min_seconds
        self.max_open_orders = max_open_orders
        self.order_size_usdc = order_size_usdc
        # F-04: Configurable rate limit cooldowns (previously hardcoded 0.2/0.5)
        self.cancel_cooldown_seconds = cancel_cooldown_seconds
        self.requote_cooldown_seconds = requote_cooldown_seconds
        
        # market_id -> list of live orders
        self.live_orders: dict[str, list[LiveOrder]] = {}
        # Rate limit tracking: market_side -> timestamp
        self.last_requote_time: dict[str, float] = {}
        # Cancel rate limit tracking: market_side -> timestamp
        self.last_cancel_time: dict[str, float] = {}

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

    def process_quotes(self, market_id: str, quotes: QuoteResult, current_time: float, min_order_size: float = 0.0) -> list[Action]:
        """
        Compare target quotes against live orders and generate required actions.
        Returns a list of Actions (CancelOrder, PlaceOrder).
        """
        actions: list[Action] = []
        market_orders = self.live_orders.get(market_id, [])
        
        # Find active orders on each side
        live_bid = next((o for o in market_orders if o.side == "BID" and o.status == "live"), None)
        live_ask = next((o for o in market_orders if o.side == "ASK" and o.status == "live"), None)
        
        actions.extend(self._process_side(market_id, "BID", quotes.bid, live_bid, current_time, min_order_size))
        actions.extend(self._process_side(market_id, "ASK", quotes.ask, live_ask, current_time, min_order_size))
        
        return actions

    def _process_side(
        self,
        market_id: str,
        side: Side,
        target_price: float | None,
        live_order: LiveOrder | None,
        current_time: float,
        min_order_size: float
    ) -> list[Action]:
        actions: list[Action] = []
        
        # 1. No target quote (e.g. emergency halt)
        if target_price is None:
            if live_order:
                # Cancel immediately, ignoring dwell time (emergency)
                logger.warning("dwell_violation_bypass", market_id=market_id, side=side,
                               live=live_order.price, target=None, reason="emergency_halt")
                actions.append(CancelOrder(order_id=live_order.id, market_id=market_id))
                live_order.status = "pending_cancel"
            return actions

        side_key = f"{market_id}_{side}"
        cooldown_elapsed = current_time - self.last_requote_time.get(side_key, 0.0) >= self.requote_cooldown_seconds
        cancel_cooldown_elapsed = current_time - self.last_cancel_time.get(side_key, 0.0) >= self.cancel_cooldown_seconds

        # 2. Target quote exists, live order exists
        if live_order:
            # 1. Check if quote is adversely offside — if yes, cancel immediately regardless of dwell
            if is_adverse_requote(live_order.price, target_price, side):
                if cancel_cooldown_elapsed:
                    logger.warning("dwell_violation_bypass", market_id=market_id, side=side,
                                   live=live_order.price, target=target_price, reason="adverse_selection")
                    actions.append(CancelOrder(order_id=live_order.id, market_id=market_id))
                    live_order.status = "pending_cancel"
                    self.last_cancel_time[side_key] = current_time
                
                    # Place new order right away if we have room
                    if self.get_live_count() < self.max_open_orders and cooldown_elapsed:
                        size_shares = self.order_size_usdc / target_price
                        # [M-2] Suppress orders below exchange minimum
                        if size_shares >= min_order_size:
                            actions.append(PlaceOrder(market_id=market_id, side=side, price=target_price, size=size_shares))
                            self.last_requote_time[side_key] = current_time
                        else:
                            logger.warning("order_below_min_size", size=size_shares, min=min_order_size)
                return actions

            # 2. Check dwell — only reached if not an adverse requote
            if current_time - live_order.created_at < self.dwell_min_seconds:
                # Still within dwell period, do not replace
                return actions
                
            # 3. Check requote threshold — only reached if dwell has elapsed
            if exceeds_threshold(live_order.price, target_price, side, self.requote_threshold) and cooldown_elapsed and cancel_cooldown_elapsed:
                actions.append(CancelOrder(order_id=live_order.id, market_id=market_id))
                live_order.status = "pending_cancel"
                self.last_cancel_time[side_key] = current_time
                
                # Check max open orders before placing new one
                if self.get_live_count() < self.max_open_orders:
                    size_shares = self.order_size_usdc / target_price
                    # [M-2] Suppress orders below exchange minimum
                    if size_shares >= min_order_size:
                        actions.append(PlaceOrder(market_id=market_id, side=side, price=target_price, size=size_shares))
                        self.last_requote_time[side_key] = current_time
                    else:
                        logger.warning("order_below_min_size", size=size_shares, min=min_order_size)
            return actions

        # 3. Target quote exists, no live order
        if self.get_live_count() < self.max_open_orders and cooldown_elapsed:
            size_shares = self.order_size_usdc / target_price
            # [M-2] Suppress orders below exchange minimum
            if size_shares >= min_order_size:
                actions.append(PlaceOrder(market_id=market_id, side=side, price=target_price, size=size_shares))
                self.last_requote_time[side_key] = current_time
            else:
                logger.warning("order_below_min_size", size=size_shares, min=min_order_size)
            
        return actions
