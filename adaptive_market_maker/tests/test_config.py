import pytest
from pydantic import ValidationError

from config.settings import Config


def test_valid_config() -> None:
    """Test that a valid configuration loads correctly."""
    config = Config(
        spread=0.01,
        skew_factor=0.005,
        vol_mult=1.5,
        vol_lambda=0.94,
        min_spread=0.005,
        requote_threshold=0.002,
        max_inventory=1000.0,
        emergency_factor=1.2,
        dwell_min_seconds=3.6,
        oracle_pause_seconds=10.0,
        expiry_pause_seconds=60.0,
        warm_up_seconds=300,
        warm_up_min_observations=60,
        markets=["ETH-15m", "BTC-5m"],
    )

    assert config.spread == 0.01
    assert config.dwell_min_seconds == 3.6
    assert len(config.markets) == 2


def test_invalid_negative_spread() -> None:
    """Test that negative spread raises validation error."""
    with pytest.raises(ValidationError, match="spread must be non-negative"):
        Config(
            spread=-0.01,
            skew_factor=0.005,
            vol_mult=1.5,
            vol_lambda=0.94,
            min_spread=0.005,
            requote_threshold=0.002,
            max_inventory=1000.0,
            emergency_factor=1.2,
            dwell_min_seconds=4.0,
            oracle_pause_seconds=10.0,
            expiry_pause_seconds=60.0,
            warm_up_seconds=300,
            warm_up_min_observations=60,
            markets=["ETH-15m"],
        )


def test_invalid_dwell_time() -> None:
    """Test that dwell time below 3.5s raises validation error."""
    with pytest.raises(ValidationError, match="dwell_min_seconds must be at least 3.5"):
        Config(
            spread=0.01,
            skew_factor=0.005,
            vol_mult=1.5,
            vol_lambda=0.94,
            min_spread=0.005,
            requote_threshold=0.002,
            max_inventory=1000.0,
            emergency_factor=1.2,
            dwell_min_seconds=3.4,  # Below 3.5s minimum
            oracle_pause_seconds=10.0,
            expiry_pause_seconds=60.0,
            warm_up_seconds=300,
            warm_up_min_observations=60,
            markets=["ETH-15m"],
        )
