"""
Position and PnL management.
"""
import structlog
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from bot.utils.clocks import current_timestamp_ms

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
        # Use deque with maxlen for O(1) eviction — oldest entries auto-drop
        self.resolved_positions: deque[dict] = deque(maxlen=100)
        # Parity pair mapping: token_id -> complementary_token_id
        # e.g., {"yes_token": "no_token", "no_token": "yes_token"}
        self.parity_pairs: dict[str, str] = {}
        # Track tokens that were settled standalone (at 0.5) so we can
        # retroactively adjust when the complement resolves later.
        self._pending_complement_adjustments: dict[str, dict] = {}

    def register_parity_pair(self, token_a: str, token_b: str) -> None:
        """Register two tokens as a parity pair (YES/NO of the same market)."""
        self.parity_pairs[token_a] = token_b
        self.parity_pairs[token_b] = token_a

    def get_equity(self, starting_capital: float) -> float:
        """Calculate total equity (starting capital + total realized and unrealized PnL)."""
        return starting_capital + self.total_realized_pnl + self.total_unrealized_pnl

    def get_total_equity(self, starting_capital: float) -> float:
        """Alias for get_equity — used by scanner when capital_source='total_equity'."""
        return self.get_equity(starting_capital)

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

        Three mutually exclusive branches:
          1. CLOSE-ONLY: fill fully closes the position (or partially closes it)
          2. FLIP: fill closes the position AND opens a new one in the opposite direction
          3. INCREASE: fill adds to the existing position (same direction)

        Fee is the total fee for this fill (subtracted from PnL immediately).
        """
        pos = self.get_position(market_id)

        # Deduct fee from realized PnL immediately
        if fee > 0:
            pos.realized_pnl -= fee
            self.total_realized_pnl -= fee

        # BUY = positive size, SELL = negative size
        fill_qty = size if side == "BUY" else -size

        is_closing = (pos.size > 0 and fill_qty < 0) or (pos.size < 0 and fill_qty > 0)

        if is_closing:
            close_qty = min(abs(pos.size), abs(fill_qty))

            # Compute realized PnL on the closed portion
            if side == "SELL":
                # Closing a long: pnl = (sell_price - avg_buy_price) × qty
                pnl = (price - pos.avg_price) * close_qty
            else:
                # Closing a short: pnl = (avg_sell_price - buy_price) × qty
                pnl = (pos.avg_price - price) * close_qty

            pos.realized_pnl += pnl
            self.total_realized_pnl += pnl

            new_size = pos.size + fill_qty

            if abs(new_size) < 1e-6:
                # ── Branch 1: CLOSE-ONLY — position fully closed ──
                pos.size = 0.0
                pos.avg_price = 0.0
            elif (new_size > 0) == (pos.size > 0):
                # ── Branch 1b: PARTIAL CLOSE — same direction, keep avg_price ──
                pos.size = new_size
                # avg_price stays unchanged — remaining shares keep original cost basis
            else:
                # ── Branch 2: FLIP — position reversed to opposite direction ──
                pos.size = new_size
                pos.avg_price = price  # new position opens at fill price
        else:
            # ── Branch 3: INCREASE — adding to existing position ──
            new_size = pos.size + fill_qty
            assert abs(new_size) > 0, (
                f"Unexpected zero new_size when increasing position: "
                f"pos.size={pos.size}, fill_qty={fill_qty}"
            )
            pos.avg_price = (
                (pos.avg_price * abs(pos.size)) + (price * abs(fill_qty))
            ) / abs(new_size)
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
        """Calculate parity-aware unrealized PnL for a single market.
        
        Delegates to get_pair_unrealized_pnl when a parity complement exists
        and both tokens have active positions. Returns only this token's share.
        """
        pos = self.get_position(market_id)
        if pos.size == 0:
            return 0.0

        complement_id = self.parity_pairs.get(market_id)
        if complement_id and complement_id in self.positions:
            comp_pos = self.positions[complement_id]
            if comp_pos.size != 0:
                # Both legs active — compute pair-level parity PnL
                pair_pnl = self.get_pair_unrealized_pnl(market_id, complement_id, mid_prices)
                # Return this token's proportional share of the pair PnL
                total_notional = abs(pos.size * pos.avg_price) + abs(comp_pos.size * comp_pos.avg_price)
                if total_notional > 0:
                    my_weight = abs(pos.size * pos.avg_price) / total_notional
                    return pair_pnl * my_weight
                return pair_pnl * 0.5

        # Non-parity: standard mid-price valuation
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
            
        elif pos_a.size < 0 and pos_b.size < 0:
            matched = min(abs(pos_a.size), abs(pos_b.size))
            revenue = (pos_a.avg_price * matched + pos_b.avg_price * matched)
            parity_unreal = revenue - matched * 1.0
            unrealized += parity_unreal
            
            excess_a = abs(pos_a.size) - matched
            excess_b = abs(pos_b.size) - matched
            if excess_a > 0:
                mid = mid_prices.get(token_a)
                if mid is not None:
                    unrealized += (pos_a.avg_price - mid) * excess_a
            if excess_b > 0:
                mid = mid_prices.get(token_b)
                if mid is not None:
                    unrealized += (pos_b.avg_price - mid) * excess_b
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
                
                # Both sides held long — value matched shares at $1.00
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

                # Both sides held short (Type-C SELL) — matched shorts owe $1.00
                if pos.size < 0 and comp_pos.size < 0:
                    matched = min(abs(pos.size), abs(comp_pos.size))
                    # Sold parity: received (avg_price_a + avg_price_b) per matched share
                    # Must pay out $1.00 at settlement
                    # Unrealized = revenue - liability
                    revenue = (pos.avg_price * matched + comp_pos.avg_price * matched)
                    parity_pnl = revenue - matched * 1.0
                    unrealized += parity_pnl
                    
                    # Value any unmatched excess at mid-price (short valuation)
                    excess_a = abs(pos.size) - matched
                    excess_b = abs(comp_pos.size) - matched
                    if excess_a > 0:
                        mid = mid_prices.get(market_id)
                        if mid is not None:
                            unrealized += (pos.avg_price - mid) * excess_a
                    if excess_b > 0:
                        mid = mid_prices.get(complement_id)
                        if mid is not None:
                            unrealized += (comp_pos.avg_price - mid) * excess_b
                    
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

        If this token's parity complement was previously settled at 0.5 (standalone),
        retroactively adjusts the complement's PnL so the pair sums to 1.0.
        """
        if market_id not in self.positions:
            return
            
        pos = self.positions[market_id]
        if pos.size == 0:
            del self.positions[market_id]
            return

        # ── Retroactive complement adjustment ──
        # If the complement was previously settled alone at 0.5 and we now know
        # the pair should sum to 1.0, adjust the PnL delta.
        complement_id = self.parity_pairs.get(market_id)
        if complement_id and complement_id in self._pending_complement_adjustments:
            prev = self._pending_complement_adjustments.pop(complement_id)
            # The complement was settled at 0.5. For parity correctness,
            # it should have been (1.0 - settle_price). Compute the delta.
            correct_complement_price = 1.0 - settle_price
            old_complement_price = prev["settle_price"]  # was 0.5
            prev_size = prev["size"]

            if prev_size > 0:
                adjustment = (correct_complement_price - old_complement_price) * prev_size
            else:
                adjustment = (old_complement_price - correct_complement_price) * abs(prev_size)

            if abs(adjustment) > 1e-9:
                self.total_realized_pnl += adjustment
                logger.info(
                    "retroactive_complement_adjustment",
                    market_id=complement_id[:12],
                    old_price=old_complement_price,
                    correct_price=correct_complement_price,
                    adjustment=round(adjustment, 6),
                )

        # ── Normal settlement ──
        if pos.size > 0:
            pnl = (settle_price - pos.avg_price) * pos.size
        else:
            pnl = (pos.avg_price - settle_price) * abs(pos.size)
            
        pos.realized_pnl += pnl
        self.total_realized_pnl += pnl
        
        if hasattr(self, 'forensic') and self.forensic:
            self.forensic.log_position_settlement(
                market_id=market_id,
                size=pos.size,
                avg_price=pos.avg_price,
                settle_price=settle_price,
                realized_pnl=pnl,
            )
            
        self.resolved_positions.append({
            "market_id": market_id,
            "size": pos.size,
            "avg_price": pos.avg_price,
            "settle_price": settle_price,
            "pnl": pnl,
            "total_realized_pnl": pos.realized_pnl,
            "settled_at": current_timestamp_ms()
        })
        # deque(maxlen=100) handles eviction automatically — no manual pop needed

        # If this was a standalone settlement (no complement resolved simultaneously),
        # record it for potential retroactive adjustment later.
        if complement_id and settle_price == 0.5:
            self._pending_complement_adjustments[market_id] = {
                "size": pos.size,
                "settle_price": settle_price,
            }

        # Zero out the position after settlement
        pos.size = 0.0
        pos.avg_price = 0.0
        del self.positions[market_id]
