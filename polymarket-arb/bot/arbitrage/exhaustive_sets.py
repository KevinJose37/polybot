"""
Type C - Exhaustive set parity.
For a given asset x timeframe, P(up) + P(down) = 1.0 exactly.
Execution-price deviation beyond epsilon signals a tradeable imbalance.
edge = 1.0 - (up_ask + up_fee + slippage) - (down_ask + down_fee + slippage)  [BUY side]
edge = (up_bid - up_fee - slippage) + (down_bid - down_fee - slippage) - 1.0  [SELL side]
where fee_per_share = fee_rate × min(price, 1 - price)
"""
import structlog
import hashlib
import math
from typing import Optional

from bot.arbitrage.opportunity import ArbOpportunity, ArbType, ArbLeg
from bot.utils.math import calculate_order_size, fee_per_share

logger = structlog.get_logger(__name__)


def detect_exhaustive_parity(
    market_id: str,
    token_up_id: str,
    token_down_id: str,
    up_bid: float,
    up_ask: float,
    down_bid: float,
    down_ask: float,
    up_ask_vol: float,
    down_ask_vol: float,
    up_bid_vol: float,
    down_bid_vol: float,
    inventory_up: float,
    inventory_down: float,
    up_fee_rate: float,
    down_fee_rate: float,
    slippage: float,
    min_edge: float,
    min_notional: float,
    capital: float,
    multiplier: float,
    gas_fee_est: float = 0.0
) -> Optional[ArbOpportunity]:
    """
    Pure function to detect exhaustive set parity based on execution prices.
    Uses Polymarket's additive fee model: fee_rate × min(price, 1-price).
    """
    if (up_bid is None or up_ask is None or down_bid is None or down_ask is None or
        math.isnan(up_bid) or math.isnan(up_ask) or math.isnan(down_bid) or math.isnan(down_ask)):
        return None
        
    # Check BUY parity (sum of asks + fees < 1.0)
    # BUY side pays taker fees
    up_fee_buy = fee_per_share(up_ask, up_fee_rate, side="BUY")
    down_fee_buy = fee_per_share(down_ask, down_fee_rate, side="BUY")
    up_cost = up_ask + up_fee_buy + slippage
    down_cost = down_ask + down_fee_buy + slippage
    buy_cost = up_cost + down_cost + gas_fee_est
    buy_edge = 1.0 - buy_cost
    
    # Check SELL parity (sum of bids - fees > 1.0)
    # SELL side has no taker fees
    up_fee_sell = fee_per_share(up_bid, up_fee_rate, side="SELL")  # returns 0
    down_fee_sell = fee_per_share(down_bid, down_fee_rate, side="SELL")  # returns 0
    up_receive = up_bid - up_fee_sell - slippage
    down_receive = down_bid - down_fee_sell - slippage
    sell_rev = up_receive + down_receive - gas_fee_est
    sell_edge = sell_rev - 1.0
    
    is_buy = buy_edge > sell_edge
    edge = max(buy_edge, sell_edge)

    if edge > min_edge:
        if is_buy:
            up_price = up_ask
            down_price = down_ask
            max_size = min(up_ask_vol, down_ask_vol)
        else:
            up_price = up_bid
            down_price = down_bid
            max_size = min(up_bid_vol, down_bid_vol, inventory_up, inventory_down)
            
        if max_size <= 0:
            return None
            
        p = 1.0
        cost = 1.0 - edge
        b = edge / cost if cost > 0 else 0.0
            
        avg_price = (up_price + down_price) / 2.0
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
            
        opp_id = hashlib.sha256(f"C_{market_id}_{'BUY' if is_buy else 'SELL'}_{edge:.6f}".encode()).hexdigest()[:16]
        
        return ArbOpportunity(
            opportunity_id=opp_id,
            type=ArbType.EXHAUSTIVE,
            edge=edge,
            size=order_size,
            legs=[
                ArbLeg(
                    market_id=token_up_id, 
                    side="BUY" if is_buy else "SELL", 
                    price=up_price,
                    size=order_size
                ),
                ArbLeg(
                    market_id=token_down_id, 
                    side="BUY" if is_buy else "SELL", 
                    price=down_price,
                    size=order_size
                )
            ],
            timestamp_ms=0
        )
        
    return None
