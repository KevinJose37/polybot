"""
tests/test_step2.py — Tests for Fair Value Engine & Quote Generation.
Tests both unit logic and end-to-end with live Binance data.
"""

import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tau import TauCalculator
from core.fair_value import FairValueEngine
from core.quote_engine import QuoteEngine, SuspendQuoting
from utils.schemas import InventoryState, ToxicityMetrics, ToxicityLevel


def test_tau_calculator():
    """Test tau brackets and spread multipliers."""
    print("[TEST] TauCalculator")

    # > 1 hour: multiplier = 1.0
    assert TauCalculator.get_spread_multiplier(7200) == 1.0
    print("  [PASS] > 1h: mult=1.0")

    # 30-60 min: multiplier = 1.2
    assert TauCalculator.get_spread_multiplier(2000) == 1.2
    print("  [PASS] 30-60m: mult=1.2")

    # 15-30 min: multiplier = 1.5
    assert TauCalculator.get_spread_multiplier(1200) == 1.5
    print("  [PASS] 15-30m: mult=1.5")

    # 5-15 min: multiplier = 2.5
    assert TauCalculator.get_spread_multiplier(600) == 2.5
    print("  [PASS] 5-15m: mult=2.5")

    # 2-5 min: multiplier = 5.0
    assert TauCalculator.get_spread_multiplier(200) == 5.0
    print("  [PASS] 2-5m: mult=5.0")

    # < 2 min: should not quote
    assert TauCalculator.get_spread_multiplier(60) == float("inf")
    assert not TauCalculator.should_quote(60)
    print("  [PASS] < 2m: SUSPEND")

    # Tau to years
    assert abs(TauCalculator.tau_to_years(365.25 * 24 * 3600) - 1.0) < 0.001
    print("  [PASS] tau_to_years")


def test_fair_value_known_inputs():
    """Test fair value with known inputs (no API calls)."""
    print("\n[TEST] FairValueEngine (synthetic inputs)")

    fv_engine = FairValueEngine()

    # ATM option: spot ≈ strike → prob ≈ 0.50
    fv = fv_engine.compute_fair_value(
        asset="btcusdt",
        strike=100000,
        tau_seconds=3600,   # 1 hour
        spot_override=100000,
        vol_override=0.60,
    )
    assert fv is not None
    assert 0.40 < fv.probability < 0.60, f"ATM prob={fv.probability}, expected ~0.50"
    print(f"  [PASS] ATM: spot=$100k, K=$100k → prob={fv.probability:.4f}")

    # Deep ITM: spot >> strike → prob ≈ 1.0
    fv = fv_engine.compute_fair_value(
        asset="btcusdt",
        strike=80000,
        tau_seconds=3600,
        spot_override=100000,
        vol_override=0.60,
    )
    assert fv is not None
    assert fv.probability > 0.80, f"ITM prob={fv.probability}, expected >0.80"
    print(f"  [PASS] ITM: spot=$100k, K=$80k → prob={fv.probability:.4f}")

    # Deep OTM: spot << strike → prob ≈ 0.0
    fv = fv_engine.compute_fair_value(
        asset="btcusdt",
        strike=120000,
        tau_seconds=3600,
        spot_override=100000,
        vol_override=0.60,
    )
    assert fv is not None
    assert fv.probability < 0.20, f"OTM prob={fv.probability}, expected <0.20"
    print(f"  [PASS] OTM: spot=$100k, K=$120k → prob={fv.probability:.4f}")

    # Very short tau: near-deterministic
    fv = fv_engine.compute_fair_value(
        asset="btcusdt",
        strike=100000,
        tau_seconds=10,     # 10 seconds
        spot_override=100500,
        vol_override=0.60,
    )
    assert fv is not None
    assert fv.probability > 0.90, f"Short tau ITM prob={fv.probability}, expected >0.90"
    print(f"  [PASS] Short tau ITM: spot=$100.5k, K=$100k, tau=10s → prob={fv.probability:.4f}")

    # Expired
    fv = fv_engine.compute_fair_value(
        asset="btcusdt",
        strike=100000,
        tau_seconds=0,
        spot_override=101000,
        vol_override=0.60,
    )
    assert fv is not None
    assert fv.probability > 0.98
    print(f"  [PASS] Expired ITM: prob={fv.probability:.4f}")


