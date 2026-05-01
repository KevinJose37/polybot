"""
scalper/display.py — Dashboard de terminal para el bot HFT.

Muestra en tiempo real:
  - Estado de mercados activos
  - Posiciones abiertas con P&L unrealized
  - Historial reciente de trades resueltos
  - Estadísticas de sesión
"""

import sys
from datetime import datetime, timezone


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _format_usd(amount: float) -> str:
    """Format USD amount."""
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def _format_pct(value: float) -> str:
    """Format percentage."""
    return f"{value:+.1f}%"


def _format_countdown(seconds: float) -> str:
    """Format seconds into MM:SS countdown."""
    if seconds <= 0:
        return "LIVE"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def _side_arrow(side: str) -> str:
    """Return directional indicator."""
    if side == "UP":
        return "▲ UP"
    elif side == "DOWN":
        return "▼ DOWN"
    return "— WAIT"


def _pnl_emoji(pnl: float) -> str:
    """Return emoji based on P&L."""
    if pnl > 0:
        return "🟢"
    elif pnl < 0:
        return "🔴"
    return "⚪"


# ═══════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════


def print_scalper_banner():
    """Print the HFT scalper banner."""
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║   ⚡  POLYMARKET HFT SCALPER  — Paper Trading Edition                       ║
║                                                                              ║
║   Strategy: Technical Signal Scalping (EMA + RSI + Momentum + VWAP)         ║
║   Markets:  5-Minute Crypto Up/Down (BTC, ETH, SOL, XRP)                   ║
║   Mode:     Paper Trading — No real orders executed                          ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """)


def print_session_header(stats: dict):
    """Print session statistics header."""
    capital = stats.get("capital", 0)
    pnl = stats.get("total_pnl", 0)
    pnl_pct = stats.get("pnl_pct", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    total = stats.get("total_resolved", 0)
    open_count = stats.get("open_count", 0)

    pnl_str = _format_usd(pnl)
    pnl_pct_str = _format_pct(pnl_pct)
    pnl_emoji = "📈" if pnl >= 0 else "📉"

    print(f"  ╔{'═' * 76}╗")
    print(f"  ║  💰 Capital: {_format_usd(capital):>10}  │  "
          f"{pnl_emoji} Session P&L: {pnl_str} ({pnl_pct_str})  │  "
          f"📊 Trades: {total} ({wins}W/{losses}L)  │  "
          f"📌 Open: {open_count}"
          f"{'':>1}║")
    print(f"  ╚{'═' * 76}╝")


def print_market_status(markets: dict, signals: dict):
    """
    Print active markets table.

    markets: dict keyed by asset with market data
    signals: dict keyed by asset with SignalResult
    """
    now = datetime.now(timezone.utc)

    print(f"\n  {'─' * 78}")
    print("  📡 ACTIVE MARKETS")
    print(f"  {'─' * 78}")
    print(f"  {'Asset':<6} {'Window':<28} {'Up$':>5} {'Dn$':>5} "
          f"{'Signal':>8} {'Dir':<8} {'⏱ Start':>8}")
    print(f"  {'─' * 78}")

    for asset in ["BTC", "ETH", "SOL", "XRP"]:
        mkt = markets.get(asset)
        sig = signals.get(asset)

        if not mkt:
            print(f"  {asset:<6} {'(no market found)':<28}")
            continue

        title = mkt.get("title", "")
        # Extract time window from title
        time_window = title.split(" - ")[-1] if " - " in title else title[:25]
        time_window = time_window[:26]

        up_price = mkt.get("up_price", 0.5)
        down_price = mkt.get("down_price", 0.5)

        if sig:
            signal_str = f"{sig.score:+.3f}"
            dir_str = _side_arrow(sig.direction)
        else:
            signal_str = "  —"
            dir_str = "— WAIT"

        time_to_start = mkt.get("time_to_start_sec", 0)
        countdown = _format_countdown(time_to_start)

        print(f"  {asset:<6} {time_window:<28} {up_price:>5.2f} {down_price:>5.2f} "
              f"{signal_str:>8} {dir_str:<8} {countdown:>8}")

    print(f"  {'─' * 78}")


def print_open_positions(positions: list[dict], markets_data: dict | None = None):
    """Print currently open positions with unrealized P&L."""
    if not positions:
        print("\n  📭 No hay posiciones abiertas.\n")
        return

    print(f"\n  {'─' * 78}")
    print("  📌 POSICIONES ABIERTAS")
    print(f"  {'─' * 78}")

    for pos in positions:
        asset = pos.get("asset", "?")
        side = pos.get("side", "?")
        entry = pos.get("entry_price", 0)
        stake = pos.get("stake", 0)
        shares = pos.get("shares", 0)

        # Try to get current price
        current = entry  # fallback
        if markets_data and asset in markets_data:
            mkt = markets_data[asset]
            if side == "UP":
                current = mkt.get("up_price", entry)
            else:
                current = mkt.get("down_price", entry)

        upnl = (current - entry) * shares
        change_pct = ((current - entry) / entry * 100) if entry > 0 else 0

        emoji = _pnl_emoji(upnl)

        print(f"  {emoji} {asset} {side:<4} @ {entry:.2f} → now {current:.2f} "
              f"({change_pct:+.1f}%) │ Stake {_format_usd(stake)} │ "
              f"uP&L {_format_usd(upnl)}")

    print(f"  {'─' * 78}")


def print_recent_trades(trades: list[dict], limit: int = 5):
    """Print last N resolved trades."""
    if not trades:
        print("\n  📋 Sin trades resueltos aún.\n")
        return

    print(f"\n  {'─' * 78}")
    print("  📋 ÚLTIMOS TRADES RESUELTOS")
    print(f"  {'─' * 78}")

    for trade in trades[:limit]:
        status = trade.get("status", "?")
        asset = trade.get("asset", "?")
        side = trade.get("side", "?")
        pnl = trade.get("pnl", 0)
        entry = trade.get("entry_price", 0)
        exit_p = trade.get("exit_price", 0)
        reason = trade.get("exit_reason", "")

        if status == "won":
            emoji = "✅"
            status_str = "WON "
        elif status == "lost":
            emoji = "❌"
            status_str = "LOST"
        elif status == "sold":
            emoji = "💰" if pnl >= 0 else "🔻"
            status_str = "SOLD"
        else:
            emoji = "⏳"
            status_str = "OPEN"

        # Extract time from event
        exit_time = trade.get("exit_time", "")
        if exit_time:
            try:
                dt = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M")
            except ValueError:
                time_str = "??:??"
        else:
            time_str = "??:??"

        reason_str = f" ({reason})" if reason else ""

        print(f"  {emoji} {asset} {side:<4}  {time_str}  "
              f"@ {entry:.2f} → {status_str}  {_format_usd(pnl):>8}"
              f"{reason_str}")

    print(f"  {'─' * 78}")


def print_cycle_separator(cycle_num: int):
    """Print separator between polling cycles."""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n  ⚡ ─── Ciclo #{cycle_num} │ {now} {'─' * 50}")


def print_trade_action(action: str, trade: dict):
    """Print a single trade action (entry, exit, resolution)."""
    asset = trade.get("asset", "?")
    side = trade.get("side", "?")
    pnl = trade.get("pnl", 0)

    if action == "entry":
        entry = trade.get("entry_price", 0)
        stake = trade.get("stake", 0)
        signal = trade.get("signal_score", 0)
        print(f"\n  🎯 ENTRY: {asset} {side} @ {entry:.4f} │ "
              f"Stake {_format_usd(stake)} │ Signal {signal:+.3f}")
    elif action == "resolved":
        won = pnl > 0 if pnl else False
        emoji = "✅" if won else "❌"
        print(f"\n  {emoji} RESOLVED: {asset} {side} → "
              f"{'WON' if won else 'LOST'} │ P&L {_format_usd(pnl or 0)}")
    elif action == "sold":
        exit_p = trade.get("exit_price", 0)
        reason = trade.get("exit_reason", "")
        print(f"\n  💰 SOLD: {asset} {side} @ {exit_p:.4f} │ "
              f"P&L {_format_usd(pnl or 0)} │ Reason: {reason}")


def print_no_signal_msg():
    """Print message when no signals meet threshold."""
    print("  ⏸️  Sin señales suficientes para entrar. Esperando...\n")


def print_session_stop():
    """Print session stop-loss message."""
    print("\n  🛑 SESSION STOP-LOSS ALCANZADO. Bot detenido.\n")


def print_error(msg: str):
    """Print an error message."""
    print(f"\n  ❌ Error: {msg}\n")


def print_hindsight(reviewed_trades: list[dict]):
    """
    Print hindsight analysis for trades that were sold early.

    Shows what would have happened if the trade was held to resolution.
    """
    if not reviewed_trades:
        return

    print(f"\n  {'─' * 78}")
    print("  🔮 HINDSIGHT ANALYSIS — ¿Fue buena decisión vender?")
    print(f"  {'─' * 78}")

    for trade in reviewed_trades:
        hs = trade.get("hindsight", {})
        if not hs:
            continue

        asset = trade.get("asset", "?")
        side = trade.get("side", "?")
        reason = trade.get("exit_reason", "?")
        actual_pnl = hs.get("actual_pnl", 0)
        held_pnl = hs.get("held_pnl", 0)
        decision = hs.get("decision", "?")
        diff = hs.get("difference", 0)
        resolution = hs.get("resolution", "?")
        would_have_won = hs.get("would_have_won", False)

        # Decision indicator
        if decision == "GOOD":
            verdict_emoji = "✅"
            verdict_text = "BUENA VENTA"
            diff_text = f"Te ahorró {_format_usd(abs(diff))}"
        elif decision == "BAD":
            verdict_emoji = "❌"
            verdict_text = "MALA VENTA"
            diff_text = f"Perdiste {_format_usd(abs(diff))} de ganancia"
        else:
            verdict_emoji = "➖"
            verdict_text = "NEUTRAL"
            diff_text = "Mismo resultado"

        held_result = f"{'WON' if would_have_won else 'LOST'}"

        print(f"\n  {verdict_emoji} {asset} {side} │ Razón: {reason}")
        print(f"     Vendido:    P&L {_format_usd(actual_pnl):>8}")
        print(f"     Si mantenía: {held_result} → P&L {_format_usd(held_pnl):>8}  (mercado resolvió {resolution})")
        print(f"     Veredicto:  {verdict_text} — {diff_text}")

    print(f"\n  {'─' * 78}")


def print_hindsight_summary(summary: dict | None):
    """
    Print aggregate hindsight comparison:
    Total P&L from selling early vs hypothetical P&L from holding.
    """
    if not summary:
        return

    count = summary["count"]
    actual = summary["total_actual_pnl"]
    held = summary["total_held_pnl"]
    diff = summary["difference"]
    good = summary["good_calls"]
    bad = summary["bad_calls"]
    accuracy = summary["accuracy"]

    if diff > 0:
        overall = "Vender fue MEJOR"
        emoji = "✅"
    elif diff < 0:
        overall = "Holdear era MEJOR"
        emoji = "❌"
    else:
        overall = "Mismo resultado"
        emoji = "➖"

    print(f"\n  ╔{'═' * 76}╗")
    print(f"  ║  🔮 SELL vs HOLD — Resumen de {count} ventas analizadas{'':<30}║")
    print(f"  ╠{'═' * 76}╣")
    print(f"  ║  💰 P&L vendiendo:    {_format_usd(actual):>10}{'':<47}║")
    print(f"  ║  📊 P&L si holdeaba:  {_format_usd(held):>10}{'':<47}║")
    print(f"  ║  {emoji} Diferencia:       {_format_usd(diff):>10}  →  {overall:<35}║")
    print(f"  ║  🎯 Precisión:        {accuracy:>5.0f}%     ({good} buenas / {bad} malas){'':<24}║")
    print(f"  ╚{'═' * 76}╝")
