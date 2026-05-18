"""Unit tests for the signal engine."""
import math
import pytest
from engine.signal_engine import (
    EWMAState,
    update_ewma,
    get_volatility,
    get_implied_probability,
    SignalEngine
)


def test_implied_probability() -> None:
    # Standard mid
    assert get_implied_probability(0.50, 0.52) == 0.51
    # Clamping tests
    assert get_implied_probability(-0.1, 0.0) == 0.001
    assert get_implied_probability(0.99, 1.01) == 0.999


def test_update_ewma_manual_math() -> None:
    # Use the math from the user's prompt as the test case
    # User's example:
    # variance_{t-1} = 0.000009
    # delta = 0.004
    # variance_t = 0.94 * 0.000009 + 0.06 * 0.004^2 = 0.00000942
    # In the time-weighted formula: lambda_eff = math.exp(-delta_t / tau)
    # To get lambda_eff = 0.94, let delta_t = 1.0, tau = -1 / ln(0.94) ~ 16.155
    
    lambda_eff_target = 0.94
    tau = -1.0 / math.log(lambda_eff_target)
    
    state = EWMAState(variance=0.000009, last_mid=0.516, last_timestamp=100.0)
    # Step exactly 1.0 seconds forward, moving mid by 0.004
    new_state = update_ewma(state, mid=0.52, now=101.0, tau=tau)
    
    assert new_state.last_mid == 0.52
    assert new_state.last_timestamp == 101.0
    assert new_state.variance == pytest.approx(0.00000942, rel=1e-5)
    
    vol = get_volatility(new_state)
    assert vol == pytest.approx(0.003069, rel=1e-3)


def test_update_ewma_out_of_order() -> None:
    """Test that time going backward clamps delta_t to 0."""
    tau = 10.0
    state = EWMAState(variance=0.01, last_mid=0.5, last_timestamp=100.0)
    
    # Send time backwards
    new_state = update_ewma(state, mid=0.51, now=90.0, tau=tau)
    
    # delta_t is clamped to 0, lambda_eff = exp(0) = 1.0
    # variance = 1.0 * 0.01 + 0.0 * delta_price^2 = 0.01
    assert new_state.variance == 0.01
    assert new_state.last_timestamp == 90.0


def test_update_ewma_max_delta_t() -> None:
    """Test that huge gaps are capped at MAX_DELTA_T_SECONDS."""
    tau = 10.0
    state = EWMAState(variance=0.01, last_mid=0.5, last_timestamp=100.0)
    
    new_state = update_ewma(state, mid=0.5, now=1000.0, tau=tau)
    # MAX_DELTA_T_SECONDS is 60.0
    expected_lambda = math.exp(-60.0 / tau)
    assert new_state.variance == expected_lambda * 0.01


def test_signal_engine_warm_up() -> None:
    tau = 16.15
    engine = SignalEngine(
        tau=tau,
        min_spread=0.006,
        warm_up_seconds=10.0,
        warm_up_min_obs=3
    )
    
    # Market not ready initially
    assert not engine.is_market_ready("m1", 0.0)
    assert engine.get_market_volatility("m1", 0.0) is None
    
    # Cold start
    engine.update_market("m1", 0.50, 100.0)
    assert engine._states["m1"].variance == (0.006 / 2.0) ** 2
    
    # Not ready: time = 0 elapsed, obs = 1
    assert not engine.is_market_ready("m1", 100.0)
    
    # Update with duplicate timestamp
    engine.update_market("m1", 0.51, 100.0)
    # State should not have updated because timestamp was the same
    assert engine._states["m1"].last_mid == 0.50
    assert engine._warm_up_trackers["m1"]["observation_count"] == 1
    
    # Update 2
    engine.update_market("m1", 0.51, 105.0)
    assert not engine.is_market_ready("m1", 105.0) # only 5s elapsed, obs=2
    
    # Update 3
    engine.update_market("m1", 0.52, 111.0)
    
    # Now elapsed=11s (>10s), obs=3 (>=3), var > 0
    assert engine.is_market_ready("m1", 111.0)
    
    vol = engine.get_market_volatility("m1", 111.0)
    assert vol is not None
    assert vol > 0.0


def test_get_volatility_negative_guard() -> None:
    """Test guard against precision errors causing negative variance."""
    state = EWMAState(variance=-1e-15, last_mid=0.5, last_timestamp=10.0)
    vol = get_volatility(state)
    assert vol == 0.0
