"""
Tests for Type B - Monotonicity.
Uses the real Polymarket fee formula. Sell orders are fee-free.
"""
from bot.arbitrage.monotonicity import detect_monotonicity


def test_monotonicity_opportunity() -> None:
    """Verify monotonicity detector finds arb with Polymarket fee model."""
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_yes_5m="yes5",
        token_yes_15m="yes15",
        bid_5m=0.60,
        ask_15m=0.50,
        vol_5m=1000.0,
        vol_15m=1000.0,
        fee_rate_5m=0.03,
        fee_rate_15m=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    
    assert opp is not None
    assert opp.type.value == "TYPE-B"
    assert len(opp.legs) == 2
    assert opp.legs[0].side == "SELL"
    assert opp.legs[0].price == 0.60
    assert opp.legs[1].side == "BUY"
    assert opp.legs[1].price == 0.50
    # 5m SELL is fee-free, 15m BUY pays fee
    # edge = (0.60 - 0 - 0.005) - (0.50 + fee_per_share(0.50, 0.03) + 0.005)
    # fee_per_share = 0.50 × 0.03 × (0.50 × 0.50) = 0.00375
    # edge = 0.595 - 0.50875 = 0.08625
    assert abs(opp.edge - 0.08625) < 0.001


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
        fee_rate_5m=0.03,
        fee_rate_15m=0.03,
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
        fee_rate_5m=0.03,
        fee_rate_15m=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0
    )
    assert opp is None
