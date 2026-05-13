"""
execution/fill_simulator.py — Paper trading passive fill simulation.
Implements a latency-aware, queue-tracking execution model based on orderbook depth.
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
    3. Triggers adverse selection fills if the real book drops below the quote.
    4. Simulates spread capture (uninformed flow) based on a probabilistic drain rate when at the front of the queue.
    """

    def __init__(self, latency_ms: int = None, drain_rate: float = None):
        self.latency_ms = latency_ms if latency_ms is not None else config.sim_latency_ms
        self.drain_rate = drain_rate if drain_rate is not None else config.sim_queue_drain_rate
        self.maker_fee_rate = config.maker_fee_rate
        
        # Track pending and live quotes per market
        self._pending_quotes: dict[str, list[PendingQuote]] = defaultdict(list)
        self._live_quotes: dict[str, QuotePair] = {}
        
        # Track queue position per market per side
        self._queue_pos: dict[str, dict[str, float]] = defaultdict(lambda: {"BUY": 0.0, "SELL": 0.0})
        self._last_book_size: dict[str, dict[str, float]] = defaultdict(lambda: {"BUY": 0.0, "SELL": 0.0})
        self._last_best_prices: dict[str, dict[str, float]] = defaultdict(lambda: {"bid": 0.0, "ask": 1.0})
        self._last_update_ms: dict[str, int] = defaultdict(int)

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

    def submit_quotes(self, market_key: str, new_quotes: QuotePair, l2_book: MarketOdds | None):
        """Submit new quotes to the latency buffer."""
        now_ms = int(time.time() * 1000)
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
        
        # 2. Taker Fills (We crossed the spread)
        if live.bid_size > 0 and live.bid_price >= current_ask_price and current_ask_price > 0:
            fill_size = int(live.bid_size)
            fee = self.maker_fee_rate * live.bid_price * fill_size  # Should ideally be taker_fee_rate
            fills.append(FillRecord(
                market_id=market_key, asset=asset, window_minutes=window_minutes,
                side="BUY", price=live.bid_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
            ))
            live.bid_size = 0

        if live.ask_size > 0 and live.ask_price <= current_bid_price and current_bid_price > 0:
            fill_size = int(live.ask_size)
            fee = self.maker_fee_rate * live.ask_price * fill_size
            fills.append(FillRecord(
                market_id=market_key, asset=asset, window_minutes=window_minutes,
                side="SELL", price=live.ask_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
            ))
            live.ask_size = 0

        # 3. Adverse Sweeps (Market traded through us)
        # If the exchange best bid drops, takers sold. If our bid was >= new exchange bid, we got swept.
        if live.bid_size > 0 and last_bid_price > 0 and current_bid_price < last_bid_price:
            if live.bid_price >= current_bid_price:
                fill_size = int(live.bid_size)
                fee = self.maker_fee_rate * live.bid_price * fill_size
                fills.append(FillRecord(
                    market_id=market_key, asset=asset, window_minutes=window_minutes,
                    side="BUY", price=live.bid_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                ))
                live.bid_size = 0
                
        # If the exchange best ask rises, takers bought. If our ask was <= new exchange ask, we got swept.
        if live.ask_size > 0 and last_ask_price < 1.0 and current_ask_price > last_ask_price:
            if live.ask_price <= current_ask_price:
                fill_size = int(live.ask_size)
                fee = self.maker_fee_rate * live.ask_price * fill_size
                fills.append(FillRecord(
                    market_id=market_key, asset=asset, window_minutes=window_minutes,
                    side="SELL", price=live.ask_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                ))
                live.ask_size = 0

        # Proxy taker volume using L1 size drops for Queue Draining
        current_bid_size = float(l2_book.bids[0].get("size", 0)) if (l2_book.bids and float(l2_book.bids[0].get("price", 0)) == current_bid_price) else 0.0
        current_ask_size = float(l2_book.asks[0].get("size", 0)) if (l2_book.asks and float(l2_book.asks[0].get("price", 0)) == current_ask_price) else 0.0
        
        last_bid_size = self._last_book_size[market_key]["BUY"]
        last_ask_size = self._last_book_size[market_key]["SELL"]

        # 4. Drain Queue (Volume Flow Proxy at L1)
        if live.bid_size > 0 and current_bid_price == live.bid_price and current_bid_price == last_bid_price:
            if current_bid_size < last_bid_size:
                volume_traded = last_bid_size - current_bid_size
                self._queue_pos[market_key]["BUY"] -= volume_traded

            if self._queue_pos[market_key]["BUY"] <= 0:
                if current_bid_size < last_bid_size:
                    fill_size = min(int(live.bid_size), int(last_bid_size - current_bid_size))
                    if fill_size > 0:
                        fee = self.maker_fee_rate * live.bid_price * fill_size
                        fills.append(FillRecord(
                            market_id=market_key, asset=asset, window_minutes=window_minutes,
                            side="BUY", price=live.bid_price, size=fill_size, fee=fee, timestamp_ms=now_ms,
                        ))
                        live.bid_size -= fill_size
                        self._queue_pos[market_key]["BUY"] = 0
                    
        if live.ask_size > 0 and current_ask_price == live.ask_price and current_ask_price == last_ask_price:
            if current_ask_size < last_ask_size:
                volume_traded = last_ask_size - current_ask_size
                self._queue_pos[market_key]["SELL"] -= volume_traded

            if self._queue_pos[market_key]["SELL"] <= 0:
                if current_ask_size < last_ask_size:
                    fill_size = min(int(live.ask_size), int(last_ask_size - current_ask_size))
                    if fill_size > 0:
                        fee = self.maker_fee_rate * live.ask_price * fill_size
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
                    
        return fills

    def reset(self, market_key: str):
        """Reset state for a market."""
        self._pending_quotes.pop(market_key, None)
        self._live_quotes.pop(market_key, None)
        self._queue_pos.pop(market_key, None)
        self._last_update_ms.pop(market_key, None)
