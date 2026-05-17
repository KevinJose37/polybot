"""
sniper_bot/main.py — Async orchestrator.

Connects all components:
  scanner → ws_manager → signal_engine → executor → positions → dashboard

Usage:
    python -m sniper_bot
    python -m sniper_bot --capital 500 --stake 10
    python -m sniper_bot --live
    python -m sniper_bot --assets BTC,ETH
    python -m sniper_bot --no-dashboard
"""
import asyncio
import argparse
import logging
import sys
import os
import time

# Fix Windows encoding
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from .config import SniperConfig
from .ws_manager import OrderbookManager
from .scanner import scan_markets
from .lifecycle import MarketLifecycleManager
from .signal_engine import SignalEngine
from .executor import Executor
from .positions import PositionManager
from .circuit_breaker import CircuitBreaker

# Logging is configured in main() after parsing args
logger = logging.getLogger("sniper_bot.main")


async def scan_loop(config: SniperConfig, ws_mgr: OrderbookManager,
                    lifecycle: MarketLifecycleManager,
                    signal_engine: SignalEngine) -> None:
    """Periodic market discovery + token subscription loop."""
    last_prefetch = 0

    while True:
        try:
            # Discover active markets
            markets = await asyncio.to_thread(scan_markets, config)
            all_tokens = []

            for asset, info in markets.items():
                # Register lifecycle
                lifecycle.register_market(
                    asset=asset,
                    market_id=info.slug or info.condition_id,
                    start_time=info.event_start,
                    end_time=info.event_end,
                )
                # Register tokens for signal engine
                signal_engine.register_tokens(asset, info.up_token_id, info.down_token_id)

                if info.up_token_id:
                    all_tokens.append(info.up_token_id)
                if info.down_token_id:
                    all_tokens.append(info.down_token_id)

            # Subscribe to WS with current slot tokens ONLY
            if all_tokens:
                # Force resubscribe if needed (ws_manager handles idempotency)
                await ws_mgr.subscribe(all_tokens)

            # Cleanup resolved markets
            lifecycle.cleanup_resolved()

        except Exception as e:
            logger.error("Scan loop error: %s", e)

        await asyncio.sleep(1.0)  # Re-scan every 1 second (cache protects API)


async def fill_check_loop(executor: Executor) -> None:
    """Periodic fill checker for live mode (CLOB API polling)."""
    while True:
        try:
            executor.check_maker_fills()
        except Exception as e:
            logger.error("Fill check error: %s", e)
        await asyncio.sleep(5)


async def run(config: SniperConfig) -> None:
    """Main async entry point."""
    os.makedirs("logs", exist_ok=True)

    # ── Initialize components ────────────────────────────────
    ws_mgr = OrderbookManager(config.ws_url)
    lifecycle = MarketLifecycleManager(
        entry_window_s=config.entry_window_s,
    )
    positions = PositionManager(trades_file=config.trades_file)
    circuit_breaker = CircuitBreaker(
        max_consecutive_losses=config.max_consecutive_losses,
        max_drawdown_pct=config.max_drawdown_pct,
        max_drawdown_usd=config.max_drawdown_usd,
        min_signal_interval_s=config.min_signal_interval_s,
        max_open_positions=config.max_concurrent,
    )

    # ── XGBoost ML (optional) ────────────────────────────────
    xgb_scorer = None
    xgb_features = None
    if config.use_xgb_model:
        try:
            from .xgb_scorer import XGBScorer
            from .xgb_features import FeatureAccumulator
            xgb_scorer = XGBScorer()
            if xgb_scorer.is_loaded:
                xgb_features = FeatureAccumulator(ws_mgr)
                logger.info("XGBoost ML scoring ENABLED (assets: %s)", list(xgb_scorer.models.keys()))
            else:
                xgb_scorer = None
                logger.warning("XGBoost model failed to load — falling back to heuristic")
        except Exception as e:
            logger.warning("XGBoost init failed: %s — falling back to heuristic", e)
            xgb_scorer = None
            xgb_features = None

    signal_engine = SignalEngine(config, ws_mgr, lifecycle,
                                 xgb_scorer=xgb_scorer, xgb_features=xgb_features)
    executor = Executor(config, ws_mgr, positions, lifecycle, circuit_breaker)

    # ── Wire callbacks ───────────────────────────────────────
    # WS tick → feature accumulator (must be FIRST to have features ready)
    if xgb_features:
        ws_mgr.on_book_update(xgb_features.on_book_tick)
    # WS tick → signal engine (detect imbalances)
    ws_mgr.on_book_update(signal_engine.on_book_tick)
    # WS tick → executor (check maker fills on every tick for paper mode)
    ws_mgr.on_book_update(executor.on_book_tick_for_fills)
    # Signal accepted → executor (open position)
    signal_engine.on_signal(executor.on_signal)
    # Signal rejected → executor (log skipped ghost trade)
    signal_engine.on_rejected_signal(executor.on_rejected_signal)

    # ── Print startup banner ─────────────────────────────────
    ml_status = "XGB ✓" if (xgb_scorer and xgb_scorer.is_loaded) else "Heuristic"
    print("=" * 60)
    print(f"  SNIPER BOT | Mode: {config.mode} | Capital: ${config.capital}")
    print(f"  Stake: ${config.stake} | Assets: {', '.join(config.assets)}")
    print(f"  Trigger: [{config.trigger_low}, {config.trigger_high}]")
    print(f"  Max spread: {config.max_spread} | Min depth: {config.min_depth}")
    print(f"  Scoring: {ml_status} | Min confidence: {config.xgb_min_confidence}")
    print("=" * 60)

    # ── Launch tasks ─────────────────────────────────────────
    tasks = [
        asyncio.create_task(ws_mgr.run(), name="ws_manager"),
        asyncio.create_task(scan_loop(config, ws_mgr, lifecycle, signal_engine), name="scanner"),
        asyncio.create_task(fill_check_loop(executor), name="fill_checker"),
        asyncio.create_task(signal_engine.run_batcher(), name="signal_batcher"),
    ]

    if not config.no_dashboard:
        from .dashboard import Dashboard
        dashboard = Dashboard(
            config, ws_mgr, lifecycle, signal_engine,
            executor, positions, circuit_breaker,
        )
        tasks.append(asyncio.create_task(dashboard.run(), name="dashboard"))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        ws_mgr.stop()
        logger.info("Sniper bot shutdown complete")