def test_quote_engine_symmetric():
    """Test quote engine with zero inventory → symmetric quotes."""
    print("\n[TEST] QuoteEngine (zero inventory → symmetric)")

    engine = QuoteEngine()
    inv = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=0, max_position=100,
    )

    quotes = engine.compute_quotes(
        fair_value=0.50,
        inventory=inv,
        tau_seconds=3600,
        volatility=0.60,
    )

    # Should be symmetric around fair value
    assert abs(quotes.mid - 0.50) < 0.01, f"Mid={quotes.mid}, expected ~0.50"
    assert quotes.bid_price < 0.50
    assert quotes.ask_price > 0.50
    assert quotes.spread > 0
    print(f"  [PASS] Symmetric: bid={quotes.bid_price:.4f}, ask={quotes.ask_price:.4f}, "
          f"mid={quotes.mid:.4f}, spread={quotes.spread:.4f}")


def test_quote_engine_long_inventory_skew():
    """Long inventory → lower bid, raise ask (discourage more buying)."""
    print("\n[TEST] QuoteEngine (long inventory → skew)")

    engine = QuoteEngine()

    # Zero inventory baseline
    inv_zero = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=0, max_position=100,
    )
    q_zero = engine.compute_quotes(0.50, inv_zero, 3600, 0.60)

    # Long inventory
    inv_long = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=50, max_position=100,
    )
    q_long = engine.compute_quotes(0.50, inv_long, 3600, 0.60)

    # Long inventory should lower both bid and ask (shift quotes down)
    assert q_long.bid_price < q_zero.bid_price, \
        f"Long bid={q_long.bid_price} should be < zero bid={q_zero.bid_price}"
    assert q_long.ask_price < q_zero.ask_price, \
        f"Long ask={q_long.ask_price} should be < zero ask={q_zero.ask_price}"
    print(f"  [PASS] Long skew: bid {q_zero.bid_price:.4f}→{q_long.bid_price:.4f}, "
          f"ask {q_zero.ask_price:.4f}→{q_long.ask_price:.4f}")


def test_quote_engine_short_inventory_skew():
    """Short inventory → raise bid, lower ask (encourage buying)."""
    print("\n[TEST] QuoteEngine (short inventory → skew)")

    engine = QuoteEngine()

    inv_zero = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=0, max_position=100,
    )
    q_zero = engine.compute_quotes(0.50, inv_zero, 3600, 0.60)

    inv_short = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=-50, max_position=100,
    )
    q_short = engine.compute_quotes(0.50, inv_short, 3600, 0.60)

    # Short inventory should raise both bid and ask (shift quotes up)
    assert q_short.bid_price > q_zero.bid_price, \
        f"Short bid={q_short.bid_price} should be > zero bid={q_zero.bid_price}"
    assert q_short.ask_price > q_zero.ask_price, \
        f"Short ask={q_short.ask_price} should be > zero ask={q_zero.ask_price}"
    print(f"  [PASS] Short skew: bid {q_zero.bid_price:.4f}→{q_short.bid_price:.4f}, "
          f"ask {q_zero.ask_price:.4f}→{q_short.ask_price:.4f}")


def test_quote_engine_boundary_enforcement():
    """Verify boundary enforcement: bid >= 0.01, ask <= 0.99."""
    print("\n[TEST] QuoteEngine (boundary enforcement)")

    engine = QuoteEngine()
    inv = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=0, max_position=100,
    )

    # Fair value near 0: bid should not go below 0.01
    q = engine.compute_quotes(0.03, inv, 3600, 0.60)
    assert q.bid_price >= 0.01, f"Bid={q.bid_price} below minimum 0.01"
    assert q.ask_price > q.bid_price
    print(f"  [PASS] Near-zero FV: bid={q.bid_price:.4f}, ask={q.ask_price:.4f}")

    # Fair value near 1: ask should not go above 0.99
    q = engine.compute_quotes(0.97, inv, 3600, 0.60)
    assert q.ask_price <= 0.99, f"Ask={q.ask_price} above maximum 0.99"
    assert q.bid_price < q.ask_price
    print(f"  [PASS] Near-one FV: bid={q.bid_price:.4f}, ask={q.ask_price:.4f}")


