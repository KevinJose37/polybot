"""
config/settings.py — Centralized configuration for the Market Maker bot.
Uses Pydantic Settings for automatic .env loading and validation.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Market Maker configuration.
    All parameters are loaded from .env file or environment variables.
    """

    # ── Polymarket Credentials ──────────────────────────────────
    private_key: str = ""
    funder_address: str = ""
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""

    # ── Trading Mode ────────────────────────────────────────────
    paper_trading: bool = True
    initial_capital: float = 25.0

    # ── Spread Parameters ──────────────────────────────────────
    min_half_spread_bps: float = 30.0       # Hard minimum half-spread in bps on probability scale
    spread_volatility_multiplier: float = 2.0  # Higher vol → wider spread
    skew_sensitivity: float = 1.0           # Inventory skew multiplier
    spread_competition_floor_pct: float = 0.0  # Quote at 90% of best competitor spread

    # ── Inventory Limits ───────────────────────────────────────
    max_inventory_per_market: int = 100     # Max contracts per market side
    max_net_asset_inventory: int = 200      # Max net contracts per asset across all markets
    max_net_total_inventory: int = 500      # Max net contracts across entire portfolio
    soft_inventory_pct: float = 0.60        # Soft limit: increase skew
    hard_inventory_pct: float = 0.85        # Hard limit: one-sided quoting
    inventory_stale_ms: int = 60_000        # Auto-widen exit after this duration without reduction

    # ── Toxicity Detection ─────────────────────────────────────
    toxicity_window_trades: int = 20        # Rolling window size for VPIN metric
    toxicity_threshold: float = 0.70        # Enter defensive mode above this
    defensive_spread_multiplier: float = 3.0  # Spread multiplier in defensive mode
    defensive_quote_size_pct: float = 0.50  # Reduce quote size to 50% in defensive mode

    # ── Quote Management ───────────────────────────────────────
    max_quote_age_ms: int = 5000            # Force reprice after this age
    reprice_threshold_bps: float = 10.0     # Reprice if FV moves this much from quote mid
    emergency_reprice_bps: float = 50.0     # Immediate cancel if spot moves this much
    fv_update_threshold_bps: float = 5.0    # Recompute fair value if composite mid moves this much
    max_fv_age_ms: int = 500                # Max age of fair value before forced recompute
    min_spread_abs: float = 0.01            # Minimum absolute spread (ask - bid)
    min_quote_price: float = 0.01           # Never quote bid below this
    max_quote_price: float = 0.99           # Never quote ask above this

    # ── Time-to-Expiry ─────────────────────────────────────────
    min_tau_to_quote: int = 120             # Stop quoting when tau < 120 seconds

    # ── Quote Fade ─────────────────────────────────────────────
    fade_trigger_bps: float = 30.0          # Fast market: fade if move > this in trigger window
    fade_trigger_window_ms: int = 500       # Window for detecting fast market moves
    fade_duration_ms: int = 1000            # How long to fade (withdraw liquidity)
    large_trade_threshold: int = 50         # Fade if trade > this many contracts

    # ── Risk Limits ─────────────────────────────────────────────
    daily_loss_limit: float = 0.20          # Halt if portfolio drops > 20% in a day
    max_drawdown_pct: float = 0.30          # Absolute max drawdown before kill switch

    # ── Fee Modeling ────────────────────────────────────────────
    maker_fee_rate: float = 0.01            # Polymarket maker fee (currently 0)
    taker_fee_rate: float = 0.02            # Polymarket taker fee
    gas_cost_per_tx: float = 0.001          # Estimated Polygon gas cost per transaction (USD)

    # ── Paper Trading Fill Simulation ──────────────────────────
    default_fill_probability: float = 0.25  # Conservative queue-position fill probability
    fill_rate_feedback_threshold: float = 0.30  # Auto-adjust if actual vs predicted deviates > 30%

    # ── Feed Configuration ──────────────────────────────────────
    binance_ws_url: str = "wss://stream.binance.com:9443/ws"
    stale_feed_threshold_ms: int = 10_000   # Cancel quotes if feed stale > this (10s for low-vol assets)
    poly_book_stale_threshold_ms: int = 2000  # Suspend quoting if Poly book stale > this

    # ── Target Markets ──────────────────────────────────────────
    # All 4 assets × 3 windows
    assets: list[str] = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
    windows: list[int] = [5, 15, 60]        # Minutes

    # ── Logging ─────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Data Persistence ────────────────────────────────────────
    trades_file: str = "data/trades.json"
    fills_file: str = "data/fills.json"
    pnl_file: str = "data/pnl.json"
    state_file: str = "data/state.json"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global config instance
config = Settings()