def parse_args() -> SniperConfig:
    parser = argparse.ArgumentParser(description="Sniper Bot — CLOB Microstructure Scalper")
    parser.add_argument("--capital", type=float, help="Starting capital USD")
    parser.add_argument("--stake", type=float, help="Per-trade stake USD")
    parser.add_argument("--assets", type=str, help="Comma-separated assets (BTC,ETH,XRP)")
    parser.add_argument("--live", action="store_true", help="Enable LIVE trading")
    parser.add_argument("--no-dashboard", action="store_true", help="Headless mode")
    parser.add_argument("--trigger-low", type=float, dest="trigger_low")
    parser.add_argument("--trigger-high", type=float, dest="trigger_high")
    parser.add_argument("--max-spread", type=float, dest="max_spread")
    parser.add_argument("--use-xgb", type=lambda x: str(x).lower() in ['true', '1', 't', 'y', 'yes'], help="Enable/disable XGBoost (1/0, true/false)")
    parser.add_argument("--unfiltered", action="store_true", help="Run XGBoost purely on its own confidence, ignoring all filters")
    parser.add_argument("--trades-file", type=str, help="Custom trades JSON file path")
    parser.add_argument("--log-file", type=str, help="Custom log file path")
    args = parser.parse_args()

    config = SniperConfig()
    overrides = {}
    if args.capital is not None:
        overrides["capital"] = args.capital
    if args.stake is not None:
        overrides["stake"] = args.stake
    if args.assets is not None:
        overrides["assets"] = args.assets
    if args.live:
        overrides["mode"] = "LIVE"
    if args.no_dashboard:
        overrides["no_dashboard"] = True
    if args.trigger_low is not None:
        overrides["trigger_low"] = args.trigger_low
    if args.trigger_high is not None:
        overrides["trigger_high"] = args.trigger_high
    if args.max_spread is not None:
        overrides["max_spread"] = args.max_spread
    if args.use_xgb is not None:
        overrides["use_xgb_model"] = args.use_xgb
    if args.unfiltered:
        overrides["xgb_unfiltered"] = True
    if args.trades_file is not None:
        overrides["trades_file"] = args.trades_file
    if args.log_file is not None:
        overrides["log_file"] = args.log_file

    config.apply_cli_overrides(**overrides)
    return config


def main():
    config = parse_args()
    
    # Configure logging with the specified log file
    os.makedirs(os.path.dirname(config.log_file) or ".", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(config.log_file, encoding="utf-8"),
        ],
    )
    
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        print("\nSniper bot stopped.")


if __name__ == "__main__":
    main()
