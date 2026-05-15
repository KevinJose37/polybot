"""
Slippage modeling.

Used in two contexts:
1. Signal-time: conservative flat slippage estimate in detector edge calculations
   (via config slippage_est additive term).
2. Fill-time: minimum slippage floor in simulate_fill() when VWAP equals best price
   (single-level fill with zero natural slippage).
"""


def apply_slippage(price: float, side: str, slippage_pct: float = 0.005) -> float:
    """
    Calculate the actual fill price including slippage.
    BUY side pays more. SELL side receives less.

    Args:
        price: The base fill price.
        side: "BUY" or "SELL".
        slippage_pct: Slippage as a fraction (default 0.5%).

    Returns:
        Adjusted price with slippage applied, clamped to [0.0, 1.0].
    """
    if side == "BUY":
        return min(1.0, price * (1 + slippage_pct))
    else:
        return max(0.0, price * (1 - slippage_pct))