def test_quote_engine_tau_suspend():
    """Tau < 120s should raise SuspendQuoting."""
    print("\n[TEST] QuoteEngine (tau < 120s → suspend)")

    engine = QuoteEngine()
    inv = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=0, max_position=100,
    )

    try:
        engine.compute_quotes(0.50, inv, 60, 0.60)  # 60 seconds tau
        assert False, "Should have raised SuspendQuoting"
    except SuspendQuoting as e:
        print(f"  [PASS] SuspendQuoting raised: {e}")


def test_quote_engine_extreme_toxicity_suspend():
    """Extreme toxicity should raise SuspendQuoting."""
    print("\n[TEST] QuoteEngine (extreme toxicity → suspend)")

    engine = QuoteEngine()
    inv = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=0, max_position=100,
    )

    tox = ToxicityMetrics(
        order_imbalance=0.95,
        level=ToxicityLevel.EXTREME,
        spread_multiplier=1.0,
        size_multiplier=1.0,
    )

    try:
        engine.compute_quotes(0.50, inv, 3600, 0.60, toxicity=tox)
        assert False, "Should have raised SuspendQuoting"
    except SuspendQuoting as e:
        print(f"  [PASS] SuspendQuoting raised: {e}")


def test_quote_engine_toxicity_widens_spread():
    """Non-extreme toxicity should widen spread."""
    print("\n[TEST] QuoteEngine (mild toxicity → wider spread)")

    engine = QuoteEngine()
    inv = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=0, max_position=100,
    )

    # Normal
    q_normal = engine.compute_quotes(0.50, inv, 3600, 0.60)

    # Mildly toxic
    tox = ToxicityMetrics(
        order_imbalance=0.55,
        level=ToxicityLevel.MILD,
        spread_multiplier=1.5,
        size_multiplier=0.8,
    )
    q_toxic = engine.compute_quotes(0.50, inv, 3600, 0.60, toxicity=tox)

    assert q_toxic.spread > q_normal.spread, \
        f"Toxic spread={q_toxic.spread} should be > normal={q_normal.spread}"
    print(f"  [PASS] Spread widened: normal={q_normal.spread:.4f} → toxic={q_toxic.spread:.4f}")


def test_end_to_end_live():
    """End-to-end: fetch live Binance price → compute FV → generate quotes."""
    print("\n[TEST] End-to-end with live Binance data")

    from core.volatility import get_binance_spot_price

    spot = get_binance_spot_price("BTCUSDT")
    if spot is None:
        print("  [SKIP] Could not fetch Binance price (network issue)")
        return

    print(f"  Live BTC spot: ${spot:,.2f}")

    fv_engine = FairValueEngine()

    # Use a strike near current spot
    strike = round(spot / 1000) * 1000  # Round to nearest $1000
    tau = 3600  # 1 hour

    fv = fv_engine.compute_fair_value(
        "btcusdt", strike, tau, spot_override=spot
    )

    if fv is None:
        print("  [SKIP] Fair value computation failed")
        return

    print(f"  Fair value: {fv.probability:.4f} (spot=${spot:,.0f}, K=${strike:,.0f}, "
          f"vol={fv.volatility:.4f}, tau={tau}s)")

    # Generate quotes
    engine = QuoteEngine()
    inv = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=0, max_position=100,
    )
    quotes = engine.compute_quotes(fv.probability, inv, tau, fv.volatility)

    print(f"  Quotes: bid={quotes.bid_price:.4f}, ask={quotes.ask_price:.4f}, "
          f"spread={quotes.spread:.4f}, mid={quotes.mid:.4f}")

    assert 0.01 <= quotes.bid_price < quotes.ask_price <= 0.99
    print("  [PASS] End-to-end quote generation successful")


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 2 TESTS: Fair Value Engine & Quote Generation")
    print("=" * 60)

    test_tau_calculator()
    test_fair_value_known_inputs()
    test_quote_engine_symmetric()
    test_quote_engine_long_inventory_skew()
    test_quote_engine_short_inventory_skew()
    test_quote_engine_boundary_enforcement()
    test_quote_engine_tau_suspend()
    test_quote_engine_extreme_toxicity_suspend()
    test_quote_engine_toxicity_widens_spread()
    test_end_to_end_live()

    print()
    print("=" * 60)
    print("ALL STEP 2 TESTS PASSED")
    print("=" * 60)
