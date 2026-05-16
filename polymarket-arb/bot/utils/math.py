"""
Math utilities for edge calculation and sizing.

Polymarket fee formula (from docs, as of March 2026):
    fee = C × p × feeRate × (p × (1 − p))^exponent
where:
    C = number of shares traded
    p = trade price
    feeRate = rate returned by /fee-rate endpoint (e.g. 0.03)
    exponent = 1

Key rules:
    - Sell (taker) orders are NOT subject to taker fees.
    - Fees are rounded to 4 decimal places; minimum fee is 0.0001 pUSD.
    - Geopolitical & World Events markets are fee-free.
    - Peak effective fee is ~1.80% at the 50/50 price point (when feeRate=0.03).
"""


def polymarket_taker_fee(price: float, size: float, fee_rate: float, side: str = "BUY") -> float:
    """
    Polymarket taker fee per the documented formula:
    fee = C × p × feeRate × (p × (1 - p))^exponent

    Sell orders are NOT subject to taker fees and return 0.
    Result is rounded to 4 decimal places with a minimum of 0.0001.
    """
    if side == "SELL":
        return 0.0
    if price <= 0.0 or price >= 1.0 or size <= 0.0 or fee_rate <= 0.0:
        return 0.0
    raw_fee = size * price * fee_rate * (price * (1.0 - price))
    if raw_fee < 0.0001:
        return 0.0
    return round(raw_fee, 4)


def fee_per_share(price: float, fee_rate: float, side: str = "BUY") -> float:
    """Per-share fee component for edge calculations.
    
    fee_per_share = p × feeRate × (p × (1 - p))^exponent
    Sell orders return 0 (no taker fee on sells).
    """
    if side == "SELL":
        return 0.0
    if price <= 0.0 or price >= 1.0 or fee_rate <= 0.0:
        return 0.0
    return price * fee_rate * (price * (1.0 - price))


def net_cost_buy(price: float, size: float, fee_rate: float) -> float:
    """Total cost to BUY: (price × size) + fee."""
    return price * size + polymarket_taker_fee(price, size, fee_rate, side="BUY")


def net_revenue_sell(price: float, size: float, fee_rate: float) -> float:
    """Total revenue from SELL: (price × size) — no taker fee on sells."""
    return price * size  # Sell orders are fee-free


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
