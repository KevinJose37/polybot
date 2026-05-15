"""
Tests for Type B - Monotonicity.
"""
from bot.arbitrage.monotonicity import detect_monotonicity


def test_monotonicity_opportunity() -> None:
    """Verify monotonicity detector finds arb with the additive fee model."""
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_yes_5m="yes5",
        token_yes_15m="yes15",
        bid_5m=0.60,
        ask_15m=0.50,
        vol_5m=1000.0,
        vol_15m=1000.0,
        fee=0.02,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    
    assert opp is not None
    assert opp.type.value == "TYPE-B"
    assert abs(opp.edge - 0.072) < 0.0001
    assert len(opp.legs) == 2
    assert opp.legs[0].side == "SELL"
    assert opp.legs[0].price == 0.60
    assert opp.legs[1].side == "BUY"
    assert opp.legs[1].price == 0.50


def test_monotonicity_no_edge() -> None:
    """Tight spread should produce no opportunity after fees."""
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_yes_5m="yes5",
        token_yes_15m="yes15",
        bid_5m=0.50,
        ask_15m=0.51,
        vol_5m=1000.0,
        vol_15m=1000.0,
        fee=0.02,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    assert opp is None


def test_monotonicity_inverted() -> None:
    """5m bid < 15m ask should produce no opportunity."""
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_yes_5m="yes5",
        token_yes_15m="yes15",
        bid_5m=0.45,
        ask_15m=0.50,
        vol_5m=1000.0,
        vol_15m=1000.0,
        fee=0.02,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    assert opp is None
