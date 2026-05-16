"""
Fill and execution deduplication management.
"""
import structlog
from bot.utils.clocks import current_timestamp_ms

logger = structlog.get_logger(__name__)

class FillManager:
    """
    Manages active opportunities to avoid duplicate execution.
    Also tracks inflight orders to enable cancellations and TTL enforcement.
    """
    def __init__(self, dedup_window_ms: int = 60000):
        self.dedup_window_ms = dedup_window_ms
        self.active_opportunities: dict[str, int] = {}
        self.inflight_orders: dict[str, dict] = {}  # Tracks open orders

    def is_duplicate(self, opportunity_id: str) -> bool:
        """
        Returns True if the opportunity was executed within the dedup window.
        """
        now = current_timestamp_ms()
        if opportunity_id in self.active_opportunities:
            timestamp = self.active_opportunities[opportunity_id]
            if now - timestamp < self.dedup_window_ms:
                return True
            else:
                # Expired
                del self.active_opportunities[opportunity_id]
        return False

    def mark_executed(self, opportunity_id: str) -> None:
        """Mark an opportunity as executed."""
        self.active_opportunities[opportunity_id] = current_timestamp_ms()

    def check_and_mark(self, opportunity_id: str) -> bool:
        """Atomically check for duplicate and mark as executed if not.
        Returns True if the opportunity was already executed (is a duplicate).
        Returns False and marks it as executed if it was not a duplicate.
        """
        if self.is_duplicate(opportunity_id):
            return True
        self.mark_executed(opportunity_id)
        return False

    def add_inflight_order(self, order_id: str, order_data: dict) -> None:
        """Track an order that is currently pending on the exchange."""
        order_data["created_at_ms"] = current_timestamp_ms()
        self.inflight_orders[order_id] = order_data

    def remove_inflight_order(self, order_id: str) -> None:
        """Remove a tracked order once filled, cancelled, or rejected."""
        self.inflight_orders.pop(order_id, None)

    def check_expired_orders(self, timeout_s: float) -> list[str]:
        """
        Returns order IDs that have exceeded the TTL timeout.
        These should be cancelled by the execution layer.
        """
        now = current_timestamp_ms()
        timeout_ms = int(timeout_s * 1000)
        expired = []
        for order_id, data in self.inflight_orders.items():
            created = data.get("created_at_ms", 0)
            if now - created > timeout_ms:
                logger.warning("order_ttl_expired", order_id=order_id, age_ms=now - created)
                expired.append(order_id)
        return expired

    def cleanup(self) -> None:
        """Remove expired opportunities from memory."""
        now = current_timestamp_ms()
        expired = [opp_id for opp_id, ts in self.active_opportunities.items() if now - ts >= self.dedup_window_ms]
        for opp_id in expired:
            del self.active_opportunities[opp_id]

