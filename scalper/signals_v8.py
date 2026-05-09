"""
scalper/signals_v8.py — Choppy Market Mean Reversion Signal Engine.

V8 operates OPPOSITE to V1-V7 momentum strategies:
  - Detects when the market is in a CHOPPY (range-bound) regime
  - In choppy: bets that price will REVERT to the mean
  - In trending: deactivates completely (no trades)

Regime Detection:
  Uses Polymarket mid-price oscillations within the current 5-min window.
  If price crosses the SMA back and forth multiple times → CHOPPY.
  If price stays on one side of the SMA consistently → TRENDING.

Mean Reversion Signal:
  - Computes SMA of Polymarket mid-price over configurable window
  - Measures deviation from SMA
  - Price ABOVE SMA → signal DOWN (will revert down)
  - Price BELOW SMA → signal UP (will revert up)

Diagnostic Logging:
  All logs use [V8-*] prefix for easy filtering.
"""

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("polybot.scalper.signals_v8")

# Import the shared SignalResult from the existing signals module
from scalper.signals import SignalResult


# ═══════════════════════════════════════════════════════════════
# Regime Detection
# ═══════════════════════════════════════════════════════════════

# Rolling buffer of recent market resolutions: deque of {"asset": str, "won_side": str, "ts": float}
_resolution_history: dict[str, deque] = {}

# Per-cycle regime cache (reset each cycle)
_regime_cache: dict[str, str] = {}

# Regime log file
REGIME_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "regime", "regime_log.json")


def record_resolution(asset: str, won_side: str):
    """
    Record a market resolution for regime detection.
    Called externally when a market resolves (UP or DOWN won).
    """
    if asset not in _resolution_history:
        _resolution_history[asset] = deque(maxlen=20)
    _resolution_history[asset].append({
        "won_side": won_side,
        "ts": time.time(),
    })


def detect_regime(
    asset: str,
    token_id: str = "",
    window: int = 5,
    trending_threshold: int = 4,
) -> tuple[str, str, dict]:
    """
    Detect the current market regime for an asset (cascade logic).

    Primary method: historical resolutions of the last N cycles.
    Fallback method: real-time mid-price oscillation of current cycle.
    
    Returns:
        (regime_type, method_used, details_dict)
    """
    # ── 1. Primary Method: Historical Resolutions ──
    if asset in _resolution_history:
        resolutions = list(_resolution_history[asset])
        if len(resolutions) >= window:
            recent = resolutions[-window:]
            directions = [r["won_side"] for r in recent]
            
            # Find dominant direction
            up_count = directions.count("UP")
            down_count = directions.count("DOWN")
            
            dominant_count = max(up_count, down_count)
            dominant_side = "UP" if up_count > down_count else "DOWN"
            
            details = {"up_count": up_count, "down_count": down_count, "window": window}
            
            if dominant_count >= trending_threshold:
                return (f"TRENDING_{dominant_side}", "historical", details)
            return ("CHOPPY", "historical", details)

    # ── 2. Fallback Method: Real-time Oscillation ──
    if token_id:
        try:
            from scalper.orderbook_ws import _mid_history, _lock
            with _lock:
                history = _mid_history.get(token_id)
                if history and len(history) >= 10:
                    now = time.time()
                    recent = [(t, p) for t, p in history if t >= now - 60]
                    if len(recent) >= 10:
                        prices = [p for _, p in recent]
                        oscillation = max(prices) - min(prices)
                        
                        details = {"oscillation": round(oscillation, 4), "samples": len(prices)}
                        
                        if oscillation < 0.015:  # Less than 1.5 cents range -> CHOPPY
                            return ("CHOPPY", "realtime", details)
                        return ("TRENDING", "realtime", details)
        except (ImportError, AttributeError):
            pass

    # ── 3. Cold Start ──
    return ("UNKNOWN", "cold-start", {"cycles": len(_resolution_history.get(asset, []))})


