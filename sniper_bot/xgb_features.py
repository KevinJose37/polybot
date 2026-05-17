"""
sniper_bot/xgb_features.py — Real-time feature accumulator for XGBoost inference.

Replicates the offline feature_engineering.py pipeline in real-time.
Samples book state every 5 seconds to match training data cadence.

Features (11 total, must match train_xgboost.py FEATURES list):
    spread, bsi, ofi_zscore, ofi_ewma_1m, ofi_ewma_3m,
    gravity_imbalance, gravity_ewma_1m, ret_1m, ret_3m,
    spread_ewma_1m, tick_rate_1m

Warm-up: 36 samples (3 minutes) before features are reliable.
"""
import time
import logging
from collections import deque
from dataclasses import dataclass, field

from .ws_manager import BookSnapshot, OrderbookManager

logger = logging.getLogger("sniper_bot.xgb_features")

# Match training feature order exactly
FEATURE_NAMES = [
    'spread',
    'bsi',
    'gravity_imbalance',
    'gravity_ewma',
    'ofi_ewma_short',
    'ofi_ewma_long',
    'spread_ewma',
    'tick_rate',
    'depth_ratio',
    'volume_25s',
]

SAMPLE_INTERVAL_S = 5.0  # Must match training data cadence (5-second bars)
MIN_WARM_SAMPLES = 5     # 25s warmup — fits well within 60s entry window
FULL_WARM_SAMPLES = 37   # 36 samples for ret_3m + 1 for OFI = 3 min warm-up


def _ewma_update(prev: float, value: float, alpha: float) -> float:
    """Single-step EWMA update."""
    if prev is None:
        return value
    return alpha * value + (1.0 - alpha) * prev


@dataclass
class _TokenState:
    """Per-token accumulator state."""
    # Raw latest book (updated on every tick)
    last_book: BookSnapshot | None = None
    tick_count: int = 0  # Ticks since last sample

    # Sampled history (5s intervals)
    sample_count: int = 0
    last_sample_time: float = 0.0

    # Previous sample values (for OFI delta)
    prev_bid: float = 0.0
    prev_bid_size: float = 0.0
    prev_ask: float = 0.0
    prev_ask_size: float = 0.0

    # OFI running statistics (for z-score)
    ofi_history: deque = field(default_factory=lambda: deque(maxlen=200))
    ofi_mean: float = 0.0
    ofi_std: float = 1.0

    # Mid-price history (for returns)
    mid_history: deque = field(default_factory=lambda: deque(maxlen=40))

    # EWMA accumulators
    ofi_ewma_short: float | None = None
    ofi_ewma_long: float | None = None
    gravity_ewma: float | None = None
    spread_ewma: float | None = None
    tick_rate_ewma: float | None = None
    volume_25s_accum: float = 0.0

    # Latest computed features
    features: dict = field(default_factory=dict)


# EWMA alphas matching training (span-based)
ALPHA_SHORT = 2.0 / (6 + 1)   # span=6
ALPHA_LONG = 2.0 / (18 + 1)   # span=18


