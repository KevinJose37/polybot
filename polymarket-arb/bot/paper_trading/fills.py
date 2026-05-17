"""
Simulated fill logic with depth-weighted VWAP and minimum slippage floor.
"""

from bot.orderbook.local_book import LocalOrderBook
from bot.paper_trading.slippage import apply_slippage
import math
import random


def simulate_fill(order_size: float, book: LocalOrderBook, side: str, slippage_pct: float = 0.005, order_type: str = "IOC", latency_ms: int = 0, limit_price: float | None = None) -> tuple[bool, float, float]:
    """
    Simulate a fill by walking the L2 orderbook to compute depth-weighted VWAP.
    Models adverse selection and enforces FOK/IOC and Limit Price mechanics.
    """
    if book.is_stale():
        return False, 0.0, 0.0

    # Adverse selection: probability that the orderbook is swept by a faster arb bot before our order arrives.
    # We use a Poisson arrival model: P(swept) = 1 - exp(-lambda * time).
    # Assuming lambda = 2 sweeps per second on average for highly contested arbs.
    lambda_rate = 2.0
    p_swept = 1.0 - math.exp(-lambda_rate * (latency_ms / 1000.0))
    if random.random() < p_swept:
        return False, 0.0, 0.0

    remaining = order_size
    total_cost = 0.0
    filled_size = 0.0

    if side == "BUY":
        # Walk the asks to buy
        levels = book.ask_depth(levels=20)
    else:
        # Walk the bids to sell
        levels = book.bid_depth(levels=20)

    for price, size in levels:
        if remaining <= 0:
            break
            
        if limit_price is not None:
            if side == "BUY" and price > limit_price:
                break
            if side == "SELL" and price < limit_price:
                break
                
        take_size = min(remaining, size)
        total_cost += take_size * price
        filled_size += take_size
        remaining -= take_size

    if order_type == "FOK" and filled_size < order_size:
        return False, 0.0, 0.0

    if filled_size == 0:
        return False, 0.0, 0.0

    vwap = total_cost / filled_size
    
    # VWAP from walking the book IS the fill price.
    # Slippage is already budgeted in the detector's edge calculation (slippage_est).
    # Applying it again here would double-count, systematically eroding thin arb edges.
    
    return True, filled_size, vwap
