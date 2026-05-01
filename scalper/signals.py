"""
scalper/signals.py — Motor de señales técnicas para scalping de 5 minutos.

Consume velas de 1 minuto de Binance y genera un composite signal score
de -1.0 (fuerte DOWN) a +1.0 (fuerte UP).

Indicadores:
  - EMA Cross (3 vs 8 períodos)
  - RSI(7) adaptado a 5 minutos
  - Momentum (rate of change 5 velas)
  - Volume Spike Detection
  - VWAP Deviation
"""

import logging
from dataclasses import dataclass

import numpy as np
import requests

from scalper.config import BINANCE_KLINES_URL

logger = logging.getLogger("polybot.scalper.signals")


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class SignalResult:
    """Result of technical analysis for a single asset."""

    asset: str
    direction: str        # "UP", "DOWN", or "NEUTRAL"
    score: float          # -1.0 to +1.0
    ema_signal: float
    rsi_signal: float
    momentum_signal: float
    volume_signal: float
    vwap_signal: float
    rsi_value: float
    current_price: float
    ema_fast: float
    ema_slow: float
    confidence: str       # "HIGH", "MEDIUM", "LOW"


# ═══════════════════════════════════════════════════════════════
# Binance data fetch
# ═══════════════════════════════════════════════════════════════


def fetch_klines(symbol: str, interval: str = "1m", limit: int = 30) -> list[dict]:
    """
    Fetch kline/candlestick data from Binance.

    Returns list of dicts with: open, high, low, close, volume, timestamp
    """
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }

    try:
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=8)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as exc:
        logger.warning("Error fetching klines for %s: %s", symbol, exc)
        return []

    klines = []
    for candle in raw:
        klines.append({
            "timestamp": int(candle[0]),
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
            "close_time": int(candle[6]),
            "quote_volume": float(candle[7]),
            "trades": int(candle[8]),
        })

    return klines


