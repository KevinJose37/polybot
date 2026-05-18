"""Unit tests for the Quoting Engine."""

import math
import pytest
from engine.quoting_engine import calculate_quotes, QuotingEngine, QuoteResult


def test_calculate_quotes_exact_example() -> None:
    """Test the exact numerical example provided by the user."""
    # Assume:
    # mid = 0.52
    # vol = 0.00307 (vol was √0.00000942 = 0.003069...)
    # inventory = 3.0
    # min_spread = 0.006
    # vol_mult = 2.0
    # max_inventory = 5.0
    # skew_factor = 0.5
    # tick_size = 0.001
    
    mid = 0.52
    vol = 0.00307
    inventory = 3.0
    min_spread = 0.006
    vol_mult = 2.0
    max_inventory = 5.0
    skew_factor = 0.5
    tick_size = 0.001

    quotes = calculate_quotes(
        mid=mid,
        vol=vol,
        inventory=inventory,
        min_spread=min_spread,
        vol_mult=vol_mult,
        max_inventory=max_inventory,
        skew_factor=skew_factor,
        tick_size=tick_size
    )

    # 1. Half-spread = max(0.003, 0.00307 * 2.0) = 0.00614
    assert quotes.half_spread == pytest.approx(0.00614)
    # 2 & 3. Skew = (3.0 / 5.0) * 0.5 * 0.00614 = 0.001842
    assert quotes.skew == pytest.approx(0.001842)
    # 7. Tick rounding and bounds: bid = 0.512, ask = 0.525
    assert quotes.bid == 0.512
    assert quotes.ask == 0.525


def test_calculate_quotes_ratio_clamp() -> None:
    """Test that inventory beyond max_inventory is clamped for skew."""
    # With inventory = 10.0 and max = 5.0, ratio should be clamped to 1.0
    quotes = calculate_quotes(
        mid=0.50,
        vol=0.0,  # Forces half_spread to min_spread/2 = 0.003
        inventory=10.0,
        min_spread=0.006,
        vol_mult=2.0,
        max_inventory=5.0,
        skew_factor=0.5,
        tick_size=0.001
    )
    # ratio = 1.0 -> skew = 1.0 * 0.5 * 0.003 = 0.0015
    assert quotes.skew == pytest.approx(0.0015)


def test_calculate_quotes_bound_correction() -> None:
    """Test that the deterministic bound logic pushes quotes safely away from mid."""
    # mid = 0.5005
    # tick_size = 0.001
    # raw bid might be 0.5001 -> floor(0.5001 / 0.001) * 0.001 = 0.500
    # But wait, let's force bid >= mid.
    # Suppose mid = 0.50, half_spread = 0.0001, skew = 0.
    # bid_raw = 0.4999 -> floor(0.499) = 0.499
    # ask_raw = 0.5001 -> ceil(0.501) = 0.501
    
    # To trigger the `bid >= mid` condition, we can use a very small negative skew or very tiny spread,
    # but since tick size rounding goes out, we have to create a specific scenario where tick rounding 
    # pushes it *onto* the mid.
    # mid = 0.500, tick = 0.001
    # raw_bid = 0.500 -> floor = 0.500 >= mid!
    # raw_ask = 0.500 -> ceil = 0.500 <= mid!
    
    quotes = calculate_quotes(
        mid=0.500,
        vol=0.0,
        inventory=0.0,
        min_spread=0.000, # tiny spread
        vol_mult=0.0,
        max_inventory=5.0,
        skew_factor=0.0,
        tick_size=0.001
    )
    # Raw bid and ask will be exactly 0.500.
    # Bound checks will see bid >= mid (0.500 >= 0.500) -> floor((0.500 - 0.001) / 0.001) * 0.001 = 0.499
    # Bound checks will see ask <= mid (0.500 <= 0.500) -> ceil((0.500 + 0.001) / 0.001) * 0.001 = 0.501
    assert quotes.bid == pytest.approx(0.499)
    assert quotes.ask == pytest.approx(0.501)


def test_calculate_quotes_polymarket_bounds() -> None:
    """Test that quotes are strictly clamped to [0.001, 0.999]."""
    quotes = calculate_quotes(
        mid=0.002,
        vol=0.0,
        inventory=0.0,
        min_spread=0.100, # forces wide spread
        vol_mult=0.0,
        max_inventory=5.0,
        skew_factor=0.0,
        tick_size=0.001
    )
    assert quotes.bid == 0.001  # clamped from negative
    assert quotes.ask == pytest.approx(0.052)

    quotes_high = calculate_quotes(
        mid=0.998,
        vol=0.0,
        inventory=0.0,
        min_spread=0.100,
        vol_mult=0.0,
        max_inventory=5.0,
        skew_factor=0.0,
        tick_size=0.001
    )
    assert quotes_high.bid == pytest.approx(0.948)
    assert quotes_high.ask == 0.999  # clamped from > 1.0


def test_quoting_engine_orchestration_emergency_stop() -> None:
    """Test the orchestration logic for emergency halting."""
    engine = QuotingEngine(
        min_spread=0.006,
        vol_mult=2.0,
        max_inventory=5.0,
        skew_factor=0.5,
        emergency_factor=1.3,
        tick_size=0.001
    )
    
    # 1. Normal inventory
    q1 = engine.get_quotes(mid=0.50, vol=0.003, inventory=3.0)
    assert q1.bid is not None
    assert q1.ask is not None
    
    # 2. Extreme long inventory (>= 5.0 * 1.3 = 6.5)
    q2 = engine.get_quotes(mid=0.50, vol=0.003, inventory=6.5)
    assert q2.bid is None  # Halt buying
    assert q2.ask is not None
    
    # 3. Extreme short inventory (<= -6.5)
    q3 = engine.get_quotes(mid=0.50, vol=0.003, inventory=-6.6)
    assert q3.bid is not None
    assert q3.ask is None  # Halt selling


def test_quoting_engine_mid_clamp() -> None:
    """Test that the orchestrator clamps garbage mid values at ingestion."""
    engine = QuotingEngine(
        min_spread=0.006,
        vol_mult=2.0,
        max_inventory=5.0,
        skew_factor=0.0,
        emergency_factor=1.3,
        tick_size=0.001
    )
    
    # Send a garbage mid < 0.001
    quotes = engine.get_quotes(mid=-0.50, vol=0.0, inventory=0.0)
    # mid should be clamped to 0.001, half_spread = 0.003
    # bid_raw = 0.001 - 0.003 = -0.002 -> floor = -0.002 -> clamp to 0.001
    # ask_raw = 0.001 + 0.003 = 0.004 -> ceil = 0.004 -> bound check OK -> 0.004
    assert quotes.bid == 0.001
    assert quotes.ask == 0.004
