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
        max_position_usdc=2.5,  # with mid=0.50, max_inv = 5.0 shares
        skew_factor=0.5,
        emergency_factor=1.3
    )
    
    # 1. Normal inventory
    q1 = engine.get_quotes(mid=0.50, vol=0.003, inventory=3.0, tick_size=0.001)
    assert q1.bid is not None
    assert q1.ask is not None
    
    # 2. Extreme long inventory (>= 5.0 * 1.3 = 6.5)
    q2 = engine.get_quotes(mid=0.50, vol=0.003, inventory=6.5, tick_size=0.001)
    assert q2.bid is None  # Halt buying
    assert q2.ask is not None
    
    # 3. Extreme short inventory (<= -6.5)
    q3 = engine.get_quotes(mid=0.50, vol=0.003, inventory=-6.6, tick_size=0.001)
    assert q3.bid is not None
    assert q3.ask is None  # Halt selling


def test_quoting_engine_mid_clamp() -> None:
    """Test that garbage mid values still produce valid quotes when pre-clamped by caller."""
    engine = QuotingEngine(
        min_spread=0.006,
        vol_mult=2.0,
        max_position_usdc=2.5,  # dummy value
        skew_factor=0.0,
        emergency_factor=1.3
    )
    
    # Caller (bot.py) clamps mid to [tick_size, 1.0 - tick_size] before calling get_quotes.
    # So the QuotingEngine receives 0.001, not -0.50.
    clamped_mid = max(0.001, min(0.999, -0.50))  # = 0.001
    quotes = engine.get_quotes(mid=clamped_mid, vol=0.0, inventory=0.0, tick_size=0.001)
    # mid = 0.001, half_spread = 0.003
    # bid_raw = 0.001 - 0.003 = -0.002 -> floor = -0.002 -> clamp to 0.001
    # ask_raw = 0.001 + 0.003 = 0.004 -> ceil = 0.004 -> bound check OK -> 0.004
    assert quotes.bid == 0.001
    assert quotes.ask == 0.004


def test_quoting_engine_soft_disable_at_max_inventory() -> None:
    """F-13: Test that inventory at max_inventory WIDENS the accumulating side
    (not nulls it). Only at emergency_factor × max_inventory is the side nulled."""
    engine = QuotingEngine(
        min_spread=0.006,
        vol_mult=2.0,
        max_position_usdc=2.5,  # mid=0.50 -> 5.0 shares max
        skew_factor=0.5,
        emergency_factor=1.3
    )
    
    # Get normal quotes for comparison (inventory below max)
    q_normal = engine.get_quotes(mid=0.50, vol=0.003, inventory=4.9, tick_size=0.001)
    assert q_normal.bid is not None
    assert q_normal.ask is not None
    
    # Inventory exactly at max (5.0): soft-disable widens bid, does NOT null it
    q_long = engine.get_quotes(mid=0.50, vol=0.003, inventory=5.0, tick_size=0.001)
    assert q_long.bid is not None   # NOT None — widened instead
    assert q_long.ask is not None
    # Widened bid must be further from mid than the normal bid
    assert q_long.bid < q_normal.bid
    
    q_short = engine.get_quotes(mid=0.50, vol=0.003, inventory=-5.0, tick_size=0.001)
    assert q_short.bid is not None
    assert q_short.ask is not None  # NOT None — widened instead
    # Widened ask must be further from mid than the normal ask
    assert q_short.ask > q_normal.ask
    
    # At emergency_factor × max_inventory (6.5+): hard disable nulls the side
    q_emergency_long = engine.get_quotes(mid=0.50, vol=0.003, inventory=6.5, tick_size=0.001)
    assert q_emergency_long.bid is None   # Hard null at emergency threshold
    assert q_emergency_long.ask is not None
    
    q_emergency_short = engine.get_quotes(mid=0.50, vol=0.003, inventory=-6.5, tick_size=0.001)
    assert q_emergency_short.bid is not None
    assert q_emergency_short.ask is None  # Hard null at emergency threshold

