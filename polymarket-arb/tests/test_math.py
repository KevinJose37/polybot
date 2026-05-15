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
    """Hand-verify Polymarket fee formula."""
    # price=0.62, size=100, fee_rate=0.02
    # fee = 0.02 * min(0.62, 0.38) * 100 = 0.02 * 0.38 * 100 = 0.76
    fee = polymarket_taker_fee(0.62, 100.0, 0.02)
    assert abs(fee - 0.76) < 0.0001
    
    # price=0.50 (max fee point): 0.02 * 0.50 * 100 = 1.00
    fee_50 = polymarket_taker_fee(0.50, 100.0, 0.02)
    assert abs(fee_50 - 1.0) < 0.0001
    
    # price=0.01 (near boundary): 0.02 * 0.01 * 100 = 0.02
    fee_01 = polymarket_taker_fee(0.01, 100.0, 0.02)
    assert abs(fee_01 - 0.02) < 0.0001

    # Edge cases: price=0 or size=0 -> 0
    assert polymarket_taker_fee(0.0, 100.0, 0.02) == 0.0
    assert polymarket_taker_fee(0.50, 0.0, 0.02) == 0.0
    assert polymarket_taker_fee(1.0, 100.0, 0.02) == 0.0


def test_fee_per_share() -> None:
    """Per-share fee component."""
    # price=0.40, fee_rate=0.02 -> 0.02 * min(0.40, 0.60) = 0.008
    assert abs(fee_per_share(0.40, 0.02) - 0.008) < 0.0001
    # price=0.50 -> 0.02 * 0.50 = 0.01
    assert abs(fee_per_share(0.50, 0.02) - 0.01) < 0.0001


def test_net_cost_buy() -> None:
    """Buy cost = price*size + fee."""
    # price=0.62, size=100 -> cost = 62.0 + 0.76 = 62.76
    cost = net_cost_buy(0.62, 100.0, 0.02)
    assert abs(cost - 62.76) < 0.0001


def test_net_revenue_sell() -> None:
    """Sell revenue = price*size - fee."""
    # price=0.62, size=100 -> revenue = 62.0 - 0.76 = 61.24
    rev = net_revenue_sell(0.62, 100.0, 0.02)
    assert abs(rev - 61.24) < 0.0001
