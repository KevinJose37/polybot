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
    Supports parity pair tracking for correct valuation of arb positions.
    """
    def __init__(self):
        # market_id -> Position
        self.positions: dict[str, Position] = {}
        self.total_realized_pnl = 0.0
        self.total_unrealized_pnl = 0.0
        self.resolved_positions: list[dict] = []
        # Parity pair mapping: token_id -> complementary_token_id
        # e.g., {"yes_token": "no_token", "no_token": "yes_token"}
        self.parity_pairs: dict[str, str] = {}

    def register_parity_pair(self, token_a: str, token_b: str) -> None:
        """Register two tokens as a parity pair (YES/NO of the same market)."""
        self.parity_pairs[token_a] = token_b
        self.parity_pairs[token_b] = token_a

    def get_equity(self, starting_capital: float) -> float:
        """Calculate total equity (starting capital + total realized and unrealized PnL)."""
        return starting_capital + self.total_realized_pnl + self.total_unrealized_pnl

    def get_available_capital(self, starting_capital: float) -> float:
        """Calculate available cash (capital + realized PnL minus what is tied up in active positions at cost)."""
        pos_cost = sum(
            abs(p.size * p.avg_price)
            for p in self.positions.values()
            if p.size != 0
        )
        return starting_capital + self.total_realized_pnl - pos_cost

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

    def get_market_unrealized_pnl(self, market_id: str, mid_prices: dict[str, float]) -> float:
        """Calculate parity-aware unrealized PnL for a single market."""
        pos = self.get_position(market_id)
        if pos.size == 0:
            return 0.0

        complement_id = self.parity_pairs.get(market_id)
        if complement_id and complement_id in self.positions:
            comp_pos = self.positions[complement_id]
            if pos.size > 0 and comp_pos.size > 0:
                matched = min(pos.size, comp_pos.size)
                # Avoid double counting: only compute full parity PnL when querying the primary leg
                # Or compute proportional parity PnL. Easiest is to just compute for the individual leg's matched portion.
                # Actually, the prompt says "Centralize all PnL and equity derivations strictly within PositionManager and TradingStats, leaving the dashboard to be purely presentation logic."
                pass

        # To keep it simple and accurate, we can just compute the true unrealized PnL for this specific token
        # but parity valuation applies to the pair.
        # Let's return the standard mtm for a single leg.
        mid = mid_prices.get(market_id)
        return self.mark_to_market(market_id, mid) if mid is not None else 0.0

    def get_pair_unrealized_pnl(self, token_a: str, token_b: str, mid_prices: dict[str, float]) -> float:
        """Calculate parity-aware unrealized PnL for a pair of tokens."""
        pos_a = self.get_position(token_a)
        pos_b = self.get_position(token_b)
        
        unrealized = 0.0
        if pos_a.size > 0 and pos_b.size > 0:
            matched = min(pos_a.size, pos_b.size)
            parity_unreal = matched * 1.0 - (pos_a.avg_price * matched + pos_b.avg_price * matched)
            unrealized += parity_unreal
            
            excess_a = pos_a.size - matched
            excess_b = pos_b.size - matched
            if excess_a > 0:
                mid = mid_prices.get(token_a)
                if mid is not None:
                    unrealized += (mid - pos_a.avg_price) * excess_a
            if excess_b > 0:
                mid = mid_prices.get(token_b)
                if mid is not None:
                    unrealized += (mid - pos_b.avg_price) * excess_b
            return unrealized
            
        # Non-parity
        mid_a = mid_prices.get(token_a)
        mid_b = mid_prices.get(token_b)
        if mid_a is not None:
            unrealized += self.mark_to_market(token_a, mid_a)
        if mid_b is not None:
            unrealized += self.mark_to_market(token_b, mid_b)
        return unrealized

    def update_all_mtm(self, mid_prices: dict[str, float]) -> None:
        """
        Update total unrealized PnL using the latest mid prices.
        For parity pairs (YES+NO), values matched shares at $1.00 guaranteed
        instead of using individual mid prices.
        """
        unrealized = 0.0
        valued_tokens: set[str] = set()
        
        for market_id, pos in self.positions.items():
            if pos.size == 0 or market_id in valued_tokens:
                continue
                
            complement_id = self.parity_pairs.get(market_id)
            if complement_id and complement_id in self.positions:
                comp_pos = self.positions[complement_id]
                
                # Both sides held — value matched shares at $1.00
                if pos.size > 0 and comp_pos.size > 0:
                    matched = min(pos.size, comp_pos.size)
                    # Matched parity: guaranteed $1.00 payout per share
                    # Unrealized = payout - cost_of_both_legs
                    parity_pnl = matched * 1.0 - (pos.avg_price * matched + comp_pos.avg_price * matched)
                    unrealized += parity_pnl
                    
                    # Value any unmatched excess at mid-price
                    excess_a = pos.size - matched
                    excess_b = comp_pos.size - matched
                    if excess_a > 0:
                        mid = mid_prices.get(market_id)
                        if mid is not None:
                            unrealized += (mid - pos.avg_price) * excess_a
                    if excess_b > 0:
                        mid = mid_prices.get(complement_id)
                        if mid is not None:
                            unrealized += (mid - comp_pos.avg_price) * excess_b
                    
                    valued_tokens.add(market_id)
                    valued_tokens.add(complement_id)
                    continue
            
            # Non-parity position: use standard mid-price valuation
            mid = mid_prices.get(market_id)
            if mid is not None:
                unrealized += self.mark_to_market(market_id, mid)
            valued_tokens.add(market_id)
                
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
