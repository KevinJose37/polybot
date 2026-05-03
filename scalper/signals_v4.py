"""
scalper/signals_v4.py — V4 Real-Time Tick Signal Engine.

Uses Binance WebSocket ticks instead of 1-minute klines.
Adds Polymarket price as a 6th contrarian indicator (Form B).

Indicators:
  1. Tick EMA Cross (20 vs 50 ticks)     — 25%
  2. Tick Momentum (weighted last 30)     — 25%
  3. Buy/Sell Pressure (upticks ratio)    — 20%
  4. Tick RSI (14-tick)                   — 15%
  5. Polymarket Contrarian Signal         — 15%
"""

import logging

import numpy as np

from scalper.binance_ws import BinanceTickManager, Tick
from scalper.signals import SignalResult, _ema, _rsi, fetch_klines

logger = logging.getLogger("polybot.scalper.signals_v4")


def _tick_prices(ticks: list[Tick]) -> np.ndarray:
    """Extract price array from ticks."""
    return np.array([t.price for t in ticks])


def _tick_ema_signal(prices: np.ndarray) -> float:
    """
    EMA cross signal using tick data.
    Fast EMA(20 ticks) vs Slow EMA(50 ticks).
    """
    if len(prices) < 55:
        return 0.0

    fast = _ema(prices, 20)
    slow = _ema(prices, 50)

    current_price = prices[-1]
    if current_price == 0:
        return 0.0

    diff = fast[-1] - slow[-1]
    diff_prev = fast[-2] - slow[-2]
    pct = (diff / current_price) * 100

    signal = float(np.clip(pct * 30, -1.0, 1.0))

    # Bonus for fresh crossover
    if diff > 0 and diff_prev <= 0:
        signal = min(signal + 0.3, 1.0)
    elif diff < 0 and diff_prev >= 0:
        signal = max(signal - 0.3, -1.0)

    return signal


def _tick_momentum(prices: np.ndarray) -> float:
    """
    Exponential-weighted momentum over last 30 ticks.
    More weight on most recent ticks.
    """
    if len(prices) < 35:
        return 0.0

    # Last 30 tick-to-tick changes
    recent = prices[-30:]
    changes = np.diff(recent) / recent[:-1] * 100

    # Exponential weights (newest = highest)
    n = len(changes)
    weights = np.exp(np.linspace(0, 2, n))
    weights /= weights.sum()

    weighted_mom = float(np.dot(changes, weights))

    return float(np.clip(weighted_mom * 20, -1.0, 1.0))


def _tick_pressure(ticks: list[Tick]) -> float:
    """
    Buy/sell pressure from aggressor side.

    is_buyer_maker=True → seller initiated (sell pressure)
    is_buyer_maker=False → buyer initiated (buy pressure)

    Returns signal in [-1, +1]:
      +1 = all buy pressure
      -1 = all sell pressure
    """
    if len(ticks) < 20:
        return 0.0

    recent = ticks[-50:]

    buy_volume = sum(t.quantity for t in recent if not t.is_buyer_maker)
    sell_volume = sum(t.quantity for t in recent if t.is_buyer_maker)
    total = buy_volume + sell_volume

    if total == 0:
        return 0.0

    # Ratio from -1 (all sell) to +1 (all buy)
    imbalance = (buy_volume - sell_volume) / total

    return float(np.clip(imbalance * 2, -1.0, 1.0))


def _tick_rsi(prices: np.ndarray, period: int = 14) -> float:
    """RSI calculated on tick prices."""
    if len(prices) < period + 2:
        return 50.0
    return _rsi(prices, period)


def _poly_contrarian_signal(up_price: float) -> float:
    """
    Polymarket price as contrarian indicator (Form B).

    Logic: If the market already prices UP at 0.65, the "easy money"
    is gone. Contrarian bonus when market hasn't reacted yet.

    up_price=0.40 → +0.20 (market leans DOWN, UP contrarian bonus)
    up_price=0.50 → +0.00 (neutral)
    up_price=0.60 → -0.20 (market already priced UP, penalty)
    """
    return float(np.clip((0.50 - up_price) * 2, -1.0, 1.0))


