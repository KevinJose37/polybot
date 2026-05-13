"""
risk/toxicity_monitor.py — Order flow toxicity detection.
Implements a VPIN-inspired metric to detect informed/toxic flow
and adapt quoting behavior defensively.
"""

import time
from collections import deque
from loguru import logger

from config.settings import config
from utils.schemas import ToxicityMetrics, ToxicityLevel, TradeEvent


class ToxicityMonitor:
    """
    Monitors order flow toxicity using a rolling window of recent trades.

    Metric: order_imbalance = abs(buy_vol - sell_vol) / total_vol
    - 0.0 = perfectly balanced (uninformed flow)
    - 1.0 = completely one-sided (informed/toxic flow)

    Thresholds (from strategy doc Section G.2):
    - < 0.40: NORMAL — standard spreads
    - 0.40–0.60: MILD — widen spread 20%
    - 0.60–0.75: DIRECTIONAL — widen spread 50%, reduce size
    - 0.75–0.90: HIGHLY_DIRECTIONAL — one-sided quoting, aggressive inventory mgmt
    - > 0.90: EXTREME — suspend quoting, emergency inventory reduction
    """

    # Toxicity level thresholds and their multipliers
    LEVELS = [
        # (min_imbalance, max_imbalance, level, spread_mult, size_mult)
        (0.00, 0.40, ToxicityLevel.NORMAL, 1.0, 1.0),
        (0.40, 0.60, ToxicityLevel.MILD, 1.2, 1.0),
        (0.60, 0.75, ToxicityLevel.DIRECTIONAL, 1.5, 0.7),
        (0.75, 0.90, ToxicityLevel.HIGHLY_DIRECTIONAL, 2.5, 0.5),
        (0.90, 1.01, ToxicityLevel.EXTREME, config.defensive_spread_multiplier, 0.0),
    ]

    def __init__(self, window_size: int = 0):
        """
        Args:
            window_size: Number of trades in rolling window. 0 = use config default.
        """
        self.window_size = window_size or config.toxicity_window_trades

        # Per-market rolling trade windows
        self._trade_windows: dict[str, deque] = {}  # market_key -> deque of (side, volume)

        # Current metrics per market
        self._metrics: dict[str, ToxicityMetrics] = {}

    def record_trade(self, market_key: str, trade: TradeEvent):
        """
        Record a new trade and update toxicity metrics for the market.

        Args:
            market_key: Identifier like "btcusdt_60"
            trade: The trade event with side info
        """
        if market_key not in self._trade_windows:
            self._trade_windows[market_key] = deque(maxlen=self.window_size)

        window = self._trade_windows[market_key]

        # Determine trade side: is_buyer_maker=True means sell-initiated (seller aggressed)
        side = "SELL" if trade.is_buyer_maker else "BUY"
        volume = trade.price * trade.quantity

        window.append((side, volume))

        # Recompute metrics
        self._metrics[market_key] = self._compute_metrics(market_key)

    def record_fill(self, market_key: str, side: str, volume: float):
        """
        Record a fill (from Polymarket) directly without a TradeEvent.
        Useful for tracking our own fills for toxicity.
        """
        if market_key not in self._trade_windows:
            self._trade_windows[market_key] = deque(maxlen=self.window_size)

        self._trade_windows[market_key].append((side.upper(), volume))
        self._metrics[market_key] = self._compute_metrics(market_key)

    def get_toxicity(self, market_key: str) -> ToxicityMetrics:
        """Get current toxicity metrics for a market."""
        if market_key in self._metrics:
            return self._metrics[market_key]
        return ToxicityMetrics()  # Default: NORMAL

    def _compute_metrics(self, market_key: str) -> ToxicityMetrics:
        """Compute toxicity metrics from the rolling trade window."""
        window = self._trade_windows.get(market_key)
        if not window or len(window) == 0:
            return ToxicityMetrics()

        buy_volume = 0.0
        sell_volume = 0.0

        for side, vol in window:
            if side == "BUY":
                buy_volume += vol
            else:
                sell_volume += vol

        total_volume = buy_volume + sell_volume
        if total_volume == 0:
            return ToxicityMetrics()

        order_imbalance = abs(buy_volume - sell_volume) / total_volume

        # Classify toxicity level
        level = ToxicityLevel.NORMAL
        spread_mult = 1.0
        size_mult = 1.0

        for min_imb, max_imb, lvl, sp_m, sz_m in self.LEVELS:
            if min_imb <= order_imbalance < max_imb:
                level = lvl
                spread_mult = sp_m
                size_mult = sz_m
                break

        return ToxicityMetrics(
            order_imbalance=order_imbalance,
            level=level,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            sample_count=len(window),
            spread_multiplier=spread_mult,
            size_multiplier=size_mult,
        )

    def is_defensive(self, market_key: str) -> bool:
        """Check if a market is in defensive mode (highly directional or worse)."""
        metrics = self.get_toxicity(market_key)
        return metrics.level in (
            ToxicityLevel.HIGHLY_DIRECTIONAL,
            ToxicityLevel.EXTREME,
        )

    def should_suspend(self, market_key: str) -> bool:
        """Check if quoting should be suspended due to extreme toxicity."""
        metrics = self.get_toxicity(market_key)
        return metrics.level == ToxicityLevel.EXTREME

    def reset(self, market_key: str):
        """Reset toxicity tracking for a market (e.g., on market rotation)."""
        self._trade_windows.pop(market_key, None)
        self._metrics.pop(market_key, None)
