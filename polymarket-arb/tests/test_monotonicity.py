"""
Tests for Type B - Monotonicity.
Uses the real Polymarket fee formula. 
Type-B uses p=0.80 (conservative) since settlement is uncertain.
"""
from bot.arbitrage.monotonicity import detect_monotonicity


def test_monotonicity_opportunity() -> None:
    """Verify monotonicity detector finds arb with sufficient edge.
    
    With p=0.80, Kelly requires higher edge to produce a positive fraction.
    edge = 1.0 - (ask_5m_no + buy_fee + slippage) - (ask_15m_yes + buy_fee + slippage)
    """
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_no_5m="no5",
        token_yes_15m="yes15",
        ask_5m_no=0.25,
        ask_15m_yes=0.40,
        vol_5m_no=1000.0,
        vol_15m_yes=1000.0,
        fee_rate_5m=0.03,
        fee_rate_15m=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25,
        gas_fee_est=0.0
    )
    
    assert opp is not None
    assert opp.type.value == "TYPE-B"
    assert len(opp.legs) == 2
    assert opp.legs[0].side == "BUY"
    assert opp.legs[0].price == 0.25
    assert opp.legs[1].side == "BUY"
    assert opp.legs[1].price == 0.40
    assert opp.edge > 0.30
    assert opp.size > 0


def test_monotonicity_marginal_edge_rejected() -> None:
    """Small edge that was previously accepted with p=1.0 should be
    rejected with p=0.80 since Kelly fraction goes negative."""
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_no_5m="no5",
        token_yes_15m="yes15",
        ask_5m_no=0.45,
        ask_15m_yes=0.50,
        vol_5m_no=1000.0,
        vol_15m_yes=1000.0,
        fee_rate_5m=0.03,
        fee_rate_15m=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25,
        gas_fee_est=0.0
    )
    # cost ~ 0.96. edge ≈ 0.04, but p=0.80 -> cost = 0.96, b = 0.04/0.96 = 0.041. Kelly = (0.8*0.041 - 0.2)/0.041 < 0
    assert opp is None


def test_monotonicity_no_edge() -> None:
    """Tight spread should produce no opportunity after fees."""
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_no_5m="no5",
        token_yes_15m="yes15",
        ask_5m_no=0.49,
        ask_15m_yes=0.50,
        vol_5m_no=1000.0,
        vol_15m_yes=1000.0,
        fee_rate_5m=0.03,
        fee_rate_15m=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25,
        gas_fee_est=0.0
    )
    assert opp is None


def test_monotonicity_inverted() -> None:
    """cost > 1.0 should produce no opportunity."""
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_no_5m="no5",
        token_yes_15m="yes15",
        ask_5m_no=0.55,
        ask_15m_yes=0.50,
        vol_5m_no=1000.0,
        vol_15m_yes=1000.0,
        fee_rate_5m=0.03,
        fee_rate_15m=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25,
        gas_fee_est=0.0
    )
    assert opp is None
