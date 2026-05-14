"""
execution/fill_simulator.py — Paper trading passive fill simulation.
Implements a latency-aware, queue-tracking execution model based on orderbook depth.

Realism improvements (v2):
- Cancel-vs-fill discrimination: L1 size decreases are filtered by cancel_rate
- Partial fills on adverse sweeps: proportional to our share of resting liquidity
- Taker fee on crossing fills: uses taker_fee_rate, not maker_fee_rate
- Liquidity check on taker crossing fills
- Inter-tick quote cooldown after fills (simulates cancel-replace latency)
- Order rejection simulation
"""

import random
import time
from collections import defaultdict
from loguru import logger
from config.settings import config
from utils.schemas import FillRecord, QuotePair, PendingQuote, MarketOdds


class FillSimulator:
    """
    Realistic Fill Simulator using Latency Delay and L2 Queue position.
    Instead of assuming instant fills on FV-crossings, this simulator:
    1. Delays quotes by `sim_latency_ms`.
    2. Enforces queue priority by checking Polymarket L2 depth ahead of the order.
    3. Triggers adverse selection fills with partial fill logic.
    4. Filters L1 size drops by cancel_rate to avoid phantom fills.
    5. Enforces a quote cooldown after fills to simulate cancel-replace latency.
    6. Applies order rejection probability.
    """

    def __init__(self, latency_ms: int = None, drain_rate: float = None):
        self.latency_ms = latency_ms if latency_ms is not None else config.sim_latency_ms
        self.drain_rate = drain_rate if drain_rate is not None else config.sim_queue_drain_rate
        self.maker_fee_rate = config.maker_fee_rate
        self.taker_fee_rate = config.taker_fee_rate
        self.cancel_rate = config.sim_cancel_rate
        self.order_rejection_rate = config.sim_order_rejection_rate
        self.quote_cooldown_ms = config.sim_quote_cooldown_ms
        self.partial_fill_share = config.sim_partial_fill_share
        self.gas_cost = config.gas_cost_per_tx

        # Track pending and live quotes per market
        self._pending_quotes: dict[str, list[PendingQuote]] = defaultdict(list)
        self._live_quotes: dict[str, QuotePair] = {}

        # Track queue position per market per side
        self._queue_pos: dict[str, dict[str, float]] = defaultdict(lambda: {"BUY": 0.0, "SELL": 0.0})
        self._last_book_size: dict[str, dict[str, float]] = defaultdict(lambda: {"BUY": 0.0, "SELL": 0.0})
        self._last_best_prices: dict[str, dict[str, float]] = defaultdict(lambda: {"bid": 0.0, "ask": 1.0})
        self._last_update_ms: dict[str, int] = defaultdict(int)

        # Quote cooldown: market_key -> earliest ms when new quotes can be submitted
        self._quote_cooldown_until: dict[str, int] = defaultdict(int)

    def _get_size_ahead(self, levels: list[dict], price: float, is_bid: bool) -> float:
        """Calculate total size ahead of us in the orderbook."""
        size_ahead = 0.0
        for level in levels:
            p = float(level.get("price", 0))
            s = float(level.get("size", 0))
            if is_bid and p > price:
                size_ahead += s
            elif not is_bid and p < price:
                size_ahead += s
            elif p == price:
                # We join at the back of the queue at this price
                size_ahead += s
        return size_ahead

    def _get_size_at_price(self, levels: list[dict], price: float) -> float:
        """Get total size resting at a specific price level."""
        for level in levels:
            p = float(level.get("price", 0))
            if abs(p - price) < 1e-6:
                return float(level.get("size", 0))
        return 0.0

    def _get_available_liquidity(self, levels: list[dict], price: float, is_bid: bool) -> float:
        """Get available liquidity at or better than a price for taker fills."""
        total = 0.0
        for level in levels:
            p = float(level.get("price", 0))
            s = float(level.get("size", 0))
            if is_bid and p <= price:
                # Buying: we can lift asks at or below our bid
                total += s
            elif not is_bid and p >= price:
                # Selling: we can hit bids at or above our ask
                total += s
        return total

    def submit_quotes(self, market_key: str, new_quotes: QuotePair, l2_book: MarketOdds | None):
        """Submit new quotes to the latency buffer. Applies order rejection and cooldown."""
        now_ms = int(time.time() * 1000)

        # Enforce quote cooldown after fills
        if now_ms < self._quote_cooldown_until.get(market_key, 0):
            logger.debug(
                f"[FillSim] {market_key} quote blocked: cooldown until "
                f"{self._quote_cooldown_until[market_key]} (now={now_ms})"
            )
            return

        # Simulate order rejection (nonce errors, rate limits)
        if random.random() < self.order_rejection_rate:
            logger.debug(f"[FillSim] {market_key} quote rejected (simulated nonce/rate-limit)")
            return

        arrival_ms = now_ms + self.latency_ms

        # Calculate initial queue position
        q_buy = 0.0
        q_sell = 0.0
        if l2_book:
            q_buy = self._get_size_ahead(l2_book.bids, new_quotes.bid_price, is_bid=True)
            q_sell = self._get_size_ahead(l2_book.asks, new_quotes.ask_price, is_bid=False)

        pending = PendingQuote(
            quotes=new_quotes,
            arrival_ms=arrival_ms,
            q_buy=q_buy,
            q_sell=q_sell
        )
        self._pending_quotes[market_key].append(pending)

    def update_state(self, market_key: str, now_ms: int, fv: float, l2_book: MarketOdds | None, asset: str, window_minutes: int) -> list[FillRecord]:
        """Promote pending quotes and evaluate fills based on queue position and book movement."""
        fills = []

        # 1. Promote pending quotes
        pending_list = self._pending_quotes[market_key]
        for p in pending_list:
            if now_ms >= p.arrival_ms:
                self._live_quotes[market_key] = p.quotes
                self._queue_pos[market_key]["BUY"] = p.q_buy
                self._queue_pos[market_key]["SELL"] = p.q_sell

        # Remove promoted quotes
        self._pending_quotes[market_key] = [p for p in pending_list if now_ms < p.arrival_ms]

        live = self._live_quotes.get(market_key)
        if not live or not l2_book:
            self._last_update_ms[market_key] = now_ms
            return fills

        dt_sec = (now_ms - self._last_update_ms[market_key]) / 1000.0
        # Guard against huge time jumps or first run
        if dt_sec > 60 or dt_sec <= 0:
            dt_sec = 0.5

        self._last_update_ms[market_key] = now_ms

        current_bid_price = l2_book.bid_yes
        current_ask_price = l2_book.ask_yes
        last_bid_price = self._last_best_prices[market_key]["bid"]
        last_ask_price = self._last_best_prices[market_key]["ask"]

        # 2. Taker Fills (We crossed the spread) — with liquidity check and taker fees
        if live.bid_size > 0 and live.bid_price >= current_ask_price and current_ask_price > 0:
            available_liq = self._get_available_liquidity(l2_book.asks, live.bid_price, is_bid=True)
            fill_size = min(int(live.bid_size), max(1, int(available_liq)))
            if fill_size > 0:
                # Taker fill: use taker_fee_rate
                fee = self.taker_fee_rate * live.bid_price * fill_size + self.gas_cost
                fills.append(FillRecord(
                    market_id=market_key, asset=asset, window_minutes=window_minutes,
                    side="BUY", price=live.bid_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                    is_maker=False,
                ))
                live.bid_size -= fill_size

        if live.ask_size > 0 and live.ask_price <= current_bid_price and current_bid_price > 0:
            available_liq = self._get_available_liquidity(l2_book.bids, live.ask_price, is_bid=False)
            fill_size = min(int(live.ask_size), max(1, int(available_liq)))
            if fill_size > 0:
                # Taker fill: use taker_fee_rate
                fee = self.taker_fee_rate * live.ask_price * fill_size + self.gas_cost
                fills.append(FillRecord(
                    market_id=market_key, asset=asset, window_minutes=window_minutes,
                    side="SELL", price=live.ask_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                    is_maker=False,
                ))
                live.ask_size -= fill_size

        # 3. Adverse Sweeps (Market traded through us) — with partial fill logic
        # If the exchange best bid drops, takers sold. If our bid was >= new exchange bid, we got swept.
        if live.bid_size > 0 and last_bid_price > 0 and current_bid_price < last_bid_price:
            if live.bid_price >= current_bid_price:
                # Estimate taker volume from price drop using L2 depth consumed
                taker_volume = self._estimate_sweep_volume(
                    l2_book.bids, last_bid_price, current_bid_price, is_bid=True
                )
                # Our fill is proportional to our share of resting liquidity
                resting_at_price = self._get_size_at_price(l2_book.bids, live.bid_price)
                our_share = self.partial_fill_share  # Conservative estimate
                if resting_at_price > 0:
                    our_share = min(self.partial_fill_share, live.bid_size / (resting_at_price + live.bid_size))
                fill_size = min(int(live.bid_size), max(1, int(taker_volume * our_share)))
                if fill_size > 0:
                    fee = self.maker_fee_rate * live.bid_price * fill_size + self.gas_cost
                    fills.append(FillRecord(
                        market_id=market_key, asset=asset, window_minutes=window_minutes,
                        side="BUY", price=live.bid_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                    ))
                    live.bid_size -= fill_size

        # If the exchange best ask rises, takers bought. If our ask was <= new exchange ask, we got swept.
        if live.ask_size > 0 and last_ask_price < 1.0 and current_ask_price > last_ask_price:
            if live.ask_price <= current_ask_price:
                taker_volume = self._estimate_sweep_volume(
                    l2_book.asks, last_ask_price, current_ask_price, is_bid=False
                )
                resting_at_price = self._get_size_at_price(l2_book.asks, live.ask_price)
                our_share = self.partial_fill_share
                if resting_at_price > 0:
                    our_share = min(self.partial_fill_share, live.ask_size / (resting_at_price + live.ask_size))
                fill_size = min(int(live.ask_size), max(1, int(taker_volume * our_share)))
                if fill_size > 0:
                    fee = self.maker_fee_rate * live.ask_price * fill_size + self.gas_cost
                    fills.append(FillRecord(
                        market_id=market_key, asset=asset, window_minutes=window_minutes,
                        side="SELL", price=live.ask_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                    ))
                    live.ask_size -= fill_size

        # 4. Queue Drain (Volume Flow Proxy at L1) — with cancel-vs-fill discrimination
        current_bid_size = float(l2_book.bids[0].get("size", 0)) if (l2_book.bids and float(l2_book.bids[0].get("price", 0)) == current_bid_price) else 0.0
        current_ask_size = float(l2_book.asks[0].get("size", 0)) if (l2_book.asks and float(l2_book.asks[0].get("price", 0)) == current_ask_price) else 0.0

        last_bid_size = self._last_book_size[market_key]["BUY"]
        last_ask_size = self._last_book_size[market_key]["SELL"]

        # Bid side queue drain
        if live.bid_size > 0 and current_bid_price == live.bid_price and current_bid_price == last_bid_price:
            if current_bid_size < last_bid_size:
                observed_delta = last_bid_size - current_bid_size
                # Apply cancel filter: only (1 - cancel_rate) fraction are real fills
                estimated_trades = observed_delta * (1.0 - self.cancel_rate)
                self._queue_pos[market_key]["BUY"] -= estimated_trades

            if self._queue_pos[market_key]["BUY"] <= 0:
                if current_bid_size < last_bid_size:
                    observed_delta = last_bid_size - current_bid_size
                    estimated_trades = observed_delta * (1.0 - self.cancel_rate)
                    fill_size = min(int(live.bid_size), max(1, int(estimated_trades)))
                    if fill_size > 0:
                        fee = self.maker_fee_rate * live.bid_price * fill_size + self.gas_cost
                        fills.append(FillRecord(
                            market_id=market_key, asset=asset, window_minutes=window_minutes,
                            side="BUY", price=live.bid_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                        ))
                        live.bid_size -= fill_size
                        self._queue_pos[market_key]["BUY"] = 0

        # Ask side queue drain
        if live.ask_size > 0 and current_ask_price == live.ask_price and current_ask_price == last_ask_price:
            if current_ask_size < last_ask_size:
                observed_delta = last_ask_size - current_ask_size
                estimated_trades = observed_delta * (1.0 - self.cancel_rate)
                self._queue_pos[market_key]["SELL"] -= estimated_trades

            if self._queue_pos[market_key]["SELL"] <= 0:
                if current_ask_size < last_ask_size:
                    observed_delta = last_ask_size - current_ask_size
                    estimated_trades = observed_delta * (1.0 - self.cancel_rate)
                    fill_size = min(int(live.ask_size), max(1, int(estimated_trades)))
                    if fill_size > 0:
                        fee = self.maker_fee_rate * live.ask_price * fill_size + self.gas_cost
                        fills.append(FillRecord(
                            market_id=market_key, asset=asset, window_minutes=window_minutes,
                            side="SELL", price=live.ask_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                        ))
                        live.ask_size -= fill_size
                        self._queue_pos[market_key]["SELL"] = 0

        self._last_book_size[market_key]["BUY"] = current_bid_size
        self._last_book_size[market_key]["SELL"] = current_ask_size
        self._last_best_prices[market_key]["bid"] = current_bid_price
        self._last_best_prices[market_key]["ask"] = current_ask_price

        # Set quote cooldown if fills occurred
        if fills:
            self._quote_cooldown_until[market_key] = now_ms + self.quote_cooldown_ms

        return fills

    def _estimate_sweep_volume(
        self, levels: list[dict], old_price: float, new_price: float, is_bid: bool
    ) -> float:
        """
        Estimate how many contracts were consumed in a sweep
        by summing resting liquidity at levels between old and new best price.
        Falls back to a conservative estimate if book is empty.
        """
        total = 0.0
        for level in levels:
            p = float(level.get("price", 0))
            s = float(level.get("size", 0))
            if is_bid:
                # Bid dropped from old_price to new_price: volume consumed at levels in between
                if new_price <= p <= old_price:
                    total += s
            else:
                # Ask rose from old_price to new_price: volume consumed at levels in between
                if old_price <= p <= new_price:
                    total += s

        # If no levels found, use conservative estimate based on price move
        if total == 0:
            price_move = abs(new_price - old_price)
            total = max(5.0, price_move * 500)  # Conservative: 500 contracts per cent

        return total

    def reset(self, market_key: str):
        """Reset state for a market."""
        self._pending_quotes.pop(market_key, None)
        self._live_quotes.pop(market_key, None)
        self._queue_pos.pop(market_key, None)
        self._last_update_ms.pop(market_key, None)
        self._last_book_size.pop(market_key, None)
        self._last_best_prices.pop(market_key, None)
        self._quote_cooldown_until.pop(market_key, None)
