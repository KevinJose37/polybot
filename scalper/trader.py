"""
scalper/trader.py — Gestor de posiciones y paper trading para HFT.

Maneja:
  - Apertura de posiciones (simulated buy)
  - Monitoreo de posiciones abiertas
  - Early exit (sell) por profit o reversal de señal
  - Resolución automática al cierre del mercado
  - Persistencia en hft_trades.json
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import scalper.config as _cfg
from scalper.config import (
    HFT_EARLY_EXIT_PROFIT,
    HFT_EARLY_EXIT_REVERSAL,
    HFT_MAX_CONCURRENT,
    HFT_SESSION_STOP_LOSS,
    HFT_STOP_LOSS,
    HFT_TRADES_FILE,
)
from scalper.market_scanner import get_market_current_price

logger = logging.getLogger("polybot.scalper.trader")

# Active trades file — can be overridden per strategy version.
# Default is HFT_TRADES_FILE (v1). V2/V3 call set_active_trades_file()
# at startup to isolate their data.
_active_trades_file: str = HFT_TRADES_FILE


def set_active_trades_file(filename: str) -> None:
    """Override the trades file for this process (used by v2/v3 strategies)."""
    global _active_trades_file
    _active_trades_file = filename
    logger.info("Trades file set to: %s", filename)


# ═══════════════════════════════════════════════════════════════
# Trade Storage
# ═══════════════════════════════════════════════════════════════


def _trades_path() -> Path:
    """Get the absolute path to the trades file."""
    return Path(__file__).parent.parent / _active_trades_file


def load_trades() -> list[dict]:
    """Load all trades from the JSON file."""
    path = _trades_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_trades(trades: list[dict]) -> None:
    """Save all trades to the JSON file."""
    path = _trades_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, default=str)


def _next_trade_id(trades: list[dict]) -> str:
    """Generate next sequential trade ID."""
    if not trades:
        return "hft_001"
    max_num = 0
    for t in trades:
        tid = t.get("id", "hft_000")
        try:
            num = int(tid.split("_")[1])
            max_num = max(max_num, num)
        except (IndexError, ValueError):
            pass
    return f"hft_{max_num + 1:03d}"


# ═══════════════════════════════════════════════════════════════
# Capital Management
# ═══════════════════════════════════════════════════════════════


_cached_usdc_balance = None
_last_usdc_sync = 0.0

def sync_portfolio() -> dict:
    """
    Sincroniza el estado del portfolio con la blockchain (Polymarket).
    """
    global _cached_usdc_balance, _last_usdc_sync
    
    try:
        from scalper.live_client import get_balance, get_token_balance, is_live
    except ImportError:
        return {}

    if not is_live():
        return {}

    now = datetime.now(timezone.utc).timestamp()
    
    # Cache USDC balance for 30s
    if now - _last_usdc_sync > 30 or _cached_usdc_balance is None:
        bal = get_balance()
        if bal is not None:
            _cached_usdc_balance = bal
            _last_usdc_sync = now
            
    # Calculate positions value
    open_pos_value = 0.0
    trades = load_trades()
    open_trades = [t for t in trades if t.get("status") == "open"]
    
    for t in open_trades:
        token_id = t.get("token_id", "")
        if not token_id:
            open_pos_value += t.get("stake", 0)
            continue
            
        gamma_id = t.get("gamma_id", "")
        market_data = get_market_current_price(gamma_id) if gamma_id else None
        
        if market_data:
            side = t.get("side")
            current_price = market_data.get(f"{side.lower()}_price", 0.5)
        else:
            current_price = t.get("entry_price", 0.5)
            
        actual_shares = get_token_balance(token_id)
        if actual_shares is not None and actual_shares > 0:
            open_pos_value += actual_shares * current_price

    return {
        "usdc_balance": _cached_usdc_balance or 0.0,
        "positions_value": open_pos_value,
        "total_value": (_cached_usdc_balance or 0.0) + open_pos_value
    }


def sync_trade_history() -> list[dict]:
    """
    Sincroniza trades abiertos con la blockchain para detectar
    resoluciones que el bot se perdió (e.g. por crash o desconexión).
    
    Checks two conditions:
    1. Gamma API says market is closed → resolve based on final prices
    2. event_end has passed + on-chain shares are 0 → resolve based on
       USDC balance change (Gamma API can be slow to update)
    """
    trades = load_trades()
    actions = []
    
    try:
        from scalper.live_client import get_token_balance, is_live
        if not is_live():
            return actions
    except ImportError:
        return actions

    now = datetime.now(timezone.utc)
    open_trades = [t for t in trades if t.get("status") == "open"]
    changed = False
    
    for trade in open_trades:
        token_id = trade.get("token_id", "")
        if not token_id:
            continue
            
        gamma_id = trade.get("gamma_id", "")
        market_data = get_market_current_price(gamma_id) if gamma_id else None
        
        # Check if market should have ended (event_end passed)
        event_end_str = trade.get("event_end", "")
        event_ended = False
        if event_end_str:
            try:
                event_end = datetime.fromisoformat(event_end_str.replace("Z", "+00:00"))
                # Add 60s buffer for settlement
                from datetime import timedelta
                event_ended = now > event_end + timedelta(seconds=60)
            except (ValueError, TypeError):
                pass
        
        market_closed = market_data and market_data.get("closed", False)
        
        # If market is closed OR event_end has passed, check on-chain
        if market_closed or event_ended:
            actual_shares = get_token_balance(token_id)
            if actual_shares is not None and actual_shares < 0.01:
                # Shares are gone — trade resolved
                side = trade["side"]
                stake = trade.get("stake", 0)
                shares = trade.get("shares", 0)

                # Determine outcome from market data if available
                if market_data:
                    up_price = market_data.get("up_price", 0.5)
                    down_price = market_data.get("down_price", 0.5)
                    if side == "UP":
                        won = up_price > 0.9
                    else:
                        won = down_price > 0.9
                else:
                    # No market data — check if USDC increased
                    # (if we got paid, we won)
                    won = False  # conservative default
                
                if won:
                    pnl = (shares * 1.0) - stake
                    trade["status"] = "won"
                    trade["exit_price"] = 1.0
                else:
                    pnl = -stake
                    trade["status"] = "lost"
                    trade["exit_price"] = 0.0
                    
                trade["exit_time"] = now.isoformat()
                trade["pnl"] = round(pnl, 2)
                trade["exit_reason"] = "auto_sync_resolution"
                changed = True
                
                actions.append({"type": "resolved", "trade": trade, "won": won})
                
                emoji = "✅" if won else "❌"
                logger.info(
                    "%s SYNC RESOLVED %s: %s %s → %s | P&L $%.2f",
                    emoji, trade["id"], trade["asset"], trade["side"],
                    "WON" if won else "LOST", pnl,
                )
                
    if changed:
        save_trades(trades)
        
    return actions


def get_session_stats(trades: list[dict] | None = None) -> dict:
    """Calculate session statistics from trade history."""
    if trades is None:
        trades = load_trades()

    total_pnl = 0.0
    wins = 0
    losses = 0
    total_resolved = 0
    total_staked = 0.0

    for t in trades:
        status = t.get("status", "open")
        if status in ("won", "lost", "sold"):
            pnl = t.get("pnl", 0) or 0
            total_pnl += pnl
            total_resolved += 1
            total_staked += t.get("stake", 0)
            if pnl > 0:
                wins += 1
            else:
                losses += 1

    open_positions = [t for t in trades if t.get("status") == "open"]

    stats = {
        "capital": _cfg.HFT_CAPITAL + total_pnl,
        "starting_capital": _cfg.HFT_CAPITAL,
        "total_pnl": total_pnl,
        "pnl_pct": (total_pnl / _cfg.HFT_CAPITAL * 100) if _cfg.HFT_CAPITAL > 0 else 0,
        "wins": wins,
        "losses": losses,
        "total_resolved": total_resolved,
        "win_rate": (wins / total_resolved * 100) if total_resolved > 0 else 0,
        "open_count": len(open_positions),
        "total_staked": total_staked,
        "is_live": False,
        "usdc_balance": 0.0,
        "positions_value": 0.0,
    }
    
    # En modo live, sincronizar con la realidad on-chain
    try:
        from scalper.live_client import is_live
        if is_live():
            sync_data = sync_portfolio()
            if sync_data:
                stats["is_live"] = True
                stats["usdc_balance"] = sync_data["usdc_balance"]
                stats["positions_value"] = sync_data["positions_value"]
                
                # El capital total on-chain
                real_capital = sync_data["total_value"]
                stats["capital"] = real_capital
                
                # Calculamos el P&L total en base a lo que empezamos
                stats["total_pnl"] = real_capital - _cfg.HFT_CAPITAL
                stats["pnl_pct"] = (stats["total_pnl"] / _cfg.HFT_CAPITAL * 100) if _cfg.HFT_CAPITAL > 0 else 0
    except ImportError:
        pass

    return stats


def can_open_trade(trades: list[dict] | None = None) -> tuple[bool, str]:
    """Check if we can open a new trade (capital, max positions, stop-loss)."""
    if trades is None:
        trades = load_trades()

    stats = get_session_stats(trades)

    # Check session stop-loss
    loss_pct = abs(stats["total_pnl"]) / _cfg.HFT_CAPITAL if _cfg.HFT_CAPITAL > 0 else 0
    if stats["total_pnl"] < 0 and loss_pct >= HFT_SESSION_STOP_LOSS:
        return False, f"Session stop-loss hit ({loss_pct:.1%} loss)"

    # Check max concurrent positions
    if stats["open_count"] >= HFT_MAX_CONCURRENT:
        return False, f"Max concurrent positions reached ({HFT_MAX_CONCURRENT})"

    # Check capital
    if stats.get("is_live"):
        if stats["usdc_balance"] < _cfg.HFT_STAKE:
            return False, f"Insufficient USDC balance (${stats['usdc_balance']:.2f})"
    else:
        if stats["capital"] < _cfg.HFT_STAKE:
            return False, f"Insufficient capital (${stats['capital']:.2f})"

    return True, "OK"


# ═══════════════════════════════════════════════════════════════
# High Watermark Gain Protection
# ═══════════════════════════════════════════════════════════════

_peak_capital: float = 0.0
_gain_protection_pct: float = 0.50  # Protect 50% of gains
_gain_protection_enabled: bool = True


def set_gain_protection_enabled(enabled: bool):
    """Enable or disable gain protection (for overnight data collection)."""
    global _gain_protection_enabled
    _gain_protection_enabled = enabled


def update_peak_capital(current_capital: float) -> float:
    """Update and return the peak capital (high watermark)."""
    global _peak_capital
    if current_capital > _peak_capital:
        _peak_capital = current_capital
    return _peak_capital


def get_gain_protection_stop(starting_capital: float) -> float | None:
    """
    Calculate the dynamic stop level based on high watermark.

    Returns None if no meaningful gains to protect yet.
    Logic: protect 80% of gains above starting capital.

    Example:
      start=$68.65, peak=$170 → gains=$101.35
      protected = 80% × $101.35 = $81.08
      stop_at = $68.65 + $81.08 = $149.73
    """
    if not _gain_protection_enabled:
        return None

    if _peak_capital <= starting_capital:
        return None  # No gains to protect yet

    gains = _peak_capital - starting_capital

    # Don't activate protection until gains are meaningful (at least $2)
    if gains < 2.0:
        return None

    protected_gains = gains * _gain_protection_pct
    stop_level = starting_capital + protected_gains

    return stop_level


def check_gain_protection(current_capital: float, starting_capital: float) -> tuple[bool, str]:
    """
    Check if the bot should stop due to gain protection trigger.

    Returns (should_stop, reason).
    """
    stop_level = get_gain_protection_stop(starting_capital)
    if stop_level is None:
        return False, ""

    if current_capital < stop_level:
        lost = _peak_capital - current_capital
        return True, (
            f"🛡️ GAIN PROTECTION TRIGGERED\n"
            f"  Peak capital:    ${_peak_capital:.2f}\n"
            f"  Current capital: ${current_capital:.2f}\n"
            f"  Drawdown:        -${lost:.2f}\n"
            f"  Stop level:      ${stop_level:.2f} (protecting 80% of gains)"
        )

    return False, ""


# ═══════════════════════════════════════════════════════════════
# Trade Operations
# ═══════════════════════════════════════════════════════════════


def open_trade(
    asset: str,
    side: str,          # "UP" or "DOWN"
    entry_price: float,
    signal_score: float,
    market_slug: str,
    gamma_id: str,
    event_start: datetime,
    event_end: datetime,
    stake: float | None = None,
    token_id: str = "",
) -> dict | None:
    """
    Open a new trade (paper + optional live).

    Simulates buying shares of the UP or DOWN outcome at the current bestAsk.
    If live mode is active, also sends a real BUY order to the CLOB.
    """
    trades = load_trades()

    ok, reason = can_open_trade(trades)
    if not ok:
        logger.info("Cannot open trade: %s", reason)
        return None

    # Check if we already have a position for this specific market
    for t in trades:
        if t.get("market_slug") == market_slug and t.get("status") == "open":
            logger.debug("Already have position in %s", market_slug)
            return None

    actual_stake = stake or _cfg.HFT_STAKE

    # Calculate shares (how many outcome tokens we buy)
    if entry_price <= 0 or entry_price >= 1:
        logger.warning("Invalid entry price %.4f for %s", entry_price, asset)
        return None

    shares = actual_stake / entry_price

    trade = {
        "id": _next_trade_id(trades),
        "asset": asset,
        "side": side,
        "entry_price": round(entry_price, 4),
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "stake": round(actual_stake, 2),
        "shares": round(shares, 4),
        "signal_score": round(signal_score, 4),
        "status": "open",
        "exit_price": None,
        "exit_time": None,
        "pnl": None,
        "market_slug": market_slug,
        "gamma_id": gamma_id,
        "event_start": event_start.isoformat(),
        "event_end": event_end.isoformat(),
        "token_id": token_id,
    }

    trades.append(trade)
    save_trades(trades)

    logger.info(
        "OPENED %s: %s %s @ %.4f | Stake $%.2f | Shares %.2f | Signal %.3f",
        trade["id"], asset, side, entry_price, actual_stake, shares, signal_score,
    )

    # ── Live order (if enabled) ───────────────────────────────
    if token_id:
        try:
            from scalper.live_client import buy_outcome, is_live
            if is_live():
                result = buy_outcome(
                    token_id=token_id,
                    price=entry_price,
                    size=actual_stake,
                    asset=asset,
                    side=side,
                )
                if not result:
                    logger.error("Live BUY failed. Reverting paper trade %s", trade["id"])
                    trades = [t for t in trades if t["id"] != trade["id"]]
                    save_trades(trades)
                    return None
                
                # Use on-chain verified data for accurate P&L
                if isinstance(result, dict):
                    if "shares" in result:
                        trade["shares"] = round(result["shares"], 4)
                    if "actual_entry_price" in result:
                        trade["entry_price"] = result["actual_entry_price"]
                    if "actual_cost" in result:
                        trade["stake"] = result["actual_cost"]
                    save_trades(trades)

        except Exception as exc:
            logger.error("Live BUY exception. Reverting paper trade %s: %s", trade["id"], exc)
            trades = [t for t in trades if t["id"] != trade["id"]]
            save_trades(trades)
            return None

    return trade


def sell_trade(trade_id: str, exit_price: float, reason: str = "manual") -> dict | None:
    """
    Sell (early exit) an open position.

    In live mode, P&L is calculated from actual USDC received (on-chain),
    not from paper price observations.
    """
    trades = load_trades()

    for trade in trades:
        if trade["id"] != trade_id:
            continue
        if trade["status"] != "open":
            continue

        entry_price = trade["entry_price"]
        shares = trade["shares"]
        stake = trade.get("stake", entry_price * shares)

        # Default paper P&L (used if no live data)
        pnl = (exit_price - entry_price) * shares
        actual_exit_price = exit_price

        # ── Live mode: attempt real sell via CLOB (99% size workaround) ──
        token_id = trade.get("token_id", "")
        if token_id:
            try:
                from scalper.live_client import is_live, sell_outcome
                if is_live():
                    # ── WS Liquidity check: skip sell if no bids ──
                    try:
                        from scalper.orderbook_ws import check_sell_liquidity
                        liq = check_sell_liquidity(
                            token_id=token_id,
                            shares=shares,
                            entry_price=entry_price,
                        )
                        if not liq["can_sell"]:
                            print(
                                f"  [LIQUIDITY] {trade['asset']} {trade['side']}: "
                                f"{liq['reason']} → holding for resolution"
                            )
                            logger.info(
                                "SELL SKIPPED (no liquidity): %s %s — %s | "
                                "best_bid=$%.2f depth=%.1f slippage=%.0f%%",
                                trade["asset"], trade["side"], liq["reason"],
                                liq["best_bid"], liq["bid_depth"],
                                liq["slippage_pct"] * 100,
                            )
                            return None
                        else:
                            print(
                                f"  [LIQUIDITY] {trade['asset']} {trade['side']}: "
                                f"bid ${liq['best_bid']:.2f} depth {liq['bid_depth']:.1f} → selling"
                            )
                    except ImportError:
                        pass  # WS module not available, proceed with sell

                    # ── Check time to market close ──
                    event_end_str = trade.get("event_end", "")
                    if event_end_str:
                        try:
                            from datetime import timedelta
                            event_end = datetime.fromisoformat(
                                event_end_str.replace("Z", "+00:00")
                            )
                            secs_left = (event_end - datetime.now(timezone.utc)).total_seconds()
                            if secs_left < 60:
                                print(
                                    f"  [TIMING] {trade['asset']} {trade['side']}: "
                                    f"{secs_left:.0f}s to close → holding for resolution"
                                )
                                return None
                        except (ValueError, TypeError):
                            pass

                    logger.info(
                        "LIVE SELL: %s %s (reason=%s) @ %.4f",
                        trade["asset"], trade["side"], reason, exit_price,
                    )
                    result = sell_outcome(
                        token_id=token_id,
                        price=exit_price,
                        size=shares,
                        asset=trade["asset"],
                        side=trade["side"],
                    )
                    if result and result.get("success"):
                        # Use on-chain USDC delta for accurate P&L
                        actual_proceeds = result.get("actual_proceeds", 0)
                        pnl = actual_proceeds - stake
                        actual_exit_price = actual_proceeds / shares if shares > 0 else exit_price
                        logger.info(
                            "LIVE SELL OK: %s | proceeds $%.2f | P&L $%.2f",
                            trade["id"], actual_proceeds, pnl,
                        )
                    else:
                        # Sell failed — keep position open, let market resolve
                        logger.warning(
                            "LIVE SELL FAILED for %s %s — will resolve automatically",
                            trade["asset"], trade["side"],
                        )
                        print(
                            f"  [LIVE] Sell failed {trade['asset']} {trade['side']} "
                            f"({reason}) - market will resolve automatically"
                        )
                        return None
            except Exception as exc:
                logger.error("LIVE SELL exception for %s: %s", trade["id"], exc)
                return None

        # ── Mark as sold ──────────────────────────────────
        trade["status"] = "sold"
        trade["exit_price"] = round(actual_exit_price, 4)
        trade["exit_time"] = datetime.now(timezone.utc).isoformat()
        trade["pnl"] = round(pnl, 2)
        trade["exit_reason"] = reason

        save_trades(trades)

        logger.info(
            "SOLD %s: %s %s @ %.4f → %.4f | P&L $%.2f (%s)",
            trade["id"], trade["asset"], trade["side"],
            entry_price, actual_exit_price, pnl, reason,
        )

        return trade

    return None


def resolve_trade(trade_id: str, won: bool) -> dict | None:
    """
    Resolve a trade after the 5-minute market closes.

    If WON: payout = shares × $1.00 → P&L = payout - stake
    If LOST: payout = $0.00 → P&L = -stake
    """
    trades = load_trades()

    for trade in trades:
        if trade["id"] != trade_id:
            continue
        if trade["status"] != "open":
            continue

        shares = trade["shares"]
        stake = trade["stake"]

        if won:
            payout = shares * 1.0  # Full payout
            pnl = payout - stake
            trade["status"] = "won"
            trade["exit_price"] = 1.0
        else:
            pnl = -stake
            trade["status"] = "lost"
            trade["exit_price"] = 0.0

        trade["exit_time"] = datetime.now(timezone.utc).isoformat()
        trade["pnl"] = round(pnl, 2)

        save_trades(trades)

        emoji = "✅" if won else "❌"
        logger.info(
            "%s RESOLVED %s: %s %s → %s | P&L $%.2f",
            emoji, trade["id"], trade["asset"], trade["side"],
            "WON" if won else "LOST", pnl,
        )
        return trade

    return None


# ═══════════════════════════════════════════════════════════════
# Position Monitoring
# ═══════════════════════════════════════════════════════════════


def check_open_positions(signal_scores: dict[str, float] | None = None) -> list[dict]:
    """
    Check all open positions for:
    1. Market resolution (closed) → resolve as won/lost
    2. Early exit opportunities (profit take or signal reversal)

    Returns list of actions taken.
    """
    trades = load_trades()
    actions = []
    now = datetime.now(timezone.utc)

    open_trades = [t for t in trades if t.get("status") == "open"]

    for trade in open_trades:
        gamma_id = trade.get("gamma_id", "")
        asset = trade["asset"]
        side = trade["side"]

        # Fetch current market state
        market_data = get_market_current_price(gamma_id)
        if not market_data:
            continue

        # ── Check if market has closed (resolution) ──────────
        if market_data.get("closed", False):
            # Determine outcome: UP wins if up_price → 1.0
            up_price = market_data.get("up_price", 0.5)
            down_price = market_data.get("down_price", 0.5)

            if side == "UP":
                won = up_price > 0.9  # UP resolved as winner
            else:
                won = down_price > 0.9  # DOWN resolved as winner

            result = resolve_trade(trade["id"], won)
            if result:
                actions.append({
                    "type": "resolved",
                    "trade": result,
                    "won": won,
                })
            continue

        # ── Check for early exit ─────────────────────────────
        # Current price and sell price for the CORRECT side
        if side == "UP":
            current_price = market_data.get("up_price", 0.5)
            sell_price = market_data.get("up_best_bid", current_price)
        else:
            current_price = market_data.get("down_price", 0.5)
            sell_price = market_data.get("down_best_bid", current_price)

        entry_price = trade["entry_price"]

        if entry_price > 0:
            price_change = (current_price - entry_price) / entry_price
        else:
            price_change = 0

        # Stop-loss: cut losses early
        if price_change <= -HFT_STOP_LOSS:
            result = sell_trade(trade["id"], sell_price, reason="stop_loss")
            if result:
                actions.append({
                    "type": "sold",
                    "trade": result,
                    "reason": f"Stop loss ({price_change:.1%})",
                })
            continue

        # Take profit
        if price_change >= HFT_EARLY_EXIT_PROFIT:
            result = sell_trade(trade["id"], sell_price, reason="take_profit")
            if result:
                actions.append({
                    "type": "sold",
                    "trade": result,
                    "reason": f"Take profit ({price_change:.1%})",
                })
            continue

        # Signal reversal check
        if signal_scores and asset in signal_scores:
            new_signal = signal_scores[asset]
            if side == "UP" and new_signal < -HFT_EARLY_EXIT_REVERSAL:
                result = sell_trade(trade["id"], sell_price, reason="signal_reversal")
                if result:
                    actions.append({
                        "type": "sold",
                        "trade": result,
                        "reason": f"Signal reversal ({new_signal:.3f})",
                    })
            elif side == "DOWN" and new_signal > HFT_EARLY_EXIT_REVERSAL:
                result = sell_trade(trade["id"], sell_price, reason="signal_reversal")
                if result:
                    actions.append({
                        "type": "sold",
                        "trade": result,
                        "reason": f"Signal reversal ({new_signal:.3f})",
                    })

    return actions


def check_open_positions_profiled(
    signal_scores: dict[str, float] | None = None,
    profile=None,
    markets_data: dict | None = None,
) -> list[dict]:
    """
    Strategy-aware position monitoring (V2/V3).

    Supports:
    - Per-profile take_profit and stop_loss thresholds
    - Trailing stop: moves SL to break-even when position is up 20%+
    - Profile-specific signal reversal threshold
    """
    if profile is None:
        return check_open_positions(signal_scores)

    trades = load_trades()
    actions = []
    now = datetime.now(timezone.utc)

    tp = profile.take_profit
    sl = profile.stop_loss
    reversal_thresh = profile.signal_reversal
    trailing = profile.trailing_stop
    trailing_trigger = profile.trailing_trigger

    # Build slug lookup from fresh scan data
    slug_lookup = {}
    if markets_data:
        for _key, mkt in markets_data.items():
            s = mkt.get("slug", "")
            if s:
                slug_lookup[s] = mkt

    open_trades = [t for t in trades if t.get("status") == "open"]

    for trade in open_trades:
        gamma_id = trade.get("gamma_id", "")
        asset = trade["asset"]
        side = trade["side"]

        # Priority: scan data (fresh) > gamma API (may be stale)
        market_data = None
        pos_slug = trade.get("market_slug", "")
        if pos_slug and pos_slug in slug_lookup:
            market_data = slug_lookup[pos_slug]
        elif markets_data and asset in markets_data:
            mkt = markets_data[asset]
            if mkt.get("slug") == pos_slug or not pos_slug:
                market_data = mkt
        
        if market_data is None:
            market_data = get_market_current_price(gamma_id)
        if not market_data:
            continue

        # ── Resolution check ─────────────────────────────────
        if market_data.get("closed", False):
            up_price = market_data.get("up_price", 0.5)
            down_price = market_data.get("down_price", 0.5)
            won = (up_price > 0.9) if side == "UP" else (down_price > 0.9)

            result = resolve_trade(trade["id"], won)
            if result:
                actions.append({"type": "resolved", "trade": result, "won": won})
            continue

        # ── Price calculation ────────────────────────────────
        if side == "UP":
            current_price = market_data.get("up_price", 0.5)
            sell_price = market_data.get("up_best_bid", current_price)
        else:
            current_price = market_data.get("down_price", 0.5)
            sell_price = market_data.get("down_best_bid", current_price)

        entry_price = trade["entry_price"]
        price_change = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        # ── Stop-loss ────────────────────────────────────────
        effective_sl = sl

        # Trailing stop: if position was up 20%+, move SL to break-even
        if trailing and price_change >= trailing_trigger:
            effective_sl = 0.0  # break-even = 0% loss
            # Track the high watermark for future trailing
            trade_high = trade.get("_high_watermark", entry_price)
            if current_price > trade_high:
                trade["_high_watermark"] = current_price
                save_trades(trades)

        if price_change <= -effective_sl:
            reason_str = "trailing_stop" if effective_sl < sl else "stop_loss"
            result = sell_trade(trade["id"], sell_price, reason=reason_str)
            if result:
                actions.append({
                    "type": "sold", "trade": result,
                    "reason": f"{reason_str} ({price_change:.1%})",
                })
            continue

        # ── Take profit ──────────────────────────────────────
        if price_change >= tp:
            result = sell_trade(trade["id"], sell_price, reason="take_profit")
            if result:
                actions.append({
                    "type": "sold", "trade": result,
                    "reason": f"Take profit ({price_change:.1%})",
                })
            continue

        # ── Signal reversal ──────────────────────────────────
        if signal_scores and asset in signal_scores:
            new_signal = signal_scores[asset]
            should_exit = (
                (side == "UP" and new_signal < -reversal_thresh)
                or (side == "DOWN" and new_signal > reversal_thresh)
            )
            if should_exit:
                result = sell_trade(trade["id"], sell_price, reason="signal_reversal")
                if result:
                    actions.append({
                        "type": "sold", "trade": result,
                        "reason": f"Signal reversal ({new_signal:.3f})",
                    })

    return actions


def calculate_kelly_stake(
    base_stake: float,
    signal_score: float,
    capital: float,
    max_pct: float = 0.05,
) -> float:
    """
    Calculate position size using simplified Kelly Criterion.

    Higher signal confidence → larger stake (up to 5% of capital).
    """
    abs_score = abs(signal_score)

    # Confidence multiplier based on signal strength
    if abs_score >= 0.80:
        multiplier = 1.5
    elif abs_score >= 0.60:
        multiplier = 1.2
    else:
        multiplier = 1.0

    stake = base_stake * multiplier
    max_stake = capital * max_pct

    return round(min(stake, max_stake), 2)


def review_sold_trades() -> list[dict]:
    """
    Hindsight Analysis: For trades that were sold early (stop_loss,
    take_profit, signal_reversal), check the final market resolution
    to determine if selling was the right call.

    Compares actual P&L (from selling) vs hypothetical P&L (if held
    to resolution). Marks trades as 'reviewed' so we only do this once.

    Returns list of newly reviewed trades with hindsight data.
    """
    trades = load_trades()
    reviewed = []

    sold_unreviewed = [
        t for t in trades
        if t.get("status") == "sold" and not t.get("hindsight_reviewed", False)
    ]

    if not sold_unreviewed:
        return []

    for trade in sold_unreviewed:
        gamma_id = trade.get("gamma_id", "")
        if not gamma_id:
            continue

        # Check if the market has closed
        market_data = get_market_current_price(gamma_id)
        if not market_data:
            continue

        if not market_data.get("closed", False):
            continue  # Market still open, check next cycle

        # Determine resolution outcome
        up_price = market_data.get("up_price", 0.5)
        down_price = market_data.get("down_price", 0.5)

        side = trade["side"]
        if side == "UP":
            would_have_won = up_price > 0.9
        else:
            would_have_won = down_price > 0.9

        # Calculate hypothetical P&L if held to resolution
        shares = trade["shares"]
        stake = trade["stake"]

        if would_have_won:
            held_pnl = (shares * 1.0) - stake  # Won → payout $1/share
        else:
            held_pnl = -stake                    # Lost → payout $0

        # Actual P&L from selling early
        actual_pnl = trade.get("pnl", 0) or 0

        # Compare: was selling the right call?
        difference = actual_pnl - held_pnl  # positive = selling was better
        if difference > 0:
            decision = "GOOD"  # Selling saved money
            saved_amount = difference
        elif difference < 0:
            decision = "BAD"   # Should have held
            saved_amount = difference  # negative = money left on the table
        else:
            decision = "NEUTRAL"
            saved_amount = 0

        # Store hindsight data on the trade
        trade["hindsight_reviewed"] = True
        trade["hindsight"] = {
            "would_have_won": would_have_won,
            "held_pnl": round(held_pnl, 2),
            "actual_pnl": round(actual_pnl, 2),
            "decision": decision,
            "difference": round(difference, 2),
            "resolution": "UP" if up_price > 0.9 else "DOWN",
        }

        reviewed.append(trade)

        emoji = "✅" if decision == "GOOD" else "❌" if decision == "BAD" else "➖"
        logger.info(
            "%s HINDSIGHT %s: %s %s | Sold P&L=$%.2f | Held P&L=$%.2f | %s ($%.2f)",
            emoji, trade["id"], trade["asset"], side,
            actual_pnl, held_pnl, decision, difference,
        )

    if reviewed:
        save_trades(trades)

    return reviewed


def get_open_positions() -> list[dict]:
    """Return all currently open positions."""
    trades = load_trades()
    return [t for t in trades if t.get("status") == "open"]


def get_recent_resolved(limit: int = 10) -> list[dict]:
    """Return most recently resolved trades."""
    trades = load_trades()
    resolved = [t for t in trades if t.get("status") in ("won", "lost", "sold")]
    resolved.sort(key=lambda x: x.get("exit_time", ""), reverse=True)
    return resolved[:limit]


def get_hindsight_summary() -> dict | None:
    """
    Aggregate hindsight data across all reviewed sold trades.

    Returns a summary comparing total actual P&L (from selling)
    vs total hypothetical P&L (if held to resolution).
    """
    trades = load_trades()
    reviewed = [
        t for t in trades
        if t.get("status") == "sold" and t.get("hindsight_reviewed", False)
    ]

    if not reviewed:
        return None

    total_actual = 0.0
    total_held = 0.0
    good_calls = 0
    bad_calls = 0

    for t in reviewed:
        hs = t.get("hindsight", {})
        total_actual += hs.get("actual_pnl", 0)
        total_held += hs.get("held_pnl", 0)
        decision = hs.get("decision", "")
        if decision == "GOOD":
            good_calls += 1
        elif decision == "BAD":
            bad_calls += 1

    return {
        "count": len(reviewed),
        "total_actual_pnl": round(total_actual, 2),
        "total_held_pnl": round(total_held, 2),
        "difference": round(total_actual - total_held, 2),
        "good_calls": good_calls,
        "bad_calls": bad_calls,
        "accuracy": (good_calls / len(reviewed) * 100) if reviewed else 0,
    }

