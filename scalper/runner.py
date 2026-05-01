"""
scalper/runner.py — Loop principal del bot HFT de scalping.

Ciclo cada ~30 segundos:
  1. SCAN   → Descubrir mercados activos de 5m
  2. SIGNAL → Computar señales técnicas (Binance 1m klines)
  3. DECIDE → Entrar trades si |signal| > threshold
  4. MONITOR → Verificar posiciones abiertas (profit/reversal/resolution)
  5. DISPLAY → Actualizar dashboard en terminal
  6. SLEEP  → Esperar hasta el próximo ciclo
"""

import io
import logging
import time
import sys
from datetime import datetime, timezone

# ── Force UTF-8 on Windows for emoji and box-drawing ─────────
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from scalper.config import (
    HFT_ASSETS,
    HFT_POLL_INTERVAL,
    HFT_SIGNAL_THRESHOLD,
    HFT_STAKE,
)
from scalper.display import (
    print_cycle_separator,
    print_hindsight,
    print_hindsight_summary,
    print_market_status,
    print_no_signal_msg,
    print_open_positions,
    print_recent_trades,
    print_scalper_banner,
    print_session_header,
    print_session_stop,
    print_trade_action,
)
from scalper.market_scanner import scan_active_markets
from scalper.signals import compute_all_signals
from scalper.trader import (
    can_open_trade,
    check_open_positions,
    get_hindsight_summary,
    get_open_positions,
    get_recent_resolved,
    get_session_stats,
    load_trades,
    open_trade,
    review_sold_trades,
)

logger = logging.getLogger("polybot.scalper.runner")


def _is_in_entry_window(market: dict) -> bool:
    """
    Check if a market is tradeable right now.

    Entry is allowed when:
    1. Market is UPCOMING and starts within 5 minutes (300s)
    2. Market is IN PROGRESS and less than 3.5 min has elapsed
       (gives us time before the 5-min window closes)
    3. Market must be accepting orders
    """
    if not market.get("accepting_orders", False):
        return False

    time_to_start = market.get("time_to_start_sec", 99999)
    is_in_progress = market.get("is_in_progress", False)

    # If market is currently in progress, enter in the first 3.5 minutes
    if is_in_progress:
        event_start = market.get("event_start")
        if event_start:
            now = datetime.now(timezone.utc)
            elapsed = (now - event_start).total_seconds()
            return elapsed < 210  # First 3.5 minutes of the 5-minute window
        return False

    # Upcoming market: enter if it starts within 5 minutes
    return 0 < time_to_start <= 300