class FeatureAccumulator:
    """
    Accumulates orderbook ticks and produces the 10 features
    expected by the XGBoost model, sampled at 5s intervals.

    Usage:
        acc = FeatureAccumulator(ws_mgr)
        ws_mgr.on_book_update(acc.on_book_tick)  # register callback
        ...
        features = acc.get_features(token_id)  # returns dict or None
    """

    def __init__(self, ws_mgr: OrderbookManager):
        self._ws_mgr = ws_mgr
        self._states: dict[str, _TokenState] = {}

    def on_book_tick(self, token_id: str, book: BookSnapshot) -> None:
        """
        Called on EVERY book tick from ws_manager.
        Updates raw state and triggers sampling every 5s.
        """
        state = self._states.get(token_id)
        if state is None:
            state = _TokenState()
            state.last_sample_time = time.time()
            self._states[token_id] = state

        state.last_book = book
        state.tick_count += 1

        # Sample every 5 seconds
        now = time.time()
        if now - state.last_sample_time >= SAMPLE_INTERVAL_S:
            self._sample(token_id, state, book)
            state.last_sample_time = now

    def get_features(self, token_id: str) -> dict | None:
        """
        Get the latest 10 features for a token.
        Returns None if not warmed up yet.
        """
        state = self._states.get(token_id)
        if not state or state.sample_count < MIN_WARM_SAMPLES:
            return None
        return state.features if state.features else None

    def is_warm(self, token_id: str) -> bool:
        """Check if accumulator has enough samples for reliable features."""
        state = self._states.get(token_id)
        if not state:
            return False
        return state.sample_count >= MIN_WARM_SAMPLES

    def is_fully_warm(self, token_id: str) -> bool:
        """Check if all EWMA/return features are fully stabilized (3 min)."""
        state = self._states.get(token_id)
        if not state:
            return False
        return state.sample_count >= FULL_WARM_SAMPLES

    def warm_progress(self, token_id: str) -> float:
        """Returns warm-up progress 0.0 to 1.0."""
        state = self._states.get(token_id)
        if not state:
            return 0.0
        return min(1.0, state.sample_count / FULL_WARM_SAMPLES)

    def clear(self) -> None:
        """Clear all accumulated state (for market rotation)."""
        self._states.clear()

    # ── Internal sampling ─────────────────────────────────────

    def _sample(self, token_id: str, state: _TokenState, book: BookSnapshot) -> None:
        """Compute one 5-second sample and update EWMA accumulators."""
        state.sample_count += 1

        # ── 1. Base features ──────────────────────────────────
        spread = book.spread
        total_bba_size = book.best_bid_size + book.best_ask_size
        bsi = book.best_bid_size / total_bba_size if total_bba_size > 0 else 0.5

        # ── 2. Gravity imbalance & Depth Ratio (L5 depth) ────────
        bid_l5 = sum(s for _, s in book.bids[:5])
        ask_l5 = sum(s for _, s in book.asks[:5])
        total_l5 = bid_l5 + ask_l5
        gravity_imbalance = (bid_l5 - ask_l5) / total_l5 if total_l5 > 0 else 0.0
        
        depth_ratio = bid_l5 / ask_l5 if ask_l5 > 0 else 1.0
        depth_ratio = max(0.01, min(100.0, depth_ratio))

        # ── 3. OFI (Order Flow Imbalance) ─────────────────────
        ofi_raw = 0.0
        if state.sample_count > 1:
            # Bid side OFI
            if book.best_bid > state.prev_bid:
                bid_ofi = book.best_bid_size
            elif book.best_bid == state.prev_bid:
                bid_ofi = book.best_bid_size - state.prev_bid_size
            else:
                bid_ofi = -state.prev_bid_size

            # Ask side OFI
            if book.best_ask < state.prev_ask:
                ask_ofi = book.best_ask_size
            elif book.best_ask == state.prev_ask:
                ask_ofi = book.best_ask_size - state.prev_ask_size
            else:
                ask_ofi = -state.prev_ask_size

            ofi_raw = bid_ofi - ask_ofi

        # Update previous values for next sample
        state.prev_bid = book.best_bid
        state.prev_bid_size = book.best_bid_size
        state.prev_ask = book.best_ask
        state.prev_ask_size = book.best_ask_size

        # ── 4. OFI Z-Score (running statistics) ───────────────
        state.ofi_history.append(ofi_raw)

        if len(state.ofi_history) >= 3:
            vals = list(state.ofi_history)
            state.ofi_mean = sum(vals) / len(vals)
            variance = sum((v - state.ofi_mean) ** 2 for v in vals) / len(vals)
            state.ofi_std = max(variance ** 0.5, 0.001)  # Floor to prevent div/0

        ofi_zscore = (ofi_raw - state.ofi_mean) / state.ofi_std if state.ofi_std > 0 else 0.0
        ofi_zscore = max(-5.0, min(5.0, ofi_zscore))  # Clip to ±5 std (matches training)

        # ── 5. Mid-price returns & Momentum ───────────────────
        state.mid_history.append(book.mid_price)

        ret_short = 0.0
        if len(state.mid_history) > 6:
            old_mid = state.mid_history[-7]  # 6 samples ago
            if old_mid > 0:
                ret_short = (book.mid_price - old_mid) / old_mid
                # Clip ret_short as per audit
                ret_short = max(-0.5, min(0.5, ret_short))

        # ── 6. Tick rate ──────────────────────────────────────
        tick_rate = float(state.tick_count)
        state.tick_count = 0  # Reset for next sample

        # ── 7. EWMA updates ──────────────────────────────────
        state.ofi_ewma_short = _ewma_update(state.ofi_ewma_short, ofi_zscore, ALPHA_SHORT)
        state.ofi_ewma_long = _ewma_update(state.ofi_ewma_long, ofi_zscore, ALPHA_LONG)
        state.gravity_ewma = _ewma_update(state.gravity_ewma, gravity_imbalance, ALPHA_SHORT)
        state.spread_ewma = _ewma_update(state.spread_ewma, spread, ALPHA_SHORT)
        state.tick_rate_ewma = _ewma_update(state.tick_rate_ewma, tick_rate, ALPHA_SHORT)

        # ── 8. Assemble feature vector ────────────────────────
        state.features = {
            'spread': spread,
            'bsi': bsi,
            'gravity_imbalance': gravity_imbalance,
            'gravity_ewma': state.gravity_ewma if state.gravity_ewma is not None else 0.0,
            'ofi_ewma_short': state.ofi_ewma_short if state.ofi_ewma_short is not None else 0.0,
            'ofi_ewma_long': state.ofi_ewma_long if state.ofi_ewma_long is not None else 0.0,
            'spread_ewma': state.spread_ewma if state.spread_ewma is not None else 0.0,
            'tick_rate': state.tick_rate_ewma if state.tick_rate_ewma is not None else 0.0,
            'depth_ratio': depth_ratio,
            'volume_25s': state.volume_25s_accum,
            'ret_short': ret_short,
        }
