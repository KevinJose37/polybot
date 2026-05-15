"""
Simulated fill logic with depth-weighted VWAP and minimum slippage floor.
"""

from bot.orderbook.local_book import LocalOrderBook
from bot.paper_trading.slippage import apply_slippage


def simulate_fill(order_size: float, book: LocalOrderBook, side: str, slippage_pct: float = 0.005) -> tuple[bool, float, float]:
    """
    Simulate a fill by walking the L2 orderbook to compute depth-weighted VWAP.
    Applies a minimum slippage floor — if VWAP shows zero slippage (single level
    fill at best price), the slippage model provides a conservative minimum.
    
    Returns (is_filled, filled_size, vwap_price).
    """
    if book.is_stale():
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
            
        take_size = min(remaining, size)
        total_cost += take_size * price
        filled_size += take_size
        remaining -= take_size

    if filled_size == 0:
        return False, 0.0, 0.0

    vwap = total_cost / filled_size
    
    # Apply minimum slippage floor: if VWAP equals best price (single-level fill),
    # use the slippage model to add conservative minimum market impact.
    if levels:
        best_price = levels[0][0]
        if abs(vwap - best_price) < 1e-9:
            vwap = apply_slippage(vwap, side, slippage_pct)
    
    return True, filled_size, vwap