def _run_single_cycle(
    cycle_num: int,
    target_assets: dict | None = None,
) -> bool:
    """
    Execute a single polling cycle.

    Returns False if session stop-loss is hit (should stop).
    """
    assets = target_assets or HFT_ASSETS
    trades = load_trades()

    print_cycle_separator(cycle_num)

    # ── 1. SCAN: Find active markets ─────────────────────────
    try:
        markets = scan_active_markets(assets)
    except Exception as exc:
        logger.error("Market scan failed: %s", exc)
        markets = {}

    if not markets:
        print("  📡 No se encontraron mercados activos. Esperando...\n")

    # ── 2. SIGNAL: Compute technical signals ─────────────────
    try:
        signals = compute_all_signals(assets)
    except Exception as exc:
        logger.error("Signal computation failed: %s", exc)
        signals = {}

    # ── 3. Display market status ─────────────────────────────
    print_market_status(markets, signals)

    # ── 4. Check session stats ───────────────────────────────
    stats = get_session_stats(trades)
    print_session_header(stats)

    # Check stop-loss
    can_trade, reason = can_open_trade(trades)
    if not can_trade and "stop-loss" in reason.lower():
        print_session_stop()
        return False

    # ── 5. MONITOR: Check open positions ─────────────────────
    signal_scores = {}
    for asset_key, sig in signals.items():
        signal_scores[asset_key] = sig.score

    actions = check_open_positions(signal_scores)
    for action in actions:
        print_trade_action(action["type"], action["trade"])

    # ── 6. DECIDE: Open new trades ───────────────────────────
    entries_made = 0

    for asset_key in assets:
        if asset_key not in markets or asset_key not in signals:
            continue

        market = markets[asset_key]
        signal = signals[asset_key]

        # Check if signal is strong enough
        if abs(signal.score) < HFT_SIGNAL_THRESHOLD:
            continue

        # Check if we're in the entry window
        if not _is_in_entry_window(market):
            continue

        # Check if we can open more trades
        ok, reason = can_open_trade()
        if not ok:
            logger.debug("Cannot trade %s: %s", asset_key, reason)
            continue

        # Determine side and entry price
        side = signal.direction  # "UP" or "DOWN"
        if side == "NEUTRAL":
            continue

        # Entry price: buy at the bestAsk for the CORRECT side
        if side == "UP":
            entry_price = market.get("up_best_ask", market.get("up_price", 0.5))
            if entry_price <= 0:
                entry_price = market.get("up_price", 0.5) + 0.01
        else:
            entry_price = market.get("down_best_ask", market.get("down_price", 0.5))
            if entry_price <= 0:
                entry_price = market.get("down_price", 0.5) + 0.01

        # Sanity check: don't buy at extreme prices
        if entry_price >= 0.95 or entry_price <= 0.05:
            logger.debug("Skipping %s: entry price %.4f too extreme", asset_key, entry_price)
            continue

        # Open the trade
        trade = open_trade(
            asset=asset_key,
            side=side,
            entry_price=entry_price,
            signal_score=signal.score,
            market_slug=market["slug"],
            gamma_id=market["gamma_id"],
            event_start=market["event_start"],
            event_end=market["event_end"],
        )

        if trade:
            print_trade_action("entry", trade)
            entries_made += 1

    if entries_made == 0 and not actions:
        print_no_signal_msg()

    # ── 7. Display positions and history ─────────────────────
    open_pos = get_open_positions()
    print_open_positions(open_pos, markets)

    recent = get_recent_resolved(limit=5)
    print_recent_trades(recent)

    # ── 8. HINDSIGHT: Review sold trades after market closes ─
    hindsight_results = review_sold_trades()
    print_hindsight(hindsight_results)

    # ── 9. HINDSIGHT SUMMARY: Aggregate sell vs hold ────────
    hs_summary = get_hindsight_summary()
    print_hindsight_summary(hs_summary)

    return True


def run_scalper(
    target_assets: dict | None = None,
    max_cycles: int | None = None,
):
    """
    Main entry point for the HFT scalper bot.

    Runs continuously, polling every HFT_POLL_INTERVAL seconds.
    Press Ctrl+C to stop gracefully.

    Args:
        target_assets: Dict of assets to trade (default: all from config)
        max_cycles: Maximum cycles to run (None = infinite)
    """
    print_scalper_banner()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"  ⏰ Started: {now_str}")
    print(f"  🎯 Signal Threshold: {HFT_SIGNAL_THRESHOLD}")
    print(f"  💵 Stake per trade: ${HFT_STAKE:.2f}")
    print(f"  🔄 Poll interval: {HFT_POLL_INTERVAL}s")

    assets_str = ", ".join((target_assets or HFT_ASSETS).keys())
    print(f"  📊 Assets: {assets_str}")
    print(f"\n  ▶️  Bot en ejecución. Presiona Ctrl+C para detener.\n")

    cycle = 0

    try:
        while True:
            cycle += 1

            if max_cycles and cycle > max_cycles:
                print(f"\n  ⏹️  Máximo de ciclos ({max_cycles}) alcanzado.\n")
                break

            should_continue = _run_single_cycle(cycle, target_assets)
            if not should_continue:
                break

            # Sleep between cycles
            print(f"\n  💤 Próximo ciclo en {HFT_POLL_INTERVAL}s...")
            time.sleep(HFT_POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n  ⛔ Bot detenido por el usuario.\n")

    # Final report
    print(f"\n{'═' * 80}")
    print("  📊 REPORTE FINAL DE SESIÓN")
    print(f"{'═' * 80}")

    stats = get_session_stats()
    print_session_header(stats)

    recent = get_recent_resolved(limit=20)
    print_recent_trades(recent, limit=20)

    print(f"\n  Trades guardados en: {__import__('scalper.config', fromlist=['HFT_TRADES_FILE']).HFT_TRADES_FILE}")
    print(f"  Total ciclos ejecutados: {cycle}\n")
