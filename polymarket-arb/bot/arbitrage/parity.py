"""
Type A - YES/NO Parity detector.
For each binary market, YES and NO are complementary.
edge = 1.0 - (yes_ask + yes_fee + slippage) - (no_ask + no_fee + slippage)
where fee_per_share = p × feeRate × (p × (1 - p))^exponent
Note: This detector is subsumed by Type C in production.
"""
import structlog
import hashlib
import math
from typing import Optional

from bot.arbitrage.opportunity import ArbOpportunity, ArbType, ArbLeg
from bot.utils.math import calculate_order_size, fee_per_share

logger = structlog.get_logger(__name__)


def detect_parity(
    market_id: str,
    token_yes_id: str,
    token_no_id: str,
    yes_ask: float,
    no_ask: float,
    yes_vol: float,
    no_vol: float,
    yes_fee_rate: float,
    no_fee_rate: float,
    slippage: float,
    min_edge: float,
    min_notional: float,
    capital: float
) -> Optional[ArbOpportunity]:
    """
    Pure function to detect YES/NO parity arbitrage.
    Uses Polymarket's fee model: fee = p × feeRate × (p × (1-p)).
    BUY orders pay taker fees; SELL orders are fee-free.
    """
    if yes_ask is None or no_ask is None or math.isnan(yes_ask) or math.isnan(no_ask):
        return None
        
    # Both legs are BUY (pays taker fee)
    yes_fee = fee_per_share(yes_ask, yes_fee_rate, side="BUY")
    no_fee = fee_per_share(no_ask, no_fee_rate, side="BUY")
    yes_cost = yes_ask + yes_fee + slippage
    no_cost = no_ask + no_fee + slippage
    edge = 1.0 - (yes_cost + no_cost)

    if edge > min_edge:
        max_size = min(yes_vol, no_vol)
        
        p = 1.0
        cost = 1.0 - edge
        b = edge / cost if cost > 0 else 0.0
        
        order_size = calculate_order_size(
            p=p,
            b=b,
            capital=capital,
            max_size=max_size,
            multiplier=0.25
        )
        
        if order_size < min_notional:
            return None
            
        opp_id = hashlib.sha256(f"A_{market_id}_{yes_ask:.6f}_{no_ask:.6f}".encode()).hexdigest()[:16]
        
        return ArbOpportunity(
            opportunity_id=opp_id,
            type=ArbType.PARITY,
            edge=edge,
            size=order_size,
            legs=[
                ArbLeg(market_id=token_yes_id, side="BUY", price=yes_ask, size=order_size),
                ArbLeg(market_id=token_no_id, side="BUY", price=no_ask, size=order_size)
            ],
            timestamp_ms=0
        )
        
    return None
