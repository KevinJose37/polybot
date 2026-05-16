"""
Settings configuration using pydantic-settings.
"""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingSettings(BaseSettings):
    """Trading and execution related settings."""
    polymarket_fee: float = 0.02
    slippage_est: float = 0.005
    min_edge: float = 0.01
    min_notional: float = 10.0
    kelly_fraction_multiplier: float = 0.25


class NetworkSettings(BaseSettings):
    """Networking and feed connection settings."""
    websocket_reconnect_min_backoff: float = 1.0
    websocket_reconnect_max_backoff: float = 60.0
    stale_feed_threshold_ms: int = 5000
    stale_silence_window_s: float = 30.0
    exchange_address: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    chain_id: int = 137


class ExecutionSettings(BaseSettings):
    """Execution lifecycle settings."""
    order_timeout_s: float = 30.0
    opportunity_dedup_window_s: float = 60.0


class PaperTradingSettings(BaseSettings):
    """Paper trading specific settings."""
    mean_latency_ms: float = 120.0
    std_latency_ms: float = 30.0


from pydantic import SecretStr

class ApiSettings(BaseSettings):
    """API credentials."""
    private_key: SecretStr = SecretStr("0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    host_address: str = "0x1234567890123456789012345678901234567890"

class RiskSettings(BaseSettings):
    """Risk constraints."""
    max_daily_drawdown: float = 10.0
    max_exposure_per_asset: float = 10.0
    max_portfolio_exposure: float = 25.0
    kill_switch_file: str = ".kill_switch"


class MonitoringSettings(BaseSettings):
    """Monitoring and observability settings."""
    health_port: int = 8080


class Settings(BaseSettings):
    """Main settings object."""
    environment: str = "local"
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""
    database_url: str = "sqlite+aiosqlite:///:memory:"
    redis_url: str = "redis://localhost:6379/0"
    starting_capital: float = 1000.0

    trading: TradingSettings = TradingSettings()
    network: NetworkSettings = NetworkSettings()
    execution: ExecutionSettings = ExecutionSettings()
    paper_trading: PaperTradingSettings = PaperTradingSettings()
    api: ApiSettings = ApiSettings()
    risk: RiskSettings = RiskSettings()
    monitoring: MonitoringSettings = MonitoringSettings()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Settings":
        """Load settings from environment and optionally from a TOML config file."""
        if config_path is None:
            # Default to config/default.toml relative to project root
            root = Path(__file__).parent.parent
            config_path = root / "config" / "default.toml"

        toml_data: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path, "rb") as f:
                toml_data = tomllib.load(f)
        else:
            import warnings
            warnings.warn(
                f"Config file not found at {config_path}. Using hardcoded defaults. "
                "Risk limits may not match intended values.",
                UserWarning,
                stacklevel=2,
            )

        settings = cls()
        
        # Override with TOML settings if they exist
        if "trading" in toml_data:
            settings.trading = TradingSettings(**toml_data["trading"])
        if "network" in toml_data:
            settings.network = NetworkSettings(**toml_data["network"])
        if "execution" in toml_data:
            settings.execution = ExecutionSettings(**toml_data["execution"])
        if "paper_trading" in toml_data:
            settings.paper_trading = PaperTradingSettings(**toml_data["paper_trading"])
        if "api" in toml_data:
            settings.api = ApiSettings(**toml_data["api"])
        if "risk" in toml_data:
            settings.risk = RiskSettings(**toml_data["risk"])
        if "monitoring" in toml_data:
            settings.monitoring = MonitoringSettings(**toml_data["monitoring"])

        return settings
