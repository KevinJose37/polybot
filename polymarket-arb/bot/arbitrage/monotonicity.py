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
    
    # Proper 3-outcome Kelly modeling for Inverted-V paths
    # We blend theoretical assumptions with market realities to avoid overconfidence.
    # Theoretical: 5m NO should be 1.0 - 15m YES
    confidence = 0.5
    p_A_theo = 1.0 - ask_15m_yes
    p_A_market = ask_5m_no
    p_A = confidence * p_A_theo + (1.0 - confidence) * p_A_market
    p_B = ask_15m_yes
    
    expected_payout = p_A + p_B
    edge = expected_payout - cost

    if edge > min_edge:
        max_size = min(vol_5m_no, vol_15m_yes)
        if max_size <= 0:
            return None
        
        # Maximize extreme outcomes (Inverted-V) to calculate a conservative variance
        p_0 = min(1.0 - p_A, 1.0 - p_B)
        p_2 = p_0 + p_A + p_B - 1.0
        p_1 = p_A + p_B - 2.0 * p_2
        
        # Ensure floating point stability
        p_0 = max(0.0, p_0)
        p_2 = max(0.0, p_2)
        p_1 = max(0.0, p_1)
        
        mu = edge / cost if cost > 0 else 0.0
        if mu <= 0:
            return None
            
        R_2 = (2.0 - cost) / cost
        R_1 = (1.0 - cost) / cost
        R_0 = -1.0
        
        expected_r2 = p_2 * (R_2 ** 2) + p_1 * (R_1 ** 2) + p_0 * (R_0 ** 2)
        variance = expected_r2 - (mu ** 2)
        
        if variance <= 0:
            return None
            
        # Continuous Kelly fraction
        kelly_fraction = mu / variance
        fractional_kelly = kelly_fraction * multiplier
        
        kelly_size = fractional_kelly * capital
        avg_price = (ask_5m_no + ask_15m_yes) / 2.0
        max_notional = max_size * avg_price
        
        order_size = min(max_notional, kelly_size)
        
        if order_size < min_notional:
            return None
            
        opp_id = hashlib.sha256(f"B_{market_5m_id}_{market_15m_id}_{ask_5m_no:.6f}_{ask_15m_yes:.6f}".encode()).hexdigest()[:16]
        
        opp = ArbOpportunity(
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
            timestamp_ms=0,
            metadata={
                "p_A": p_A,
                "p_B": p_B,
                "p_0": p_0,
                "expected_payout": expected_payout,
                "variance": variance,
                "kelly_fraction": kelly_fraction
            }
        )
        return opp
        
    return None

