"""
scalper/trader_v10.py — V10 Universal Trend Scalper Portfolio Manager.

Manages positions for the universal trend scalper:
- Opening positions in trending markets
- Monitoring open positions against TP/SL/time-stop
- Portfolio-level constraints (max positions, exposure)
- Independent storage in hft_trades_v10.json
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from scalper.signals_v10 import check_exit_condition

logger = logging.getLogger("polybot.v10.trader")

# ── Configuration ──────────────────────────────────────────────
TRADES_FILE = "hft_trades_v10.json"
MAX_OPEN_POSITIONS = 10
STAKE_PER_TRADE = 2.00
CLOB_BASE = "https://clob.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"


def _trades_path() -> Path:
    return Path(__file__).parent.parent / "data" / "trades" / TRADES_FILE


def load_trades() -> list[dict]:
    """Load V10 trades from JSON file."""
    path = _trades_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_trades(trades: list[dict]) -> None:
    """Save V10 trades to JSON file."""
    path = _trades_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, default=str)


def _next_trade_id(trades: list[dict]) -> str:
    """Generate next sequential trade ID for V10."""
    if not trades:
        return "v10_001"
    max_num = 0
    for t in trades:
        tid = t.get("id", "v10_000")
        try:
            num = int(tid.split("_")[1])
            max_num = max(max_num, num)
        except (IndexError, ValueError):
            pass
    return f"v10_{max_num + 1:03d}"


def get_open_positions() -> list[dict]:
    """Get all open V10 positions."""
    trades = load_trades()
    return [t for t in trades if t.get("status") == "open"]


def get_portfolio_stats() -> dict:
    """Calculate V10 portfolio statistics."""
    trades = load_trades()
    open_pos = [t for t in trades if t.get("status") == "open"]
    resolved = [t for t in trades if t.get("status") in ("won", "lost", "sold")]

    total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved)
    wins = sum(1 for t in resolved if (t.get("pnl", 0) or 0) > 0)
    losses = len(resolved) - wins
    wr = round(wins / len(resolved) * 100, 1) if resolved else 0

    open_exposure = sum(t.get("stake", 0) for t in open_pos)

    return {
        "open_positions": len(open_pos),
        "open_exposure": round(open_exposure, 2),
        "total_resolved": len(resolved),
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "total_pnl": round(total_pnl, 2),
        "total_trades": len(trades),
    }


def can_open_trade() -> tuple[bool, str]:
    """Check if we can open a new V10 position."""
    open_pos = get_open_positions()

    if len(open_pos) >= MAX_OPEN_POSITIONS:
        return False, f"Max positions reached ({MAX_OPEN_POSITIONS})"

    return True, "OK"


def is_already_in_market(market_id: str) -> bool:
    """Check if we already have a position in this market."""
    open_pos = get_open_positions()
    for t in open_pos:
        if t.get("market_id") == market_id:
            return True
    return False


def open_trend_trade(signal: dict, stake: float | None = None) -> dict | None:
    """
    Open a new V10 trend trade based on a signal.
    
    Paper trading only — simulates buying shares at the entry price.
    """
    actual_stake = stake or STAKE_PER_TRADE

    # Pre-flight checks
    ok, reason = can_open_trade()
    if not ok:
        print(f"  [V10-SKIP] Cannot open: {reason}")
        return None

    if is_already_in_market(signal["market_id"]):
        print(f"  [V10-SKIP] Already in market: {signal['question'][:50]}")
        return None

    entry_price = signal["entry_price"]
    if entry_price <= 0 or entry_price >= 1.0:
        return None

    shares = actual_stake / entry_price
    trades = load_trades()

    trade = {
        "id": _next_trade_id(trades),
        "market_id": signal["market_id"],
        "question": signal["question"],
        "slug": signal["slug"],
        "side": signal["side"],
        "token_id": signal["token_id"],
        "entry_price": round(entry_price, 4),
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "stake": round(actual_stake, 2),
        "shares": round(shares, 4),
        "momentum_score": signal.get("adjusted_score", 0),
        "momentum_1h": signal.get("momentum_1h", 0),
        "momentum_1d": signal.get("momentum_1d", 0),
        "status": "open",
        "tp_price": signal.get("tp_price", 0),
        "sl_price": signal.get("sl_price", 0),
        "exit_price": None,
        "exit_time": None,
        "exit_reason": None,
        "pnl": None,
        "gamma_id": signal.get("gamma_id", ""),
    }

    trades.append(trade)
    save_trades(trades)

    q_short = signal["question"][:55]
    print(
        f"  [V10-ENTRY] {q_short}\n"
        f"              {signal['side']} @ ${entry_price:.2f} | "
        f"Stake ${actual_stake:.2f} | "
        f"TP=${signal['tp_price']:.2f} SL=${signal['sl_price']:.2f} | "
        f"Score={signal['adjusted_score']:.3f}"
    )
    return trade


def _fetch_current_price(token_id: str) -> float | None:
    """Fetch current mid-price from CLOB for an outcome token."""
    try:
        resp = requests.get(
            f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval": "1h", "fidelity": 1},
            timeout=8,
        )
        resp.raise_for_status()
        history = resp.json().get("history", [])
        if history:
            return history[-1]["p"]
    except Exception as exc:
        logger.debug("Price fetch failed for %s: %s", token_id[:20], exc)

    # Fallback: try Gamma API
    try:
        resp = requests.get(
            f"{GAMMA_API_BASE}/markets/{token_id}",
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            prices = json.loads(data.get("outcomePrices", '["0.5","0.5"]'))
            return float(prices[0])
    except Exception:
        pass

    return None


def check_all_exits() -> list[dict]:
    """
    Check all open V10 positions for exit conditions.
    Closes positions that hit TP, SL, or time-stop.
    
    Returns list of closed trades.
    """
    trades = load_trades()
    open_trades = [t for t in trades if t.get("status") == "open"]
    closed = []
    changed = False

    for trade in open_trades:
        token_id = trade.get("token_id", "")
        if not token_id:
            continue

        current_price = _fetch_current_price(token_id)
        if current_price is None:
            print(f"  [V10-HOLD] {trade['question'][:45]} | Price unavailable")
            continue

        # Calculate unrealized P&L
        entry_price = trade["entry_price"]
        shares = trade["shares"]
        unrealized_pnl = (current_price - entry_price) * shares
        gain_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        # Check exit conditions
        action, reason = check_exit_condition(trade, current_price)

        if action == "SELL":
            # Close the position
            pnl = (current_price - entry_price) * shares
            trade["status"] = "sold"
            trade["exit_price"] = round(current_price, 4)
            trade["exit_time"] = datetime.now(timezone.utc).isoformat()
            trade["exit_reason"] = reason
            trade["pnl"] = round(pnl, 2)
            changed = True
            closed.append(trade)

            icon = "✅" if pnl > 0 else "❌"
            print(
                f"  [V10-EXIT] {icon} {trade['question'][:45]}\n"
                f"             {trade['side']} @ ${current_price:.2f} ({gain_pct:+.1%}) | "
                f"Reason: {reason} | P&L ${pnl:+.2f}"
            )
        else:
            # Hold — log status
            icon = "📈" if gain_pct > 0 else "📉"
            print(
                f"  [V10-HOLD] {icon} {trade['question'][:45]}\n"
                f"             {trade['side']} @ ${current_price:.2f} ({gain_pct:+.1%}) | "
                f"Unrealized ${unrealized_pnl:+.2f}"
            )

    if changed:
        save_trades(trades)

    return closed
