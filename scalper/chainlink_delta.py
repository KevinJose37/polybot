"""
scalper/chainlink_delta.py — Chainlink Delta Signal for V3 strategy.

Monitors the delta between Binance spot price and a Chainlink oracle proxy.
Polymarket resolves on Chainlink BTC/USD feed, so if spot has moved but
Chainlink hasn't updated yet, we can predict the resolution direction.

Data sources:
  - Spot: Binance ticker API (real-time)
  - Chainlink proxy: CryptoCompare aggregate price
    (CryptoCompare is one of Chainlink's data sources)
"""

import logging
import time
from collections import deque
from dataclasses import dataclass

import requests

from scalper.signals import SignalResult

logger = logging.getLogger("polybot.scalper.chainlink_delta")

# ═══════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/price"

# Map asset keys to their symbols
ASSET_SYMBOLS = {
    "BTC": {"binance": "BTCUSDT", "cc_fsym": "BTC"},
    "ETH": {"binance": "ETHUSDT", "cc_fsym": "ETH"},
    "SOL": {"binance": "SOLUSDT", "cc_fsym": "SOL"},
    "XRP": {"binance": "XRPUSDT", "cc_fsym": "XRP"},
}

BUFFER_SIZE = 10  # Keep last 10 readings (20 seconds at 2s interval)


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class DeltaReading:
    """Single delta measurement between spot and oracle."""
    timestamp: float
    spot_price: float
    oracle_price: float
    delta: float          # spot - oracle
    delta_pct: float      # (delta / oracle) * 100


class ChainlinkDeltaMonitor:
    """
    Monitors the price delta between Binance spot and Chainlink oracle.

    Usage:
        monitor = ChainlinkDeltaMonitor()
        monitor.update("BTC")  # call every 2 seconds
        delta = monitor.get_delta("BTC")
        signal = monitor.get_signal("BTC")
    """

    def __init__(self):
        # Buffer of recent readings per asset
        self._buffers: dict[str, deque[DeltaReading]] = {}
        self._last_update: dict[str, float] = {}

    def _get_buffer(self, asset: str) -> deque[DeltaReading]:
        """Get or create the reading buffer for an asset."""
        if asset not in self._buffers:
            self._buffers[asset] = deque(maxlen=BUFFER_SIZE)
        return self._buffers[asset]

    def _fetch_spot_price(self, asset: str) -> float | None:
        """Fetch current spot price from Binance."""
        symbols = ASSET_SYMBOLS.get(asset)
        if not symbols:
            return None

        try:
            resp = requests.get(
                BINANCE_TICKER_URL,
                params={"symbol": symbols["binance"]},
                timeout=5,
            )
            resp.raise_for_status()
            return float(resp.json()["price"])
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.warning("Spot price fetch failed for %s: %s", asset, exc)
            return None

    def _fetch_oracle_price(self, asset: str) -> float | None:
        """
        Fetch Chainlink-proxy price from CryptoCompare.

        CryptoCompare aggregates prices from multiple exchanges
        and is one of Chainlink's data sources, making it a
        reasonable proxy for the oracle price.
        """
        symbols = ASSET_SYMBOLS.get(asset)
        if not symbols:
            return None

        try:
            resp = requests.get(
                CRYPTOCOMPARE_URL,
                params={"fsym": symbols["cc_fsym"], "tsyms": "USD"},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("USD", 0))
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.warning("Oracle price fetch failed for %s: %s", asset, exc)
            return None

    def update(self, asset: str) -> DeltaReading | None:
        """
        Fetch current prices and record a new delta reading.

        Should be called every ~2 seconds for accurate delta tracking.
        """
        spot = self._fetch_spot_price(asset)
        oracle = self._fetch_oracle_price(asset)

        if spot is None or oracle is None or oracle == 0:
            return None

        delta = spot - oracle
        delta_pct = (delta / oracle) * 100

        reading = DeltaReading(
            timestamp=time.time(),
            spot_price=spot,
            oracle_price=oracle,
            delta=delta,
            delta_pct=delta_pct,
        )

        buf = self._get_buffer(asset)
        buf.append(reading)
        self._last_update[asset] = time.time()

        logger.debug(
            "Delta %s: spot=%.2f oracle=%.2f delta=%.2f (%.4f%%)",
            asset, spot, oracle, delta, delta_pct,
        )

        return reading

    def update_all(self, assets: list[str] | None = None) -> dict[str, DeltaReading | None]:
        """Update delta readings for all assets."""
        target = assets or list(ASSET_SYMBOLS.keys())
        results = {}
        for asset in target:
            results[asset] = self.update(asset)
        return results

    def get_delta(self, asset: str) -> dict | None:
        """
        Get current delta analysis for an asset.

        Returns dict with:
          - delta_pct: current percentage delta
          - direction: "UP", "DOWN", or "NEUTRAL"
          - sustained: True if delta has been consistent for 3+ readings
          - avg_delta_pct: average delta over buffer
          - readings_count: number of readings in buffer
        """
        buf = self._get_buffer(asset)
        if len(buf) < 2:
            return None

        latest = buf[-1]
        deltas = [r.delta_pct for r in buf]
        avg_delta = sum(deltas) / len(deltas)

        # Check if delta is sustained in one direction
        recent = list(buf)[-3:] if len(buf) >= 3 else list(buf)
        all_positive = all(r.delta_pct > 0.02 for r in recent)
        all_negative = all(r.delta_pct < -0.02 for r in recent)
        sustained = all_positive or all_negative

        if avg_delta > 0.02:
            direction = "UP"
        elif avg_delta < -0.02:
            direction = "DOWN"
        else:
            direction = "NEUTRAL"

        return {
            "delta_pct": latest.delta_pct,
            "avg_delta_pct": avg_delta,
            "direction": direction,
            "sustained": sustained,
            "spot_price": latest.spot_price,
            "oracle_price": latest.oracle_price,
            "readings_count": len(buf),
        }

    def get_signal(
        self,
        asset: str,
        threshold: float = 0.05,
        min_readings: int = 3,
    ) -> SignalResult | None:
        """
        Generate a trading signal based on the Chainlink delta.

        The signal is based on sustained price divergence between
        spot (Binance) and oracle (Chainlink proxy).

        Args:
            asset: Asset key (e.g., "BTC")
            threshold: minimum |delta_pct| to generate signal
            min_readings: minimum consecutive aligned readings
        """
        delta_info = self.get_delta(asset)
        if not delta_info:
            return None

        delta_pct = delta_info["delta_pct"]
        avg_delta = delta_info["avg_delta_pct"]
        sustained = delta_info["sustained"]
        spot_price = delta_info["spot_price"]

        # Score based on delta magnitude
        # Normalize: 0.05% → 0.5 score, 0.10% → 1.0 score
        raw_score = avg_delta / 0.10  # linear scaling
        score = float(max(-1.0, min(1.0, raw_score)))

        # Only generate directional signal if sustained
        if abs(avg_delta) >= threshold and sustained:
            direction = "UP" if avg_delta > 0 else "DOWN"
        elif abs(avg_delta) >= threshold:
            # Delta present but not sustained — weaker signal
            score *= 0.6
            direction = "UP" if avg_delta > 0 else "DOWN"
        else:
            direction = "NEUTRAL"
            score *= 0.3

        # Confidence based on sustain and magnitude
        if sustained and abs(avg_delta) >= 0.08:
            confidence = "HIGH"
        elif sustained or abs(avg_delta) >= 0.05:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return SignalResult(
            asset=asset,
            direction=direction,
            score=score,
            ema_signal=0.0,       # not used in delta strategy
            rsi_signal=0.0,
            momentum_signal=0.0,
            volume_signal=0.0,
            vwap_signal=0.0,
            rsi_value=50.0,
            current_price=spot_price,
            ema_fast=0.0,
            ema_slow=0.0,
            confidence=confidence,
        )


