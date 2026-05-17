"""
Tests for Type B - Monotonicity.
Uses the real Polymarket fee formula. Sell orders are fee-free.
Type-B uses p=0.80 (conservative) since settlement is uncertain.
"""
from bot.arbitrage.monotonicity import detect_monotonicity


def test_monotonicity_opportunity() -> None:
    """Verify monotonicity detector finds arb with sufficient edge.
    
    With p=0.80, Kelly requires higher edge to produce a positive fraction.
    edge = (bid_5m - slippage) - (ask_15m + buy_fee + slippage)
    We use a wider spread (0.75 vs 0.40) to ensure Kelly is positive.
    """
    opp = detect_monotonicity(
        market_5m_id="m5",
        market_15m_id="m15",
        token_yes_5m="yes5",
        token_yes_15m="yes15",
        bid_5m=0.75,
        ask_15m=0.40,
        vol_5m=1000.0,
        vol_15m=1000.0,
        fee_rate_5m=0.03,
        fee_rate_15m=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25
    )
    
    assert opp is not None
    assert opp.type.value == "TYPE-B"
    assert len(opp.legs) == 2
    assert opp.legs[0].side == "SELL"
    assert opp.legs[0].price == 0.75
    assert opp.legs[1].side == "BUY"
    assert opp.legs[1].price == 0.40
    # 5m SELL is fee-free, 15m BUY pays fee
    # fee_per_share(0.40, 0.03) = 0.40 × 0.03 × (0.40 × 0.60) = 0.00288
    # edge = (0.75 - 0.005) - (0.40 + 0.00288 + 0.005) = 0.745 - 0.40788 = 0.33712
    assert opp.edge > 0.30
    assert opp.size > 0


def test_monotonicity_marginal_edge_rejected() -> None:
    """Small edge that was previously accepted with p=1.0 should be
    rejected with p=0.80 since Kelly fraction goes negative."""
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
        capital=1000.0,
        multiplier=0.25
    )
    # edge ≈ 0.086, but p=0.80 → Kelly < 0 → size=0 → below min_notional
    assert opp is None


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
        capital=1000.0,
        multiplier=0.25
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
        capital=1000.0,
        multiplier=0.25
    )
    assert opp is None
