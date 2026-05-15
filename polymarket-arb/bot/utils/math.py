"""
Math utilities for edge calculation and sizing.
"""


def polymarket_taker_fee(price: float, size: float, fee_rate: float) -> float:
    """
    Polymarket taker fee per the documented formula:
    fee = fee_rate × min(price, 1 - price) × size

    This means fees are lower for prices near 0 or 1, and highest at price = 0.5.
    """
    if price <= 0.0 or price >= 1.0 or size <= 0.0 or fee_rate <= 0.0:
        return 0.0
    return fee_rate * min(price, 1.0 - price) * size


def fee_per_share(price: float, fee_rate: float) -> float:
    """Per-share fee component: fee_rate × min(price, 1 - price)."""
    if price <= 0.0 or price >= 1.0 or fee_rate <= 0.0:
        return 0.0
    return fee_rate * min(price, 1.0 - price)


def net_cost_buy(price: float, size: float, fee_rate: float) -> float:
    """Total cost to BUY: (price × size) + fee."""
    return price * size + polymarket_taker_fee(price, size, fee_rate)


def net_revenue_sell(price: float, size: float, fee_rate: float) -> float:
    """Total revenue from SELL: (price × size) - fee."""
    return price * size - polymarket_taker_fee(price, size, fee_rate)


def calculate_kelly_fraction(p: float, b: float) -> float:
    """
    Calculate the Kelly fraction given win probability p and net odds b.
    kelly_fraction = (p * b - q) / b
    """
    if p <= 0.0 or b <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
        
    q = 1.0 - p
    f = (p * b - q) / b
    return max(0.0, f)


def calculate_fractional_kelly(p: float, b: float, multiplier: float = 0.25) -> float:
    """
    Calculate the fractional Kelly sizing.
    fractional_kelly = kelly_fraction * multiplier
    """
    return calculate_kelly_fraction(p, b) * multiplier


def calculate_order_size(
    p: float,
    b: float, 
    capital: float, 
    max_size: float, 
    multiplier: float = 0.25
) -> float:
    """
    Calculate the order size in USD using fractional Kelly, bounded by max_size.
    order_size = min(max_size, fractional_kelly * capital)
    """
    fractional_kelly = calculate_fractional_kelly(p, b, multiplier)
    return min(max_size, fractional_kelly * capital)
