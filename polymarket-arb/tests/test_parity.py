"""
Tests for Type A - YES/NO Parity.
Uses the real Polymarket fee formula. BUY orders pay taker fees.
"""
from bot.arbitrage.parity import detect_parity


def test_parity_opportunity() -> None:
    """Verify parity detector finds arb with Polymarket fee model."""
    opp = detect_parity(
        market_id="m1",
        token_yes_id="yes1",
        token_no_id="no1",
        yes_ask=0.40,
        no_ask=0.50,
        yes_vol=1000.0,
        no_vol=500.0,
        yes_fee_rate=0.03,
        no_fee_rate=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    
    assert opp is not None
    assert opp.type.value == "TYPE-A"
    # yes_fee = 0.40 × 0.03 × (0.40 × 0.60) = 0.40 × 0.03 × 0.24 = 0.00288
    # no_fee  = 0.50 × 0.03 × (0.50 × 0.50) = 0.50 × 0.03 × 0.25 = 0.00375
    # yes_cost = 0.40 + 0.00288 + 0.005 = 0.40788
    # no_cost  = 0.50 + 0.00375 + 0.005 = 0.50875
    # edge = 1.0 - 0.40788 - 0.50875 = 0.08337
    assert abs(opp.edge - 0.08337) < 0.001
    assert len(opp.legs) == 2
    assert opp.legs[0].side == "BUY"
    assert opp.legs[1].side == "BUY"
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
        yes_fee_rate=0.03,
        no_fee_rate=0.03,
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
        yes_fee_rate=0.03,
        no_fee_rate=0.03,
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
        yes_fee_rate=0.03,
        no_fee_rate=0.03,
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
        yes_fee_rate=0.03,
        no_fee_rate=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=100.0,  # Very high threshold
        capital=10.0          # Very low capital
    )
    assert opp is None
