from typing import Optional
from pydantic import BaseModel, Field, model_validator


class LatencyConfig(BaseModel):
    place_mean_ms: float = Field(default=50.0, ge=0.0)
    place_std_ms: float = Field(default=15.0, ge=0.0)
    cancel_mean_ms: float = Field(default=30.0, ge=0.0)
    cancel_std_ms: float = Field(default=10.0, ge=0.0)
    market_data_mean_ms: float = Field(default=20.0, ge=0.0)
    market_data_std_ms: float = Field(default=5.0, ge=0.0)
    p_fat_tail: float = Field(default=0.01, ge=0.0, le=1.0)
    fat_tail_mult: float = Field(default=5.0, ge=1.0)

class MarketOverride(BaseModel):
    min_spread: Optional[float] = None
    vol_mult: Optional[float] = None
    order_size_usdc: Optional[float] = None
    max_position_usdc: Optional[float] = None

class Config(BaseModel):
    """
    Configuration for the Adaptive Maker-Side Market Making strategy.
    """

    paper_trading: bool = Field(False, description="Enable paper trading and latency simulation")
    latency: LatencyConfig = Field(default_factory=LatencyConfig)

    spread: float = Field(0.008, description="Target spread to capture")
    skew_factor: float = Field(0.5, description="Factor for inventory skewing")
    vol_mult: float = Field(2.0, description="Multiplier for EWMA volatility when calculating half-spread")
    vol_lambda: float = Field(0.94, description="Decay factor for EWMA calculation")
    min_spread: float = Field(0.006, description="Minimum allowable spread")
    requote_threshold: float = Field(0.003, description="Threshold price difference required to cancel-and-replace")
    max_position_usdc: float = Field(50.0, description="Maximum absolute position in USDC per market")
    emergency_factor: float = Field(1.3, description="Multiplier applied to max_inventory to stop quoting that side")
    dwell_min_seconds: float = Field(3.6, description="Minimum dwell time in seconds before cancelling an order (rebate rule)")
    cancel_cooldown_seconds: float = Field(0.5, description="Minimum seconds between cancel requests per side (rate limiter)")
    requote_cooldown_seconds: float = Field(1.0, description="Minimum seconds between requote (cancel+place) cycles per side")
    oracle_pause_seconds: float = Field(15.0, description="Look-ahead buffer in seconds to pause quoting before an oracle update")
    oracle_pause_cooldown_seconds: float = Field(30.0, description="Seconds to wait before clearing oracle pause")
    polygon_rpc_url: str = Field("https://polygon-rpc.com/", description="Polygon RPC URL")
    expiry_pause_seconds: float = Field(90.0, description="Seconds to pause quoting before the end of the market window")
    warm_up_seconds: int = Field(300, description="Seconds required to warm up the EWMA volatility before quoting")
    warm_up_min_observations: int = Field(60, description="Minimum number of mid-price samples required for EWMA warm up")
    order_size_usdc: float = Field(1.0, description="Fixed order size in USDC. Do NOT size dynamically until capital > $200.")
    max_open_orders: int = Field(4, description="Hard cap on number of live orders to prevent accidental overdeployment.")
    max_capital_deployed_pct: float = Field(0.60, description="Maximum percentage of capital in open orders + inventory combined.")
    total_capital: float = Field(30.0, description="Total capital available for deployment, in USDC.")
    max_drawdown_pct: float = Field(0.15, ge=0.01, le=1.0, description="Maximum drawdown percentage before kill-switch halts quoting.")  # [H-4]
    min_order_size: float = Field(0.0, ge=0.0, description="Minimum order size in shares. Orders below this are suppressed.")  # [M-2]
    markets: list[str] = Field(
        ["ETH-15m", "BTC-15m", "SOL-15m", "ETH-5m", "BTC-5m", "SOL-5m"], 
        description="List of market definitions for dynamic discovery (format: 'ASSET-WINDOW', e.g., 'ETH-15m')."
    )
    # [H-1] Runtime list of active token IDs populated by lifecycle manager.
    # NOT validated for ASSET-WINDOW format — these are raw Polymarket token IDs.
    active_token_ids: list[str] = Field(default_factory=list, description="Runtime-populated active token IDs (not user-configured).")
    market_overrides: dict[str, MarketOverride] = Field(default_factory=dict, description="Per-market overrides for risk parameters.")
    chainlink_feeds: dict[str, str] = Field(
        default_factory=lambda: {
            "ETH": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
            "BTC": "0xc907E116054Ad103354f2D350FD2455D0EB91572",
            "SOL": "0x108D63925EE2EAA14F5e076CDE0F46a489c93df5",
            "XRP": "0x785ba89291f676b5386652eB12b30cF361020694"
        },
        description="Mapping of underlying to Chainlink feed addresses on Polygon."
    )

    @model_validator(mode="after")
    def validate_constraints(self) -> "Config":
        if self.spread < 0:
            raise ValueError("spread must be non-negative")
        if self.min_spread < 0:
            raise ValueError("min_spread must be non-negative")
        if self.requote_threshold < 0:
            raise ValueError("requote_threshold must be non-negative")
        if self.dwell_min_seconds < 3.5:
            raise ValueError("dwell_min_seconds must be at least 3.5 seconds to qualify for rebates")
        if self.order_size_usdc <= 0:
            raise ValueError("order_size_usdc must be positive")
        if self.max_open_orders <= 0:
            raise ValueError("max_open_orders must be positive")
        if not (0 < self.max_capital_deployed_pct <= 1.0):
            raise ValueError("max_capital_deployed_pct must be between 0 and 1.0")
        
        for market in self.markets:
            if "-" not in market:
                raise ValueError(f"Invalid market format: {market}. Expected 'ASSET-WINDOW' (e.g., 'ETH-15m')")
            
        return self
