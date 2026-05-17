"""
Type B - Cross-timeframe monotonicity.
5-minute probability must be closer to 0.5 than 15-minute probability for the
same asset/direction (higher uncertainty at shorter timeframe). Violation signals arb.
To avoid naked shorting constraints on the 5m YES token, we synthetically short
it by buying the 5m NO token.
edge = 1.0 - (ask_5m_no + buy_fee + slippage) - (ask_15m_yes + buy_fee + slippage) - gas_fee_est
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
    token_no_5m: str,
    token_yes_15m: str,
    ask_5m_no: float,
    ask_15m_yes: float,
    vol_5m_no: float,
    vol_15m_yes: float,
    fee_rate_5m: float,
    fee_rate_15m: float,
    slippage: float,
    min_edge: float,
    min_notional: float,
    capital: float,
    multiplier: float,
    gas_fee_est: float = 0.0
) -> Optional[ArbOpportunity]:
    """
    Pure function to detect cross-timeframe monotonicity arbitrage.
    Compares the NO token on 5m and YES token on 15m markets.
    Uses Polymarket's additive fee model: fee_rate × min(price, 1-price).
    """
    if ask_5m_no is None or ask_15m_yes is None or math.isnan(ask_5m_no) or math.isnan(ask_15m_yes):
        return None
        
    # Both legs are BUY — pay taker fee
    buy_fee_5m = fee_per_share(ask_5m_no, fee_rate_5m, side="BUY")
    buy_fee_15m = fee_per_share(ask_15m_yes, fee_rate_15m, side="BUY")
    
    cost = ask_5m_no + buy_fee_5m + slippage + ask_15m_yes + buy_fee_15m + slippage + gas_fee_est
    edge = 1.0 - cost

    if edge > min_edge:
        max_size = min(vol_5m_no, vol_15m_yes)
        if max_size <= 0:
            return None
        
        # Unlike parity arb (guaranteed $1.00 payout), monotonicity trades
        # depend on actual settlement outcomes across different timeframes.
        # Using p=0.80 as a conservative discount for settlement uncertainty.
        p = 0.80
        b = edge / cost if cost > 0 else 0.0
        
        avg_price = (ask_5m_no + ask_15m_yes) / 2.0
        order_size = calculate_order_size(
            p=p,
            b=b,
            capital=capital,
            max_size=max_size,
            multiplier=multiplier,
            avg_price=avg_price
        )
        
        if order_size < min_notional:
            return None
            
        opp_id = hashlib.sha256(f"B_{market_5m_id}_{market_15m_id}_{ask_5m_no:.6f}_{ask_15m_yes:.6f}".encode()).hexdigest()[:16]
        
        return ArbOpportunity(
            opportunity_id=opp_id,
            type=ArbType.MONOTONICITY,
            edge=edge,
            size=order_size,
            legs=[
                # Buy 5m NO
                ArbLeg(market_id=token_no_5m, side="BUY", price=ask_5m_no, size=order_size),
                # Buy 15m YES
                ArbLeg(market_id=token_yes_15m, side="BUY", price=ask_15m_yes, size=order_size)
            ],
            timestamp_ms=0
        )
        
    return None

