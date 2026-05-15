"""
Tests for settings.
"""
import os
from unittest import mock

from bot.settings import Settings


def test_settings_load_defaults() -> None:
    """Test that settings load default values correctly without a .env file."""
    with mock.patch.dict(os.environ, {}, clear=True):
        settings = Settings.load()
        assert settings.environment == "local"
        assert settings.starting_capital == 1000.0
        assert settings.trading.polymarket_fee == 0.02
        assert settings.trading.min_notional == 1.0
        assert settings.network.stale_feed_threshold_ms == 5000


def test_settings_override_env() -> None:
    """Test that environment variables override defaults."""
    env_vars = {
        "ENVIRONMENT": "production",
        "STARTING_CAPITAL": "5000.0"
    }
    with mock.patch.dict(os.environ, env_vars, clear=True):
        settings = Settings.load()
        assert settings.environment == "production"
        assert settings.starting_capital == 5000.0
