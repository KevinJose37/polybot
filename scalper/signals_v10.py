"""
scalper/signals_v10.py — V10 Universal Trend Scalper Signal Module.

V10 scans all Polymarket markets for momentum trends.
Unlike V1-V9 (which trade fixed crypto assets on 5min windows),
V10 trades any market with active price momentum.

Signal = momentum_score from universal_scanner + entry/exit calculations.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger("polybot.v10.signals")

# ── Configuration ──────────────────────────────────────────────
SIGNAL_THRESHOLD = 0.10        # Minimum momentum score to enter
TP_PCT = 0.50                  # +50% take profit (let winners run big)
SL_PCT = 0.25                  # -25% stop loss
TIME_STOP_HOURS = 720          # 30 days without movement → evaluate exit
STALE_MOVEMENT_THRESHOLD = 0.05  # If price moved < 5% in 30 days, consider stale


def compute_signals_v10(trending_markets: list[dict]) -> list[dict]:
    """
    Generate trading signals from trending markets.
    
    For each market with momentum, compute:
    - entry_price, side, token_id
    - tp_price (take profit)
    - sl_price (stop loss)
    - signal quality score
    
    Returns list of signal dicts sorted by score.
    """
    signals = []

    for m in trending_markets:
        entry_price = m.get("entry_price", 0)
        side = m.get("trade_side", "YES")
        momentum_score = m.get("final_score", 0)
        confirmed = m.get("confirmed", False)

        if entry_price <= 0 or entry_price >= 1.0:
            continue

        if momentum_score < SIGNAL_THRESHOLD:
            continue

        # Calculate TP and SL price levels
        tp_price = round(entry_price * (1 + TP_PCT), 4)
        sl_price = round(entry_price * (1 - SL_PCT), 4)

        # Cap TP at 0.95 (can't go above 1.0)
        tp_price = min(tp_price, 0.95)
        # Floor SL at 0.02
        sl_price = max(sl_price, 0.02)

        # Confirmation bonus
        quality = "CONFIRMED" if confirmed else "UNCONFIRMED"
        adjusted_score = momentum_score * (1.2 if confirmed else 0.8)

        signal = {
            "market_id": m.get("id", ""),
            "question": m.get("question", "?"),
            "slug": m.get("slug", ""),
            "side": side,
            "token_id": m.get("trade_token_id", ""),
            "entry_price": entry_price,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "momentum_score": momentum_score,
            "adjusted_score": round(adjusted_score, 4),
            "quality": quality,
            "momentum_1h": m.get("momentum_1h", 0),
            "momentum_1d": m.get("momentum_1d", 0),
            "momentum_1w": m.get("momentum_1w", 0),
            "liquidity": m.get("liquidity", 0),
            "volume_24h": m.get("volume_24h", 0),
            "spread": m.get("spread", 0),
            "days_to_resolution": m.get("days_to_resolution", 999),
            "best_bid": m.get("best_bid", 0),
            "best_ask": m.get("best_ask", 0),
            "gamma_id": m.get("gamma_id", ""),
            "yes_token_id": m.get("yes_token_id", ""),
            "no_token_id": m.get("no_token_id", ""),
        }
        signals.append(signal)

    # Sort by adjusted score descending
    signals.sort(key=lambda x: x["adjusted_score"], reverse=True)
    return signals


def check_exit_condition(
    trade: dict,
    current_price: float,
) -> tuple[str, str]:
    """
    Check if an open V10 position should be exited.
    
    Returns (action, reason):
    - ("SELL", "take_profit") — price hit TP
    - ("SELL", "stop_loss") — price hit SL  
    - ("SELL", "time_stop") — position is stale
    - ("HOLD", "") — keep holding
    """
    entry_price = trade.get("entry_price", 0)
    if entry_price <= 0:
        return "HOLD", ""

    gain_pct = (current_price - entry_price) / entry_price

    # Take profit
    if gain_pct >= TP_PCT:
        return "SELL", f"take_profit (+{gain_pct:.1%})"

    # Stop loss
    if gain_pct <= -SL_PCT:
        return "SELL", f"stop_loss ({gain_pct:.1%})"

    # Time stop (7 days)
    entry_time_str = trade.get("entry_time", "")
    if entry_time_str:
        try:
            entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
            hours_held = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600
            if hours_held >= TIME_STOP_HOURS:
                # Only exit if the position hasn't moved much
                if abs(gain_pct) < STALE_MOVEMENT_THRESHOLD:
                    return "SELL", f"time_stop ({hours_held:.0f}h, {gain_pct:+.1%})"
        except (ValueError, TypeError):
            pass

    return "HOLD", ""
