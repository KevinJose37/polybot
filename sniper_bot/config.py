"""
sniper_bot/config.py — All tunable parameters for the sniper bot.

Load order: defaults → .env → CLI args
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class SniperConfig:
    """Immutable-ish config. Set once at startup, read everywhere."""

    # ── Capital ──────────────────────────────────────────────────
    capital: float = float(os.getenv("SNIPER_CAPITAL", "500.0"))
    stake: float = float(os.getenv("SNIPER_STAKE", "10.0"))
    max_concurrent: int = int(os.getenv("SNIPER_MAX_CONCURRENT", "4"))

    # ── Signal gates ─────────────────────────────────────────────
    trigger_low: float = 0.40       # Ask must be >= this to trigger
    trigger_high: float = 0.60      # Ask must be <= this to trigger
    max_ask: float = 0.65           # Hard reject above this
    min_ask: float = 0.35           # Hard reject below this (trap zone)
    max_spread: float = 0.03        # Reject if spread > 3 cents
    min_depth: float = 50.0         # Min ask depth (shares) to enter
    max_velocity: float = 0.05      # Reject if ask moved > 5c in 500ms (toxic flow)
    entry_window_s: int = 60        # Only allow entries in first 60s of market

    # ── Dynamic TP (book-depth based) ────────────────────────────
    wall_threshold: float = 1000.0  # Shares — defines a "liquidity wall" (Increased to ignore small bumps)
    min_tp_increment: float = 0.05  # TP at least 5c above entry
    max_tp: float = 0.80            # Never set TP above this (Allows riding trends)
    # Fallback TP when no wall detected in book
    tp_fallback_low: float = 0.25   # entry < 0.40 → +25c
    tp_fallback_mid: float = 0.20   # entry 0.40-0.50 → +20c
    tp_fallback_high: float = 0.15  # entry > 0.50 → +15c

    # ── Circuit breaker ──────────────────────────────────────────
    max_consecutive_losses: int = 999
    max_drawdown_pct: float = 0.50  # Halt if we lose 50% of capital (Increased for small $10 capital)
    max_drawdown_usd: float = 50.0  # Hard USD floor
    min_signal_interval_s: float = 2.0  # Anti-spam between signals

    # ── Execution ────────────────────────────────────────────────
    mode: str = os.getenv("SNIPER_MODE", "PAPER").upper()  # PAPER | LIVE
    assets: tuple = ("BTC", "ETH", "XRP", "SOL")
    market_duration_min: int = 5
    hold_to_resolution: bool = True  # If true, hold to $1/$0 and ignore TP

    # ── Paper trading realism ────────────────────────────────────
    paper_slippage_base: float = 0.001     # Base taker slippage
    paper_slippage_spread_mult: float = 0.1  # Additional slippage per cent of spread
    paper_maker_fill_conservative: bool = True  # Require depth > shares at TP level

    # ── Dashboard ────────────────────────────────────────────────
    refresh_fps: int = 6
    no_dashboard: bool = False

    # ── XGBoost ML Model ─────────────────────────────────────────
    use_xgb_model: bool = str(os.getenv("SNIPER_USE_XGB", "1")).lower() in ['true', '1', 't', 'y', 'yes']  # Toggle ML scoring
    xgb_model_path: str = os.getenv("SNIPER_XGB_MODEL", "data/ml_models/v11_xgboost_model.json")
    xgb_min_confidence: float = 0.55  # Minimum XGB probability to accept signal
    xgb_unfiltered: bool = False      # Run XGBoost completely raw without any gates

    # ── Persistence & Logging ────────────────────────────────────
    trades_file: str = os.getenv("SNIPER_TRADES_FILE", "data/sniper_trades.json")
    log_file: str = os.getenv("SNIPER_LOG_FILE", "logs/sniper_bot.log")

    # ── API ───────────────────────────────────────────────────────
    gamma_api_base: str = "https://gamma-api.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # ── Asset definitions (slug patterns for Gamma discovery) ────
    asset_defs: dict = field(default_factory=lambda: {
        "BTC": {"slug_prefix": "btc-updown", "name": "Bitcoin"},
        "ETH": {"slug_prefix": "eth-updown", "name": "Ethereum"},
        "XRP": {"slug_prefix": "xrp-updown", "name": "Ripple"},
        "SOL": {"slug_prefix": "sol-updown", "name": "Solana"},
    })

    def tp_for_entry(self, entry_price: float) -> float:
        """Fallback TP when book analysis finds no wall."""
        # Modified to fixed 0.80 as requested
        return 0.80

    def apply_cli_overrides(self, **kwargs):
        """Apply CLI argument overrides."""
        for key, val in kwargs.items():
            if val is not None and hasattr(self, key):
                setattr(self, key, val)
        # Parse assets from comma string if needed
        if isinstance(self.assets, str):
            self.assets = tuple(a.strip().upper() for a in self.assets.split(","))
