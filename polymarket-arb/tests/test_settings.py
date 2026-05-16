"""
Tests for settings.
"""
import os
from pathlib import Path
from unittest import mock

from bot.settings import Settings


def test_settings_load_defaults() -> None:
    """Test that settings load CODE defaults when no TOML exists."""
    with mock.patch.dict(os.environ, {}, clear=True):
        # Point to a non-existent TOML so code defaults are used
        settings = Settings.load(config_path=Path("nonexistent.toml"))
        assert settings.environment == "local"
        assert settings.starting_capital == 1000.0
        assert settings.trading.polymarket_fee == 0.03
        assert settings.trading.min_notional == 10.0
        assert settings.network.stale_feed_threshold_ms == 5000


def test_settings_override_env() -> None:
    """Test that environment variables override defaults."""
    env_vars = {
        "ENVIRONMENT": "production",
        "STARTING_CAPITAL": "5000.0"
    }
    with mock.patch.dict(os.environ, env_vars, clear=True):
        settings = Settings.load(config_path=Path("nonexistent.toml"))
        assert settings.environment == "production"
        assert settings.starting_capital == 5000.0


def test_settings_load_toml() -> None:
    """Test that TOML overrides code defaults."""
    with mock.patch.dict(os.environ, {}, clear=True):
        settings = Settings.load()  # Uses config/default.toml
        assert settings.trading.polymarket_fee == 0.03
        assert settings.trading.min_notional == 1.0
        assert settings.risk.max_daily_drawdown == 10.0
