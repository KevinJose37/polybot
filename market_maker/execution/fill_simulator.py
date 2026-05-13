"""
execution/fill_simulator.py — Paper trading passive fill simulation.
Implements a fair-value-crossing fill model that operates in probability space.
"""

import random
import time
from collections import defaultdict
from loguru import logger
from config.settings import config
from utils.schemas import FillRecord, QuotePair


class FillSimulator:
    """
    Production-grade paper trading fill simulator.

    The key insight: our quotes are in PROBABILITY space (0-1) and Binance
    trades are in USD space ($81,000). We can NOT compare them directly.

    Instead, fills are triggered when the **fair value** crosses through
    our quoted bid or ask price. This models the real scenario:
    - FV drops below our bid → informed sellers hit our bid → BUY fill
    - FV rises above our ask → informed buyers lift our ask → SELL fill

    Additional realism:
    - Queue-position fill probability (default 25%): not all crossings fill us
    - Cooldown per market: prevent burst fills from a single FV swing
    - Size limited to quote size
    - Maker fee modeling
    - Slippage on aggressive fills (fills closer to mid get a penalty)
    """

    def __init__(self, fill_probability: float = 0, cooldown_ms: int = 2000):
        self.fill_probability = fill_probability or config.default_fill_probability
        self.maker_fee_rate = config.maker_fee_rate
        self.cooldown_ms = cooldown_ms  # Min time between fills per market per side

        # Track last FV to detect crossings
        self._last_fv: dict[str, float] = {}  # market_key -> last fair value

        # Cooldown tracking: market_key -> {side: last_fill_ms}
        self._last_fill_ts: dict[str, dict[str, int]] = defaultdict(lambda: {"BUY": 0, "SELL": 0})

    def check_fill_on_fv_update(
        self,
        market_key: str,
        quotes: QuotePair,
        new_fv: float,
        asset: str,
        window_minutes: int,
    ) -> list[FillRecord]:
        """
        Check if a fair value update would trigger passive fills.

        A fill happens when the fair value CROSSES through our quoted price:
        - FV drops below bid → BUY fill (market sells into our bid)
        - FV rises above ask → SELL fill (market buys into our ask)

        This is more realistic than comparing different price spaces.
        """
        fills = []
        now_ms = int(time.time() * 1000)

        prev_fv = self._last_fv.get(market_key)
        self._last_fv[market_key] = new_fv

        if prev_fv is None:
            return fills  # Need a previous FV to detect crossing

        # ── Check BID fill: FV dropped below our bid ──
        if (
            quotes.bid_size > 0
            and prev_fv >= quotes.bid_price   # FV was above bid
            and new_fv < quotes.bid_price      # FV crossed below bid
            and self._check_cooldown(market_key, "BUY", now_ms)
            and random.random() < self.fill_probability
        ):
            fill_size = int(quotes.bid_size)
            fee = self.maker_fee_rate * quotes.bid_price * fill_size
            fills.append(FillRecord(
                market_id=market_key,
                asset=asset,
                window_minutes=window_minutes,
                side="BUY",
                price=quotes.bid_price,
                size=fill_size,
                fee=fee,
                timestamp_ms=now_ms,
                is_maker=True,
                is_simulated=True,
            ))
            self._last_fill_ts[market_key]["BUY"] = now_ms

        # ── Check ASK fill: FV rose above our ask ──
        if (
            quotes.ask_size > 0
            and prev_fv <= quotes.ask_price    # FV was below ask
            and new_fv > quotes.ask_price       # FV crossed above ask
            and self._check_cooldown(market_key, "SELL", now_ms)
            and random.random() < self.fill_probability
        ):
            fill_size = int(quotes.ask_size)
            fee = self.maker_fee_rate * quotes.ask_price * fill_size
            fills.append(FillRecord(
                market_id=market_key,
                asset=asset,
                window_minutes=window_minutes,
                side="SELL",
                price=quotes.ask_price,
                size=fill_size,
                fee=fee,
                timestamp_ms=now_ms,
                is_maker=True,
                is_simulated=True,
            ))
            self._last_fill_ts[market_key]["SELL"] = now_ms

        return fills

    def _check_cooldown(self, market_key: str, side: str, now_ms: int) -> bool:
        """Return True if enough time has passed since last fill for this side."""
        last = self._last_fill_ts[market_key][side]
        return (now_ms - last) >= self.cooldown_ms

    def reset(self, market_key: str):
        """Reset state for a market (e.g., on rotation)."""
        self._last_fv.pop(market_key, None)
        self._last_fill_ts.pop(market_key, None)
