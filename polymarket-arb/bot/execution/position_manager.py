"""
Position and PnL management.
"""
import structlog
from dataclasses import dataclass, field
from typing import Optional

logger = structlog.get_logger(__name__)


@dataclass
class Position:
    market_id: str
    size: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0


class PositionManager:
    """
    Tracks inventory and realized/unrealized PnL.
    """
    def __init__(self):
        # market_id -> Position
        self.positions: dict[str, Position] = {}
        self.total_realized_pnl = 0.0
        self.total_unrealized_pnl = 0.0
        self.resolved_positions: list[dict] = []

    def get_position(self, market_id: str) -> Position:
        if market_id not in self.positions:
            self.positions[market_id] = Position(market_id=market_id)
        return self.positions[market_id]

    def add_fill(self, market_id: str, side: str, price: float, size: float, fee: float = 0.0) -> None:
        """
        Update position with a new fill.
        Fee is the total fee for this fill (subtracted from PnL).
        """
        # Deduct fee from realized PnL immediately
        if fee > 0:
            pos = self.get_position(market_id)
            pos.realized_pnl -= fee
            self.total_realized_pnl -= fee
        pos = self.get_position(market_id)
        
        # BUY = positive size, SELL = negative size
        fill_qty = size if side == "BUY" else -size
        
        # Check if this trade reduces or flips position
        if (pos.size > 0 and fill_qty < 0) or (pos.size < 0 and fill_qty > 0):
            # We are closing some or all of the position
            close_qty = min(abs(pos.size), abs(fill_qty))
            
            # Realized PnL logic
            if side == "SELL":
                # Closing a long position: pnl = (sell_price - avg_buy_price) * size
                pnl = (price - pos.avg_price) * close_qty
            else:
                # Closing a short position: pnl = (avg_sell_price - buy_price) * size
                pnl = (pos.avg_price - price) * close_qty
                
            pos.realized_pnl += pnl
            self.total_realized_pnl += pnl
            
            remaining_qty = fill_qty + (close_qty if fill_qty < 0 else -close_qty)
            pos.size += fill_qty
            
            if abs(pos.size) < 1e-6:
                pos.size = 0.0
                pos.avg_price = 0.0
            elif remaining_qty != 0:
                # Flipped position
                pos.avg_price = price
        else:
            # Increasing position
            new_size = pos.size + fill_qty
            pos.avg_price = ((pos.avg_price * abs(pos.size)) + (price * abs(fill_qty))) / abs(new_size)
            pos.size = new_size

    def mark_to_market(self, market_id: str, mid_price: Optional[float]) -> float:
        """
        Calculate unrealized PnL for a market given the latest mid price.
        """
        pos = self.get_position(market_id)
        if pos.size == 0.0 or mid_price is None:
            return 0.0
            
        if pos.size > 0:
            return (mid_price - pos.avg_price) * pos.size
        else:
            return (pos.avg_price - mid_price) * abs(pos.size)

    def update_all_mtm(self, mid_prices: dict[str, float]) -> None:
        """
        Update total unrealized PnL using the latest mid prices.
        """
        unrealized = 0.0
        for market_id, pos in self.positions.items():
            if pos.size != 0:
                mid = mid_prices.get(market_id)
                if mid is not None:
                    unrealized += self.mark_to_market(market_id, mid)
        self.total_unrealized_pnl = unrealized

    def settle_market(self, market_id: str, settle_price: float = 0.5) -> None:
        """
        Settle a position when a market resolves.
        Approximates resolution to 0.5 per leg, mathematically correct for arbitrage sets.
        """
        if market_id not in self.positions:
            return
            
        pos = self.positions[market_id]
        if pos.size == 0:
            del self.positions[market_id]
            return
            
        # PnL = (settle_price - avg_price) * size for Long
        # PnL = (avg_price - settle_price) * size for Short
        if pos.size > 0:
            pnl = (settle_price - pos.avg_price) * pos.size
        else:
            pnl = (pos.avg_price - settle_price) * abs(pos.size)
            
        pos.realized_pnl += pnl
        self.total_realized_pnl += pnl
        
        self.resolved_positions.append({
            "market_id": market_id,
            "size": pos.size,
            "avg_price": pos.avg_price,
            "settle_price": settle_price,
            "pnl": pnl,
            "total_realized_pnl": pos.realized_pnl
        })
        if len(self.resolved_positions) > 10:
            self.resolved_positions.pop(0)
        
        pos.size = 0.0
        pos.avg_price = 0.0
        del self.positions[market_id]
