"""
Type B - Cross-timeframe monotonicity.
5-minute probability must be closer to 0.5 than 15-minute probability for the
same asset/direction (higher uncertainty at shorter timeframe). Violation signals arb.
edge = (bid_5m - sell_fee - slippage) - (ask_15m + buy_fee + slippage)
where fee_per_share = fee_rate × min(price, 1 - price)
"""
import structlog
import hashlib
import math
from typing import Optional

from bot.arbitrage.opportunity import ArbOpportunity, ArbType, ArbLeg
from bot.utils.math import calculate_order_size, fee_per_share

logger = structlog.get_logger(__name__)


def detect_monotonicity(
    market_5m_id: str,
    market_15m_id: str,
    token_yes_5m: str,
    token_yes_15m: str,
    bid_5m: float,
    ask_15m: float,
    vol_5m: float,
    vol_15m: float,
    fee_rate_5m: float,
    fee_rate_15m: float,
    slippage: float,
    min_edge: float,
    min_notional: float,
    capital: float
) -> Optional[ArbOpportunity]:
    """
    Pure function to detect cross-timeframe monotonicity arbitrage.
    Compares the YES token (UP direction) across 5m and 15m markets.
    Uses Polymarket's additive fee model: fee_rate × min(price, 1-price).
    """
    if bid_5m is None or ask_15m is None or math.isnan(bid_5m) or math.isnan(ask_15m):
        return None
        
    # 5m leg is SELL — no taker fee on sells
    sell_fee = fee_per_share(bid_5m, fee_rate_5m, side="SELL")  # returns 0
    # 15m leg is BUY — pays taker fee
    buy_fee = fee_per_share(ask_15m, fee_rate_15m, side="BUY")
    receive = bid_5m - sell_fee - slippage
    pay = ask_15m + buy_fee + slippage
    edge = receive - pay

    if edge > min_edge:
        max_size = min(vol_5m, vol_15m)
        
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
            
        opp_id = hashlib.sha256(f"B_{market_5m_id}_{market_15m_id}_{bid_5m:.6f}_{ask_15m:.6f}".encode()).hexdigest()[:16]
        
        return ArbOpportunity(
            opportunity_id=opp_id,
            type=ArbType.MONOTONICITY,
            edge=edge,
            size=order_size,
            legs=[
                # Sell 5m
                ArbLeg(market_id=token_yes_5m, side="SELL", price=bid_5m, size=order_size),
                # Buy 15m
                ArbLeg(market_id=token_yes_15m, side="BUY", price=ask_15m, size=order_size)
            ],
            timestamp_ms=0
        )
        
    return None