# ═══════════════════════════════════════════════════════════════
# Technical indicators
# ═══════════════════════════════════════════════════════════════


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Calculate Exponential Moving Average."""
    alpha = 2.0 / (period + 1)
    ema_arr = np.zeros_like(data, dtype=float)
    ema_arr[0] = data[0]
    for i in range(1, len(data)):
        ema_arr[i] = alpha * data[i] + (1 - alpha) * ema_arr[i - 1]
    return ema_arr


def _rsi(closes: np.ndarray, period: int = 7) -> float:
    """Calculate RSI for the most recent point."""
    if len(closes) < period + 1:
        return 50.0  # neutral default

    deltas = np.diff(closes)
    recent_deltas = deltas[-(period):]

    gains = np.where(recent_deltas > 0, recent_deltas, 0)
    losses = np.where(recent_deltas < 0, -recent_deltas, 0)

    avg_gain = np.mean(gains) if len(gains) > 0 else 0
    avg_loss = np.mean(losses) if len(losses) > 0 else 0

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi_value = 100.0 - (100.0 / (1.0 + rs))
    return rsi_value


def _vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
          volumes: np.ndarray) -> float:
    """Calculate Volume-Weighted Average Price."""
    typical_price = (highs + lows + closes) / 3.0
    cumulative_tp_vol = np.sum(typical_price * volumes)
    cumulative_vol = np.sum(volumes)

    if cumulative_vol == 0:
        return closes[-1]
    return cumulative_tp_vol / cumulative_vol


# ═══════════════════════════════════════════════════════════════
# Signal computation
# ═══════════════════════════════════════════════════════════════


def compute_signal(asset: str, binance_symbol: str) -> SignalResult | None:
    """
    Compute composite trading signal for an asset.

    Fetches 30 × 1m candles from Binance and runs:
      1. EMA(3) vs EMA(8) cross — trend direction
      2. RSI(7) — overbought/oversold
      3. Momentum — 5-candle rate of change
      4. Volume Spike — unusual volume detection
      5. VWAP Deviation — price vs volume-weighted average

    Returns SignalResult with score from -1.0 (DOWN) to +1.0 (UP).
    """
    klines = fetch_klines(binance_symbol, interval="1m", limit=30)
    if len(klines) < 15:
        logger.warning("Insufficient kline data for %s (%d candles)",
                        asset, len(klines))
        return None

    # Extract arrays
    closes = np.array([k["close"] for k in klines])
    highs = np.array([k["high"] for k in klines])
    lows = np.array([k["low"] for k in klines])
    volumes = np.array([k["volume"] for k in klines])

    current_price = closes[-1]

    # ── 1. EMA Cross Signal ──────────────────────────────────
    ema_fast = _ema(closes, 3)
    ema_slow = _ema(closes, 8)

    ema_diff = ema_fast[-1] - ema_slow[-1]
    ema_diff_prev = ema_fast[-2] - ema_slow[-2]

    # Normalize by price to get percentage difference
    ema_pct = (ema_diff / current_price) * 100

    # Scale: strong cross = ±1.0, weak = ±0.3
    ema_signal = np.clip(ema_pct * 20, -1.0, 1.0)

    # Bonus for fresh crossover
    if ema_diff > 0 and ema_diff_prev <= 0:
        ema_signal = min(ema_signal + 0.3, 1.0)  # bullish cross
    elif ema_diff < 0 and ema_diff_prev >= 0:
        ema_signal = max(ema_signal - 0.3, -1.0)  # bearish cross

    # ── 2. RSI Signal ────────────────────────────────────────
    rsi_value = _rsi(closes, period=7)

    # Map RSI to signal: <35 = bullish (oversold bounce), >65 = bearish
    # For 5m scalping, we use it as momentum confirmation
    if rsi_value >= 65:
        rsi_signal = (rsi_value - 50) / 50  # positive = UP momentum
    elif rsi_value <= 35:
        rsi_signal = (rsi_value - 50) / 50  # negative = DOWN momentum
    else:
        rsi_signal = (rsi_value - 50) / 50  # linear in neutral zone

    rsi_signal = np.clip(rsi_signal, -1.0, 1.0)

    # ── 3. Momentum Signal ───────────────────────────────────
    # Rate of change over last 5 candles
    if len(closes) >= 6:
        roc = (closes[-1] - closes[-6]) / closes[-6] * 100
    else:
        roc = 0

    # Scale: ±0.1% change = ±1.0 signal for crypto
    momentum_signal = np.clip(roc * 10, -1.0, 1.0)

    # ── 4. Volume Spike Signal ───────────────────────────────
    avg_volume = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
    recent_volume = np.mean(volumes[-3:])

    if avg_volume > 0:
        volume_ratio = recent_volume / avg_volume
    else:
        volume_ratio = 1.0

    # Volume spike amplifies the direction of price movement
    last_move = closes[-1] - closes[-4] if len(closes) >= 4 else 0
    volume_direction = 1.0 if last_move >= 0 else -1.0

    if volume_ratio > 2.0:
        volume_signal = volume_direction * 1.0  # strong spike
    elif volume_ratio > 1.5:
        volume_signal = volume_direction * 0.6
    elif volume_ratio > 1.2:
        volume_signal = volume_direction * 0.3
    else:
        volume_signal = 0.0  # no notable volume change

    # ── 5. VWAP Deviation Signal ─────────────────────────────
    vwap_value = _vwap(highs[-10:], lows[-10:], closes[-10:], volumes[-10:])

    vwap_dev = (current_price - vwap_value) / vwap_value * 100
    vwap_signal = np.clip(vwap_dev * 15, -1.0, 1.0)

    # ── Composite Score ──────────────────────────────────────
    score = (
        0.30 * ema_signal
        + 0.25 * rsi_signal
        + 0.20 * momentum_signal
        + 0.15 * volume_signal
        + 0.10 * vwap_signal
    )

    score = np.clip(score, -1.0, 1.0)

    # Direction and confidence
    if score >= 0.40:
        direction = "UP"
    elif score <= -0.40:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    abs_score = abs(score)
    if abs_score >= 0.70:
        confidence = "HIGH"
    elif abs_score >= 0.40:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    result = SignalResult(
        asset=asset,
        direction=direction,
        score=float(score),
        ema_signal=float(ema_signal),
        rsi_signal=float(rsi_signal),
        momentum_signal=float(momentum_signal),
        volume_signal=float(volume_signal),
        vwap_signal=float(vwap_signal),
        rsi_value=float(rsi_value),
        current_price=float(current_price),
        ema_fast=float(ema_fast[-1]),
        ema_slow=float(ema_slow[-1]),
        confidence=confidence,
    )

    logger.debug(
        "Signal %s: score=%.3f dir=%s conf=%s | "
        "EMA=%.3f RSI=%.3f MOM=%.3f VOL=%.3f VWAP=%.3f",
        asset, score, direction, confidence,
        ema_signal, rsi_signal, momentum_signal, volume_signal, vwap_signal,
    )

    return result


def compute_all_signals(
    assets: dict | None = None,
) -> dict[str, SignalResult]:
    """Compute signals for all configured assets."""
    from scalper.config import HFT_ASSETS
    target_assets = assets or HFT_ASSETS

    signals = {}
    for asset_key, asset_cfg in target_assets.items():
        signal = compute_signal(asset_key, asset_cfg["binance_symbol"])
        if signal:
            signals[asset_key] = signal

    return signals
