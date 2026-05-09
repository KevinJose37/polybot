"""
scalper/runner_v10.py — V10 Universal Trend Scalper Main Loop.

Independent runner for V10 (separate from the crypto 5min runner).
Runs on a longer cycle (~5 minutes) and manages a portfolio of up to
10 positions across ALL Polymarket markets.

Cycle:
1. Check exits on open positions (TP/SL/time-stop)
2. Scan markets for new momentum opportunities
3. Open new positions if slots available
4. Print dashboard
"""
import logging
import time
from datetime import datetime

from scalper.universal_scanner import get_trending_markets
from scalper.signals_v10 import compute_signals_v10
from scalper.trader_v10 import (
    check_all_exits,
    get_open_positions,
    get_portfolio_stats,
    open_trend_trade,
    MAX_OPEN_POSITIONS,
    STAKE_PER_TRADE,
)

logger = logging.getLogger("polybot.v10.runner")

# ── Configuration ──────────────────────────────────────────────
CYCLE_INTERVAL_SEC = 300       # 5 minutes between cycles
MAX_NEW_ENTRIES_PER_CYCLE = 2  # Don't enter more than 2 per cycle


def _print_banner():
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║   🌐  POLYMARKET V10 — Universal Trend Scalper                              ║
║                                                                              ║
║   Strategy: Momentum-based trend following across ALL markets               ║
║   Markets:  Politics, Sports, Entertainment, News, Crypto (non-5m/15m)     ║
║   Mode:     Paper Trading — No real orders executed                          ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """)


def _print_dashboard(stats: dict, cycle: int):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"""
  ╔════════════════════════════════════════════════════════════════════════════╗
  ║  🌐 V10 Cycle #{cycle} | {now}                                            ║
  ║  📊 Open: {stats['open_positions']}/{MAX_OPEN_POSITIONS} | """
          f"""Exposure: ${stats['open_exposure']:.2f} | """
          f"""P&L: ${stats['total_pnl']:+.2f} | """
          f"""Trades: {stats['total_resolved']} ({stats['wins']}W/{stats['losses']}L) """
          f"""WR={stats['win_rate']:.0f}%  ║
  ╚════════════════════════════════════════════════════════════════════════════╝""")


def run_v10_cycle(cycle_num: int, stake: float = STAKE_PER_TRADE) -> bool:
    """
    Execute a single V10 cycle.
    Returns True to continue, False to stop.
    """
    print(f"\n  ⚡ ─── V10 Cycle #{cycle_num} │ {datetime.now().strftime('%H:%M:%S')} "
          f"──────────────────────────────────────────")

    # ── Step 1: Check exits on open positions ─────────────────
    open_pos = get_open_positions()
    if open_pos:
        print(f"\n  📋 Checking {len(open_pos)} open positions...")
        closed = check_all_exits()
        if closed:
            print(f"  → Closed {len(closed)} positions this cycle")
    else:
        print(f"\n  📭 No open positions.")

    # ── Step 2: Scan for new opportunities ────────────────────
    open_count = len(get_open_positions())  # Refresh after exits
    available_slots = MAX_OPEN_POSITIONS - open_count

    if available_slots <= 0:
        print(f"\n  ⏸️  All {MAX_OPEN_POSITIONS} slots filled. Skipping scan.")
    else:
        print(f"\n  🔍 Scanning markets... ({available_slots} slots available)")
        trending = get_trending_markets(max_results=10)

        if not trending:
            print(f"  ⏸️  No trending markets found this cycle.")
        else:
            # Generate signals
            signals = compute_signals_v10(trending)
            print(f"  [V10-SIGNAL] {len(signals)} signals generated from {len(trending)} trending markets")

            # Log top signals
            for i, sig in enumerate(signals[:5]):
                q = sig["question"][:50]
                icon = "✅" if sig["quality"] == "CONFIRMED" else "⚠️"
                print(
                    f"    {icon} #{i+1}: {q}\n"
                    f"       {sig['side']} @${sig['entry_price']:.2f} | "
                    f"1h={sig['momentum_1h']:+.3f} 1d={sig['momentum_1d']:+.3f} | "
                    f"Score={sig['adjusted_score']:.3f} | "
                    f"liq=${sig['liquidity']:.0f} spr={sig['spread']:.3f}"
                )

            # Open new positions (max N per cycle)
            entries = 0
            for sig in signals:
                if entries >= MAX_NEW_ENTRIES_PER_CYCLE:
                    break
                if entries >= available_slots:
                    break

                trade = open_trend_trade(sig, stake=stake)
                if trade:
                    entries += 1

    # ── Step 3: Dashboard ─────────────────────────────────────
    stats = get_portfolio_stats()
    _print_dashboard(stats, cycle_num)

    return True


def run_v10_scalper(
    capital: float = 20.0,
    stake: float = STAKE_PER_TRADE,
    interval: int = CYCLE_INTERVAL_SEC,
):
    """
    Main entry point for V10 Universal Trend Scalper.
    
    Runs continuously, scanning markets every `interval` seconds.
    """
    _print_banner()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"  ⏰ Started: {now_str}")
    print(f"  🌐 Universe: ALL Polymarket markets (excl. crypto 5m/15m)")
    print(f"  💵 Stake per trade: ${stake:.2f}")
    print(f"  📊 Max positions: {MAX_OPEN_POSITIONS}")
    print(f"  🔄 Scan interval: {interval}s ({interval//60}min)")
    print(f"  🎯 TP: +10% | SL: -25% | Time-stop: 7 days")
    print(f"\n  ▶️  Bot en ejecución. Presiona Ctrl+C para detener.\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                should_continue = run_v10_cycle(cycle, stake=stake)
                if not should_continue:
                    break
            except Exception as exc:
                print(f"  ❌ Cycle error: {exc}")
                logger.exception("V10 cycle %d failed", cycle)

            print(f"\n  💤 Next scan in {interval}s...")
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  ⏹️  V10 stopped by user after {cycle} cycles.")
        stats = get_portfolio_stats()
        print(f"  📊 Final: {stats['total_resolved']} trades, "
              f"WR={stats['win_rate']:.0f}%, P&L=${stats['total_pnl']:+.2f}")
