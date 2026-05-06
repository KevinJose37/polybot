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


# ═══════════════════════════════════════════════════════════════
# V6 Poly Velocity Signal Engine
# ═══════════════════════════════════════════════════════════════


def compute_all_signals_poly_velocity(
    assets: dict,
    markets: dict,
    tick_manager=None,
) -> dict[str, SignalResult]:
    """
    V6 signal engine: Polymarket orderbook velocity as primary signal.

    Uses mid-price velocity from the WS orderbook — available within
    5-10 seconds of market open (vs 30-60s for Binance tick warmup).

    Signal flow:
      1. Read mid-price velocity over 10s and 30s windows
      2. Weighted combo: 70% short-term + 30% trend
      3. Scale to [-1, 1] score
      4. Optional: if Binance ticks are warm, halve score on disagreement

    Args:
        assets: Asset config dict (e.g. HFT_ASSETS)
        markets: Market data dict with up_token_id, up_price, etc.
        tick_manager: Optional BinanceTickManager for confirmation
    """
    from scalper.orderbook_ws import get_mid_velocity, get_price
    from scalper.config import HFT_ASSETS

    target_assets = assets or HFT_ASSETS
    signals = {}

    for asset_key in target_assets:
        if asset_key not in markets:
            continue

        market = markets[asset_key]
        up_token_id = market.get("up_token_id", "")

        if not up_token_id:
            continue

        # ── Primary signal: Polymarket mid-price velocity ─────────
        vel_10 = get_mid_velocity(up_token_id, window_sec=10)
        vel_30 = get_mid_velocity(up_token_id, window_sec=30)

        # Debug: show buffer state to diagnose velocity=0
        from scalper.orderbook_ws import debug_mid_history
        dbg = debug_mid_history(up_token_id)
        if dbg["entries"] == 0:
            print(f"  [V6-DBG] {asset_key}: NO BUFFER (token not in _mid_history) token={up_token_id[:20]}...")
        else:
            print(
                f"  [V6-DBG] {asset_key}: buf={dbg['entries']} | "
                f"10s={dbg['in_10s']}samples/{dbg['unique_prices_10s']}unique | "
                f"30s={dbg['in_30s']} | mid=${dbg['latest_mid']:.4f} | "
                f"age={dbg['age_newest_sec']}s"
            )

        # Weighted combo: react fast (70%) but confirm with trend (30%)
        velocity = 0.7 * vel_10 + 0.3 * vel_30

        # Scale to [-1, 1]:  $0.01 move → ~0.20,  $0.02 → ~0.40
        score = float(np.clip(velocity * 20, -1.0, 1.0))

        # ── Optional Binance confirmation ─────────────────────────
        binance_agrees = None  # None = no data, True/False = verdict
        if tick_manager and tick_manager.is_warm(asset_key):
            ticks = tick_manager.get_ticks(asset_key, count=30)
            if len(ticks) >= 10:
                recent = np.mean([t.price for t in ticks[-10:]])
                older = np.mean([t.price for t in ticks[:10]])
                binance_dir = recent - older

                # Disagreement: Poly says UP but Binance says DOWN (or vice versa)
                if (score > 0 and binance_dir < 0) or (score < 0 and binance_dir > 0):
                    score *= 0.5  # Halve confidence on conflict
                    binance_agrees = False
                else:
                    binance_agrees = True

        # ── Direction ─────────────────────────────────────────────
        if score > 0.05:
            direction = "UP"
        elif score < -0.05:
            direction = "DOWN"
        else:
            direction = "NEUTRAL"

        # ── Confidence ────────────────────────────────────────────
        abs_score = abs(score)
        if abs_score >= 0.40 and binance_agrees is not False:
            confidence = "HIGH"
        elif abs_score >= 0.20:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        current_price = get_price(up_token_id) or market.get("up_price", 0.5)

        signals[asset_key] = SignalResult(
            asset=asset_key,
            direction=direction,
            score=score,
            ema_signal=float(vel_10),       # repurposed: 10s velocity
            rsi_signal=float(vel_30),       # repurposed: 30s velocity
            momentum_signal=float(velocity),
            volume_signal=0.0,
            vwap_signal=0.0,
            rsi_value=50.0,
            current_price=float(current_price),
            ema_fast=0.0,
            ema_slow=0.0,
            confidence=confidence,
        )

        # ── Diagnostic print ─────────────────────────────────────
        binance_str = (
            "AGREE" if binance_agrees is True
            else "DISAGREE (-50%)" if binance_agrees is False
            else "N/A (cold)"
        )
        print(
            f"  [V6-POLY] {asset_key}: "
            f"vel10={vel_10:+.4f} vel30={vel_30:+.4f} → "
            f"score={score:+.3f} dir={direction} "
            f"| Binance={binance_str}"
        )
    # ── Quiet-market fallback ────────────────────────────────────
    # If ALL velocities are zero after 45s, the market is quiet but
    # in-band prices (0.45-0.55) are still tradeable. A quiet market
    # at $0.50 means nobody knows the direction → coin flip with +10%
    # TP is still +EV if spread is tight.
    all_neutral = all(
        s.direction == "NEUTRAL" for s in signals.values()
    ) if signals else True

    if all_neutral and markets:
        from datetime import datetime, timezone

        # Guard 1: require WS to have live prices — no blind entries
        try:
            from scalper.orderbook_ws import get_status as ws_status
            ws_info = ws_status()
            if ws_info.get("active_prices", 0) == 0:
                print("  [V6-FALLBACK] BLOCKED: WS has 0 prices live → skipping")
                return signals
        except Exception:
            pass

        for asset_key in target_assets:
            if asset_key not in markets or asset_key not in signals:
                continue

            market = markets[asset_key]
            event_start = market.get("event_start")
            if not event_start:
                continue

            elapsed = (datetime.now(timezone.utc) - event_start).total_seconds()
            if elapsed < 45:
                continue  # Too early for fallback

            # Check if price is in the golden zone
            up_price = market.get("up_price", 0.5)
            if not (0.45 <= up_price <= 0.55):
                continue

            # Guard 2: reject if price drifted too far from 0.50
            # A market that moved $0.03+ is NOT quiet — it chose a direction
            drift = abs(up_price - 0.50)
            if drift > 0.03:
                print(
                    f"  [V6-FALLBACK] {asset_key}: SKIP — price drifted "
                    f"${drift:.2f} from $0.50 (now ${up_price:.2f})"
                )
                continue

            # Use Binance ticks for direction if available (even partial)
            fallback_dir = None
            if tick_manager:
                ticks = tick_manager.get_ticks(asset_key, count=20)
                if len(ticks) >= 5:
                    recent_avg = np.mean([t.price for t in ticks[-5:]])
                    older_avg = np.mean([t.price for t in ticks[:5]])
                    if recent_avg > older_avg:
                        fallback_dir = "UP"
                    elif recent_avg < older_avg:
                        fallback_dir = "DOWN"

            if not fallback_dir:
                # No Binance data → use whichever side is cheaper (better R/R)
                fallback_dir = "DOWN" if up_price >= 0.50 else "UP"

            # Generate minimum-confidence signal (just above typical threshold)
            fallback_score = 0.16 if fallback_dir == "UP" else -0.16

            signals[asset_key] = SignalResult(
                asset=asset_key,
                direction=fallback_dir,
                score=fallback_score,
                ema_signal=0.0,
                rsi_signal=0.0,
                momentum_signal=0.0,
                volume_signal=0.0,
                vwap_signal=0.0,
                rsi_value=50.0,
                current_price=float(up_price),
                ema_fast=0.0,
                ema_slow=0.0,
                confidence="LOW",
            )

            binance_tag = f"Binance={fallback_dir}" if tick_manager else "coin-flip"
            print(
                f"  [V6-FALLBACK] {asset_key}: quiet market @ ${up_price:.2f} "
                f"after {elapsed:.0f}s → {fallback_dir} (score={fallback_score:+.2f}, {binance_tag})"
            )

    return signals