def _log_regime(asset: str, regime: str, details: dict):
    """Append regime detection to regime_log.json."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "asset": asset,
        "regime": regime,
        **details,
    }
    try:
        if os.path.exists(REGIME_LOG_FILE):
            with open(REGIME_LOG_FILE, "r", encoding="utf-8") as f:
                log = json.load(f)
        else:
            log = []

        log.append(entry)

        # Keep last 500 entries to prevent unbounded growth
        if len(log) > 500:
            log = log[-500:]

        with open(REGIME_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.debug("Failed to write regime log: %s", e)


# ═══════════════════════════════════════════════════════════════
# Mean Reversion Signal
# ═══════════════════════════════════════════════════════════════


def compute_reversion_signal(
    asset: str,
    token_id: str,
    market: dict,
    reversion_threshold: float = 0.008,
    ma_window_sec: int = 60,
) -> SignalResult | None:
    """
    Compute a mean reversion signal for a single asset.

    Uses Polymarket WS mid-price history to calculate:
    1. SMA over ma_window_sec
    2. Current deviation from SMA
    3. If deviation > threshold → signal in OPPOSITE direction (mean reversion)

    Returns SignalResult or None if insufficient data.
    """
    if not token_id:
        return None

    try:
        from scalper.orderbook_ws import _mid_history, _lock
    except ImportError:
        return None

    now = time.time()
    cutoff = now - ma_window_sec

    with _lock:
        history = _mid_history.get(token_id)
        if not history or len(history) < 5:
            print(
                f"  [V8-SIGNAL] {asset}: Insufficient mid-price history "
                f"({len(history) if history else 0} samples) -> SKIP"
            )
            return None

        # Get samples within the MA window
        window_samples = [(t, p) for t, p in history if t >= cutoff]
        if len(window_samples) < 3:
            # Use all available if window is too narrow
            window_samples = list(history)

        current_mid = history[-1][1]

    # Calculate SMA
    prices = [p for _, p in window_samples]
    sma = sum(prices) / len(prices)

    # Deviation from mean
    deviation = current_mid - sma

    # Score: deviation normalized to [-1, 1]
    # For mean reversion, we INVERT: positive deviation → sell (DOWN signal)
    raw_score = -deviation / 0.05  # 0.05 deviation = full score
    score = max(-1.0, min(1.0, raw_score))

    # Direction (INVERTED for mean reversion)
    if deviation > reversion_threshold:
        direction = "DOWN"  # Price above mean → will revert down
    elif deviation < -reversion_threshold:
        direction = "UP"    # Price below mean → will revert up
    else:
        direction = "NEUTRAL"

    # Log the signal computation
    dev_sign = "+" if deviation >= 0 else ""
    dir_reason = "price ABOVE mean" if deviation > 0 else "price BELOW mean"
    if direction != "NEUTRAL":
        print(
            f"  [V8-SIGNAL] {asset}: mid=${current_mid:.3f} | "
            f"SMA{ma_window_sec}=${sma:.3f} | "
            f"dev={dev_sign}{deviation:.4f} | "
            f"-> {direction} ({dir_reason})"
        )
    else:
        print(
            f"  [V8-SIGNAL] {asset}: mid=${current_mid:.3f} | "
            f"SMA{ma_window_sec}=${sma:.3f} | "
            f"dev={dev_sign}{deviation:.4f} < thresh {reversion_threshold} -> NEUTRAL"
        )

    return SignalResult(
        asset=asset,
        direction=direction,
        score=float(score),
        ema_signal=0.0,
        rsi_signal=0.0,
        momentum_signal=0.0,
        volume_signal=0.0,
        vwap_signal=float(deviation),  # Store deviation in vwap_signal field
        rsi_value=0.0,
        current_price=float(current_mid),
        ema_fast=float(current_mid),
        ema_slow=float(sma),
        confidence="MEDIUM" if abs(deviation) > reversion_threshold * 2 else "LOW",
    )


# ═══════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════

# Track stats across cycles
_v8_stats = {
    "choppy_cycles": 0,
    "trending_cycles": 0,
    "unknown_cycles": 0,
    "signals_generated": 0,
}


def compute_all_signals_v8(
    assets: dict,
    markets: dict,
    regime_window: int = 5,
    regime_threshold: int = 4,
    reversion_threshold: float = 0.008,
    ma_window_sec: int = 60,
) -> dict[str, SignalResult]:
    """
    Main signal computation for V8.

    1. Detect regime for each asset
    2. If CHOPPY → compute mean reversion signal
    3. If TRENDING → skip (return NEUTRAL)
    4. Log everything

    Returns dict of asset_key → SignalResult
    """
    signals = {}
    regime_details = {}

    for asset_key in assets:
        if asset_key not in markets:
            continue

        market = markets[asset_key]

        # Get token IDs for this market
        up_token = market.get("up_token_id", "")
        down_token = market.get("down_token_id", "")

        # Use the UP token for regime detection (arbitrary choice, both track same market)
        primary_token = up_token or down_token

        # ── 1. REGIME DETECTION ──────────────────────────────
        regime, method, details = detect_regime(
            asset=asset_key,
            token_id=primary_token,
            window=regime_window,
            trending_threshold=regime_threshold,
        )

        # Determine which token to use for signal
        # In mean reversion we use the UP token's mid-price
        signal_token = up_token

        # Log regime
        if regime == "CHOPPY":
            _v8_stats["choppy_cycles"] += 1
            method_str = "historical: " + f"{details.get('up_count',0)}UP/{details.get('down_count',0)}DOWN" if method == "historical" else f"realtime: oscillation=${details.get('oscillation',0)}"
            print(f"  [V8-REGIME] {asset_key}: CHOPPY ({method_str}) -> mean reversion active")
        elif regime.startswith("TRENDING"):
            _v8_stats["trending_cycles"] += 1
            method_str = "historical" if method == "historical" else f"realtime: oscillation=${details.get('oscillation',0)}"
            print(f"  [V8-REGIME] {asset_key}: {regime} ({method_str}) -> V8 skips")
        else:
            _v8_stats["unknown_cycles"] += 1
            print(f"  [V8-REGIME] {asset_key}: UNKNOWN (cold-start: solo {details.get('cycles',0)} ciclos) -> V8 skips")
            # In UNKNOWN, V8 does not enter (user requested to skip rather than assume choppy)
            regime = "UNKNOWN"

        # Log regime to file
        _log_regime(asset_key, regime, {
            "method": method,
            "mid_price": market.get("up_price", 0.5),
            **details
        })

        # ── 2. SIGNAL GENERATION ─────────────────────────────
        if regime == "CHOPPY":
            signal = compute_reversion_signal(
                asset=asset_key,
                token_id=signal_token,
                market=market,
                reversion_threshold=reversion_threshold,
                ma_window_sec=ma_window_sec,
            )
            if signal:
                signals[asset_key] = signal
                _v8_stats["signals_generated"] += 1
        else:
            # TRENDING regime → return NEUTRAL signal (V8 sits out)
            signals[asset_key] = SignalResult(
                asset=asset_key,
                direction="NEUTRAL",
                score=0.0,
                ema_signal=0.0,
                rsi_signal=0.0,
                momentum_signal=0.0,
                volume_signal=0.0,
                vwap_signal=0.0,
                rsi_value=0.0,
                current_price=market.get("up_price", 0.5),
                ema_fast=0.0,
                ema_slow=0.0,
                confidence="LOW",
            )

    # ── 3. CYCLE STATS ───────────────────────────────────────
    total = _v8_stats["choppy_cycles"] + _v8_stats["trending_cycles"] + _v8_stats["unknown_cycles"]
    if total > 0 and total % 5 == 0:  # Print every 5 cycles
        activation = _v8_stats["choppy_cycles"] / total * 100
        print(
            f"  [V8-STATS] Choppy: {_v8_stats['choppy_cycles']} | "
            f"Trending (skip): {_v8_stats['trending_cycles']} | "
            f"Activation: {activation:.0f}%"
        )

    return signals