def compute_signal_v4(
    asset: str,
    tick_manager: BinanceTickManager,
    up_price: float = 0.50,
    down_price: float = 0.50,
) -> SignalResult | None:
    """
    V4 composite signal using real-time ticks + Polymarket price.

    Falls back to kline-based signal if WebSocket isn't warm yet.
    """
    # Check if tick buffer is warm
    if not tick_manager.is_warm(asset):
        warmup = tick_manager.get_warmup_status().get(asset, {})
        current = warmup.get("ticks", 0)
        needed = warmup.get("needed", 50)
        logger.info(
            "[V4-WARMUP] %s: %d/%d ticks — using kline fallback",
            asset, current, needed,
        )
        return _fallback_kline_signal(asset, up_price)

    ticks = tick_manager.get_ticks(asset, count=300)
    prices = _tick_prices(ticks)

    if len(prices) < 55:
        return _fallback_kline_signal(asset, up_price)

    current_price = prices[-1]

    # ── 1. Tick EMA Cross ────────────────────────────────────
    ema_signal = _tick_ema_signal(prices)

    # ── 2. Tick Momentum ─────────────────────────────────────
    momentum_signal = _tick_momentum(prices)

    # ── 3. Buy/Sell Pressure ─────────────────────────────────
    pressure_signal = _tick_pressure(ticks)

    # ── 4. Tick RSI ──────────────────────────────────────────
    rsi_value = _tick_rsi(prices, period=14)
    rsi_signal = float(np.clip((rsi_value - 50) / 50, -1.0, 1.0))

    # ── 5. Polymarket Contrarian ─────────────────────────────
    poly_signal = _poly_contrarian_signal(up_price)

    # ── V4 Composite Score ───────────────────────────────────
    score = (
        0.25 * ema_signal
        + 0.25 * momentum_signal
        + 0.20 * pressure_signal
        + 0.15 * rsi_signal
        + 0.15 * poly_signal
    )
    score = float(np.clip(score, -1.0, 1.0))

    # ── Direction & Confidence ───────────────────────────────
    abs_score = abs(score)

    if score >= 0.35:
        direction = "UP"
    elif score <= -0.35:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    # Confidence: requires multiple indicators to align
    aligned = sum(1 for s in [ema_signal, momentum_signal, pressure_signal, rsi_signal]
                  if (s > 0) == (score > 0) and abs(s) > 0.1)

    if aligned >= 3 and abs_score >= 0.50:
        confidence = "HIGH"
    elif aligned >= 2 and abs_score >= 0.35:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    result = SignalResult(
        asset=asset,
        direction=direction,
        score=score,
        ema_signal=ema_signal,
        rsi_signal=rsi_signal,
        momentum_signal=momentum_signal,
        volume_signal=pressure_signal,    # repurposed: pressure instead of volume
        vwap_signal=poly_signal,          # repurposed: poly signal instead of VWAP
        rsi_value=float(rsi_value),
        current_price=float(current_price),
        ema_fast=0.0,
        ema_slow=0.0,
        confidence=confidence,
    )

    logger.debug(
        "V4 %s: score=%.3f dir=%s conf=%s | "
        "EMA=%.3f MOM=%.3f PRES=%.3f RSI=%.3f POLY=%.3f | up$=%.2f",
        asset, score, direction, confidence,
        ema_signal, momentum_signal, pressure_signal, rsi_signal, poly_signal,
        up_price,
    )

    return result


def _fallback_kline_signal(asset: str, up_price: float) -> SignalResult | None:
    """
    Fallback: use kline-based signal when WebSocket isn't warm.
    Incorporates Polymarket price as extra indicator.
    """
    from scalper.config import HFT_ASSETS

    asset_cfg = HFT_ASSETS.get(asset)
    if not asset_cfg:
        return None

    binance_symbol = asset_cfg["binance_symbol"]
    klines = fetch_klines(binance_symbol, interval="1m", limit=15)

    if len(klines) < 10:
        return None

    closes = np.array([k["close"] for k in klines])
    current_price = closes[-1]

    # Simple kline-based signal
    ema_fast = _ema(closes, 3)
    ema_slow = _ema(closes, 8)
    ema_diff = (ema_fast[-1] - ema_slow[-1]) / current_price * 100
    ema_signal = float(np.clip(ema_diff * 20, -1.0, 1.0))

    rsi_value = _rsi(closes, period=7)
    rsi_signal = float(np.clip((rsi_value - 50) / 50, -1.0, 1.0))

    poly_signal = _poly_contrarian_signal(up_price)

    # Simplified score with poly signal
    score = 0.40 * ema_signal + 0.35 * rsi_signal + 0.25 * poly_signal
    score = float(np.clip(score, -1.0, 1.0))

    if score >= 0.35:
        direction = "UP"
    elif score <= -0.35:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    return SignalResult(
        asset=asset,
        direction=direction,
        score=score,
        ema_signal=ema_signal,
        rsi_signal=rsi_signal,
        momentum_signal=0.0,
        volume_signal=0.0,
        vwap_signal=poly_signal,
        rsi_value=float(rsi_value),
        current_price=float(current_price),
        ema_fast=float(ema_fast[-1]),
        ema_slow=float(ema_slow[-1]),
        confidence="LOW",  # fallback always LOW confidence
    )


def compute_all_signals_v4(
    tick_manager: BinanceTickManager,
    assets: dict | None = None,
    markets: dict | None = None,
) -> dict[str, SignalResult]:
    """
    Compute V4 signals for all assets.

    Args:
        tick_manager: BinanceTickManager with live ticks
        assets: Asset config dict
        markets: Market data dict with up_price/down_price
    """
    from scalper.config import HFT_ASSETS
    target_assets = assets or HFT_ASSETS

    signals = {}
    for asset_key in target_assets:
        # Get Polymarket prices from market data
        up_price = 0.50
        down_price = 0.50
        if markets and asset_key in markets:
            up_price = markets[asset_key].get("up_price", 0.50)
            down_price = markets[asset_key].get("down_price", 0.50)

        signal = compute_signal_v4(
            asset_key, tick_manager,
            up_price=up_price,
            down_price=down_price,
        )
        if signal:
            signals[asset_key] = signal

    return signals
