"""
Tests for Type A - YES/NO Parity.
"""
from bot.arbitrage.parity import detect_parity


def test_parity_opportunity() -> None:
    """Verify parity detector finds arb with the additive fee model."""
    opp = detect_parity(
        market_id="m1",
        token_yes_id="yes1",
        token_no_id="no1",
        yes_ask=0.40,
        no_ask=0.50,
        yes_vol=1000.0,
        no_vol=500.0,
        fee=0.02,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    
    assert opp is not None
    assert opp.type.value == "TYPE-A"
    # edge = 1.0 - (0.40 + 0.008 + 0.005) - (0.50 + 0.01 + 0.005) = 0.072
    assert abs(opp.edge - 0.072) < 0.0001
    assert len(opp.legs) == 2
    assert opp.legs[0].side == "BUY"
    assert opp.legs[1].side == "BUY"
    # Legs carry raw exchange prices, not all-in cost
    assert opp.legs[0].price == 0.40
    assert opp.legs[1].price == 0.50


def test_parity_no_edge() -> None:
    """Near-equal legs should produce no opportunity after fees."""
    opp = detect_parity(
        market_id="m1",
        token_yes_id="yes1",
        token_no_id="no1",
        yes_ask=0.49,
        no_ask=0.49,
        yes_vol=1000.0,
        no_vol=500.0,
        fee=0.02,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    assert opp is None


def test_parity_sum_gt_1() -> None:
    """Both legs at 0.99 should produce no opportunity (negative edge)."""
    opp = detect_parity(
        market_id="m1",
        token_yes_id="yes1",
        token_no_id="no1",
        yes_ask=0.99,
        no_ask=0.99,
        yes_vol=1000.0,
        no_vol=1000.0,
        fee=0.02,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    assert opp is None


def test_parity_zero_volume() -> None:
    """Zero volume should produce no opportunity (order size < min_notional)."""
    opp = detect_parity(
        market_id="m1",
        token_yes_id="yes1",
        token_no_id="no1",
        yes_ask=0.40,
        no_ask=0.50,
        yes_vol=0.0,
        no_vol=0.0,
        fee=0.02,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    assert opp is None


def test_parity_below_min_notional() -> None:
    """Tiny capital should produce no opportunity if size < min_notional."""
    opp = detect_parity(
        market_id="m1",
        token_yes_id="yes1",
        token_no_id="no1",
        yes_ask=0.40,
        no_ask=0.50,
        yes_vol=1000.0,
        no_vol=1000.0,
        fee=0.02,
        slippage=0.005,
        min_edge=0.01,
        min_notional=100.0,  # Very high threshold
        capital=10.0          # Very low capital
    )
    assert opp is None