def compute_all_signals_chainlink(
    monitor: ChainlinkDeltaMonitor,
    assets: dict | None = None,
    threshold: float = 0.05,
    technical_signals: dict[str, SignalResult] | None = None,
    require_confirmation: bool = True,
) -> dict[str, SignalResult]:
    """
    Compute Chainlink delta signals for all assets.

    If require_confirmation=True, also checks that the technical
    signal (from V1) agrees with the delta direction.

    Args:
        monitor: ChainlinkDeltaMonitor instance
        assets: Asset config dict
        threshold: delta_pct threshold
        technical_signals: V1 technical signals for confirmation
        require_confirmation: require tech signal alignment
    """
    from scalper.config import HFT_ASSETS
    target_assets = assets or HFT_ASSETS

    # Update all delta readings
    monitor.update_all(list(target_assets.keys()))

    signals = {}
    for asset_key in target_assets:
        signal = monitor.get_signal(asset_key, threshold=threshold)
        if not signal:
            continue

        # Confirmation check: delta and technical must agree
        if require_confirmation and technical_signals:
            tech = technical_signals.get(asset_key)
            if tech and tech.direction != "NEUTRAL":
                if signal.direction != tech.direction:
                    logger.debug(
                        "V3 %s: Delta says %s but tech says %s → SKIP",
                        asset_key, signal.direction, tech.direction,
                    )
                    # Override to NEUTRAL if signals conflict
                    signal = SignalResult(
                        asset=asset_key,
                        direction="NEUTRAL",
                        score=signal.score * 0.2,
                        ema_signal=0.0, rsi_signal=0.0,
                        momentum_signal=0.0, volume_signal=0.0,
                        vwap_signal=0.0, rsi_value=50.0,
                        current_price=signal.current_price,
                        ema_fast=0.0, ema_slow=0.0,
                        confidence="LOW",
                    )

        signals[asset_key] = signal

    return signals
