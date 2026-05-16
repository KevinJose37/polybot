"""
Tests for math utilities.
"""
from bot.utils.math import (
    calculate_kelly_fraction,
    calculate_fractional_kelly,
    calculate_order_size,
    polymarket_taker_fee,
    fee_per_share,
    net_cost_buy,
    net_revenue_sell,
)


def test_calculate_kelly_fraction() -> None:
    """Test kelly fraction calculation."""
    # p = 1.0, b = 0.0526315 -> f = 1.0
    kelly = calculate_kelly_fraction(1.0, 0.0526315)
    assert kelly == 1.0
    
    # b = 0 -> 0
    assert calculate_kelly_fraction(1.0, 0.0) == 0.0
    
    # p = 0 -> 0
    assert calculate_kelly_fraction(0.0, 1.0) == 0.0


def test_kelly_p_zero_explicit() -> None:
    """Test Kelly fraction is explicitly 0 when probability is 0."""
    assert calculate_kelly_fraction(0.0, 1.0) == 0.0
    assert calculate_kelly_fraction(0.0, 100.0) == 0.0


def test_kelly_zero_edge() -> None:
    """p=0.5, b=1.0 should yield f*=0 (no edge = no bet)."""
    # f = (0.5*1.0 - 0.5) / 1.0 = 0.0
    kelly = calculate_kelly_fraction(0.5, 1.0)
    assert kelly == 0.0


def test_kelly_near_one() -> None:
    """p near 1 should not overflow."""
    kelly = calculate_kelly_fraction(0.99, 0.01)
    assert 0.0 <= kelly <= 1.0


def test_calculate_fractional_kelly() -> None:
    """Test fractional kelly calculation."""
    frac = calculate_fractional_kelly(1.0, 0.0526315, 0.25)
    assert frac == 0.25


def test_calculate_order_size() -> None:
    """Test order size calculation."""
    # p=1.0, b=0.0526315, capital=1000, max_size=50
    # fractional_kelly = 0.25
    # size = 1000 * 0.25 = 250, capped at 50 -> 50
    size = calculate_order_size(1.0, 0.0526315, 1000.0, 50.0, 0.25)
    assert size == 50.0
    
    # size_capped = 10.0
    size_capped = calculate_order_size(1.0, 0.0526315, 1000.0, 10.0, 0.25)
    assert size_capped == 10.0


def test_polymarket_taker_fee() -> None:
    """Hand-verify Polymarket fee formula: fee = C × p × feeRate × (p × (1-p))^1."""
    # price=0.50, size=100, fee_rate=0.03
    # fee = 100 × 0.50 × 0.03 × (0.50 × 0.50)^1 = 100 × 0.50 × 0.03 × 0.25 = 0.375
    fee_50 = polymarket_taker_fee(0.50, 100.0, 0.03)
    assert abs(fee_50 - 0.375) < 0.0001

    # price=0.62, size=100, fee_rate=0.03
    # fee = 100 × 0.62 × 0.03 × (0.62 × 0.38) = 100 × 0.62 × 0.03 × 0.2356 = 0.4382
    fee_62 = polymarket_taker_fee(0.62, 100.0, 0.03)
    assert abs(fee_62 - 0.4382) < 0.001

    # price=0.01 (near boundary): fee = 100 × 0.01 × 0.03 × (0.01 × 0.99) = 0.000297 → rounds to 0.0003
    fee_01 = polymarket_taker_fee(0.01, 100.0, 0.03)
    assert abs(fee_01 - 0.0003) < 0.0001

    # Edge cases: price=0 or size=0 -> 0
    assert polymarket_taker_fee(0.0, 100.0, 0.03) == 0.0
    assert polymarket_taker_fee(0.50, 0.0, 0.03) == 0.0
    assert polymarket_taker_fee(1.0, 100.0, 0.03) == 0.0


def test_polymarket_taker_fee_sell_is_free() -> None:
    """Sell orders should not incur taker fees."""
    assert polymarket_taker_fee(0.50, 100.0, 0.03, side="SELL") == 0.0
    assert polymarket_taker_fee(0.62, 100.0, 0.03, side="SELL") == 0.0


def test_fee_per_share() -> None:
    """Per-share fee component: p × feeRate × (p × (1-p))."""
    # price=0.50, fee_rate=0.03 -> 0.50 × 0.03 × (0.50 × 0.50) = 0.00375
    assert abs(fee_per_share(0.50, 0.03) - 0.00375) < 0.0001
    # price=0.40, fee_rate=0.03 -> 0.40 × 0.03 × (0.40 × 0.60) = 0.40 × 0.03 × 0.24 = 0.00288
    assert abs(fee_per_share(0.40, 0.03) - 0.00288) < 0.0001
    # SELL should return 0
    assert fee_per_share(0.50, 0.03, side="SELL") == 0.0


def test_net_cost_buy() -> None:
    """Buy cost = price*size + fee."""
    # price=0.50, size=100 -> cost = 50.0 + 0.375 = 50.375
    cost = net_cost_buy(0.50, 100.0, 0.03)
    assert abs(cost - 50.375) < 0.001


def test_net_revenue_sell() -> None:
    """Sell revenue = price*size (no taker fee on sells)."""
    # price=0.62, size=100 -> revenue = 62.0 (fee-free)
    rev = net_revenue_sell(0.62, 100.0, 0.03)
    assert abs(rev - 62.0) < 0.0001
