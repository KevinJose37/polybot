"""
scalper/signals_v2.py — Enhanced signal engine for V2 strategy.

Improvements over signals.py (V1):
  - ATR-based volatility filter (skip ranging markets)
  - Micro-momentum: exponential weight on last 3 candles
  - Rebalanced weights: EMA 0.25, RSI 0.20, MOM 0.25, VOL 0.15, VWAP 0.15
  - Confidence scoring for Kelly sizing
"""

import logging

import numpy as np

from scalper.config import BINANCE_KLINES_URL
from scalper.signals import SignalResult, _ema, _rsi, _vwap, fetch_klines

logger = logging.getLogger("polybot.scalper.signals_v2")


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         period: int = 10) -> float:
    """Calculate Average True Range for volatility measurement."""
    if len(closes) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    recent = true_ranges[-period:]
    return float(np.mean(recent))


def _micro_momentum(closes: np.ndarray) -> float:
    """
    Micro-momentum: exponential-weighted rate of change over last 5 candles.
    More weight on recent candles for ultra-short-term prediction.
    """
    if len(closes) < 6:
        return 0.0

    # Last 5 close-to-close changes
    changes = []
    for i in range(-5, 0):
        pct_change = (closes[i] - closes[i - 1]) / closes[i - 1] * 100
        changes.append(pct_change)

    # Exponential weights: newest = highest weight
    weights = np.array([0.10, 0.15, 0.20, 0.25, 0.30])
    weighted_mom = float(np.dot(changes, weights))

    return np.clip(weighted_mom * 15, -1.0, 1.0)


def compute_signal_v2(asset: str, binance_symbol: str) -> SignalResult | None:
    """
    Enhanced composite trading signal (V2).

    Changes from V1:
    1. ATR volatility filter — skip flat markets
    2. Micro-momentum with exponential weighting
    3. Rebalanced indicator weights
    """
    klines = fetch_klines(binance_symbol, interval="1m", limit=30)
    if len(klines) < 15:
        logger.warning("Insufficient kline data for %s (%d candles)",
                        asset, len(klines))
        return None

    closes = np.array([k["close"] for k in klines])
    highs = np.array([k["high"] for k in klines])
    lows = np.array([k["low"] for k in klines])
    volumes = np.array([k["volume"] for k in klines])

    current_price = closes[-1]

    # ── Volatility filter ────────────────────────────────────
    atr_value = _atr(highs, lows, closes, period=10)
    atr_pct = (atr_value / current_price) * 100 if current_price > 0 else 0

    # If ATR < 0.01% → market is dead flat, signal is unreliable
    if atr_pct < 0.01:
        logger.debug("V2 %s: ATR %.4f%% too low (ranging), returning NEUTRAL", asset, atr_pct)
        return SignalResult(
            asset=asset, direction="NEUTRAL", score=0.0,
            ema_signal=0.0, rsi_signal=0.0, momentum_signal=0.0,
            volume_signal=0.0, vwap_signal=0.0, rsi_value=50.0,
            current_price=float(current_price),
            ema_fast=float(closes[-1]), ema_slow=float(closes[-1]),
            confidence="LOW",
        )

    # ── 1. EMA Cross Signal ──────────────────────────────────
    ema_fast = _ema(closes, 3)
    ema_slow = _ema(closes, 8)

    ema_diff = ema_fast[-1] - ema_slow[-1]
    ema_diff_prev = ema_fast[-2] - ema_slow[-2]
    ema_pct = (ema_diff / current_price) * 100
    ema_signal = float(np.clip(ema_pct * 20, -1.0, 1.0))

    # Bonus for fresh crossover
    if ema_diff > 0 and ema_diff_prev <= 0:
        ema_signal = min(ema_signal + 0.3, 1.0)
    elif ema_diff < 0 and ema_diff_prev >= 0:
        ema_signal = max(ema_signal - 0.3, -1.0)

    # ── 2. RSI Signal ────────────────────────────────────────
    rsi_value = _rsi(closes, period=7)
    rsi_signal = float(np.clip((rsi_value - 50) / 50, -1.0, 1.0))

    # ── 3. Micro-Momentum (V2 enhanced) ─────────────────────
    momentum_signal = _micro_momentum(closes)

    # ── 4. Volume Spike Signal ───────────────────────────────
    avg_volume = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
    recent_volume = np.mean(volumes[-3:])
    volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1.0

    last_move = closes[-1] - closes[-4] if len(closes) >= 4 else 0
    volume_direction = 1.0 if last_move >= 0 else -1.0

    if volume_ratio > 2.0:
        volume_signal = volume_direction * 1.0
    elif volume_ratio > 1.5:
        volume_signal = volume_direction * 0.6
    elif volume_ratio > 1.2:
        volume_signal = volume_direction * 0.3
    else:
        volume_signal = 0.0

    # ── 5. VWAP Deviation Signal ─────────────────────────────
    vwap_value = _vwap(highs[-10:], lows[-10:], closes[-10:], volumes[-10:])
    vwap_dev = (current_price - vwap_value) / vwap_value * 100
    vwap_signal = float(np.clip(vwap_dev * 15, -1.0, 1.0))

    # ── V2 Composite Score (rebalanced weights) ──────────────
    score = (
        0.25 * ema_signal
        + 0.20 * rsi_signal
        + 0.25 * momentum_signal   # increased from 0.20
        + 0.15 * volume_signal
        + 0.15 * vwap_signal       # increased from 0.10
    )
    score = float(np.clip(score, -1.0, 1.0))

    # ── Volatility-adjusted confidence ───────────────────────
    # Higher ATR = more confident in directional moves
    abs_score = abs(score)
    vol_boost = min(atr_pct / 0.10, 1.0)  # normalize ATR to 0-1

    if abs_score >= 0.70 and vol_boost > 0.5:
        confidence = "HIGH"
    elif abs_score >= 0.50 or (abs_score >= 0.40 and vol_boost > 0.7):
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    if score >= 0.40:
        direction = "UP"
    elif score <= -0.40:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    result = SignalResult(
        asset=asset,
        direction=direction,
        score=score,
        ema_signal=ema_signal,
        rsi_signal=rsi_signal,
        momentum_signal=momentum_signal,
        volume_signal=volume_signal,
        vwap_signal=vwap_signal,
        rsi_value=float(rsi_value),
        current_price=float(current_price),
        ema_fast=float(ema_fast[-1]),
        ema_slow=float(ema_slow[-1]),
        confidence=confidence,
    )

    logger.debug(
        "V2 Signal %s: score=%.3f dir=%s conf=%s ATR=%.4f%% | "
        "EMA=%.3f RSI=%.3f MOM=%.3f VOL=%.3f VWAP=%.3f",
        asset, score, direction, confidence, atr_pct,
        ema_signal, rsi_signal, momentum_signal, volume_signal, vwap_signal,
    )

    return result


def compute_all_signals_v2(
    assets: dict | None = None,
) -> dict[str, SignalResult]:
    """Compute V2-enhanced signals for all configured assets."""
    from scalper.config import HFT_ASSETS
    target_assets = assets or HFT_ASSETS

    signals = {}
    for asset_key, asset_cfg in target_assets.items():
        signal = compute_signal_v2(asset_key, asset_cfg["binance_symbol"])
        if signal:
            signals[asset_key] = signal

    return signals
