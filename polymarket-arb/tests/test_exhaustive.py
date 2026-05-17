"""
Tests for Type C - Exhaustive Sets.
Uses the real Polymarket fee formula: fee = C × p × feeRate × (p × (1-p))^1
Sell orders are fee-free.
"""
from bot.arbitrage.exhaustive_sets import detect_exhaustive_parity


def test_exhaustive_buy_opportunity() -> None:
    """Verify exhaustive detector finds BUY-side arb with Polymarket fee model."""
    opp = detect_exhaustive_parity(
        market_id="m1",
        token_up_id="yes1",
        token_down_id="no1",
        up_bid=0.40,
        up_ask=0.42,
        down_bid=0.44,
        down_ask=0.46,
        up_ask_vol=500.0,
        down_ask_vol=500.0,
        up_bid_vol=1000.0,
        down_bid_vol=1000.0,
        up_fee_rate=0.03,
        down_fee_rate=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25
    )
    
    assert opp is not None
    assert opp.type.value == "TYPE-C"
    assert opp.legs[0].side == "BUY"
    assert opp.legs[0].price == 0.42
    assert opp.edge > 0.01  # Above min_edge


def test_exhaustive_sell_opportunity() -> None:
    """Verify exhaustive detector finds SELL-side arb (sell is fee-free)."""
    opp = detect_exhaustive_parity(
        market_id="m1",
        token_up_id="yes1",
        token_down_id="no1",
        up_bid=0.56,
        up_ask=0.58,
        down_bid=0.50,
        down_ask=0.52,
        up_ask_vol=500.0,
        down_ask_vol=500.0,
        up_bid_vol=1000.0,
        down_bid_vol=1000.0,
        up_fee_rate=0.03,
        down_fee_rate=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25
    )
    
    assert opp is not None
    assert opp.type.value == "TYPE-C"
    assert opp.legs[0].side == "SELL"
    assert opp.legs[0].price == 0.56
    # Sell side is fee-free, so edge should be higher than old formula
    assert opp.edge > 0.01


def test_exhaustive_sum_exactly_1() -> None:
    """Asks summing to 1.0 exactly should have no edge after fees+slippage."""
    opp = detect_exhaustive_parity(
        market_id="m1",
        token_up_id="yes1",
        token_down_id="no1",
        up_bid=0.48,
        up_ask=0.50,
        down_bid=0.48,
        down_ask=0.50,
        up_ask_vol=500.0,
        down_ask_vol=500.0,
        up_bid_vol=1000.0,
        down_bid_vol=1000.0,
        up_fee_rate=0.03,
        down_fee_rate=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25
    )
    assert opp is None


def test_exhaustive_no_opportunity() -> None:
    """No arb when spreads are wide and sums are close to 1."""
    opp = detect_exhaustive_parity(
        market_id="m1",
        token_up_id="yes1",
        token_down_id="no1",
        up_bid=0.49,
        up_ask=0.51,
        down_bid=0.49,
        down_ask=0.51,
        up_ask_vol=500.0,
        down_ask_vol=500.0,
        up_bid_vol=1000.0,
        down_bid_vol=1000.0,
        up_fee_rate=0.03,
        down_fee_rate=0.03,
        slippage=0.005,
        min_edge=0.01,
        min_notional=1.0,
        capital=1000.0,
        multiplier=0.25
    )
    assert opp is None
