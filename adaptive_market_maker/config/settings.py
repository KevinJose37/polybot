from pydantic import BaseModel, Field, model_validator


class Config(BaseModel):
    """
    Configuration for the Adaptive Maker-Side Market Making strategy.
    """

    spread: float = Field(0.008, description="Target spread to capture")
    skew_factor: float = Field(0.5, description="Factor for inventory skewing")
    vol_mult: float = Field(2.0, description="Multiplier for EWMA volatility when calculating half-spread")
    vol_lambda: float = Field(0.94, description="Decay factor for EWMA calculation")
    min_spread: float = Field(0.006, description="Minimum allowable spread")
    requote_threshold: float = Field(0.003, description="Threshold price difference required to cancel-and-replace")
    max_inventory: float = Field(5.0, description="Maximum absolute inventory per market")
    emergency_factor: float = Field(1.3, description="Multiplier applied to max_inventory to stop quoting that side")
    dwell_min_seconds: float = Field(3.6, description="Minimum dwell time in seconds before cancelling an order (rebate rule)")
    oracle_pause_seconds: float = Field(15.0, description="Look-ahead buffer in seconds to pause quoting before an oracle update")
    expiry_pause_seconds: float = Field(90.0, description="Seconds to pause quoting before the end of the market window")
    warm_up_seconds: int = Field(300, description="Seconds required to warm up the EWMA volatility before quoting")
    warm_up_min_observations: int = Field(60, description="Minimum number of mid-price samples required for EWMA warm up")
    order_size_usdc: float = Field(1.0, description="Fixed order size in USDC. Do NOT size dynamically until capital > $200.")
    max_open_orders: int = Field(4, description="Hard cap on number of live orders to prevent accidental overdeployment.")
    max_capital_deployed_pct: float = Field(0.60, description="Maximum percentage of capital in open orders + inventory combined.")
    markets: list[str] = Field(
        ["ETH-15m", "BTC-15m", "SOL-15m", "ETH-5m", "BTC-5m", "SOL-5m"], 
        description="List of market definitions for dynamic discovery (format: 'ASSET-WINDOW', e.g., 'ETH-15m')."
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
