"""High-Fidelity Paper Trading Client for Polymarket."""

import asyncio
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from adapters.base import TradeEvent, OrderBook
from config.settings import LatencyConfig
from core.interfaces import PolymarketClientProtocol

logger = structlog.get_logger(__name__)


@dataclass
class PaperLiveOrder:
    id: str
    market_id: str
    side: str
    price: float
    size: float
    remaining_size: float
    queue_ahead: float
    created_at: float


class ForensicLogger:
    def __init__(self, log_dir: str = "logs"):
        Path(log_dir).mkdir(exist_ok=True)
        self.file = open(Path(log_dir) / "paper_forensics.jsonl", "a")

    def log_event(self, event_type: str, data: dict):
        record = {
            "timestamp": time.time(),
            "event": event_type,
            **data
        }
        self.file.write(json.dumps(record) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


def sample_latency(mean: float, std: float, p_fat: float, fat_mult: float) -> float:
    if random.random() < p_fat:
        return max(0.0, random.gauss(mean * fat_mult, std)) / 1000.0
    return max(0.0, random.gauss(mean, std)) / 1000.0


class PaperPolymarketClient(PolymarketClientProtocol):
    def __init__(self, latency_config: LatencyConfig):
        self.latency_config = latency_config
        self.synthetic_inventory: dict[str, float] = {}
        self.live_orders: dict[str, PaperLiveOrder] = {}
        self.latest_books: dict[str, OrderBook] = {}
        self.forensic = ForensicLogger()
        self._order_counter = 0

    def update_book(self, book: OrderBook):
        self.latest_books[book.market_id] = book

    async def fetch_inventory(self, market_id: str) -> float:
        await asyncio.sleep(sample_latency(
            self.latency_config.market_data_mean_ms,
            self.latency_config.market_data_std_ms,
            self.latency_config.p_fat_tail,
            self.latency_config.fat_tail_mult
        ))
        return self.synthetic_inventory.get(market_id, 0.0)

    def get_inventory(self, market_id: str) -> float:
        return self.synthetic_inventory.get(market_id, 0.0)

    async def place_order(self, market_id: str, side: str, price: float, size: float) -> str:
        latency = sample_latency(
            self.latency_config.place_mean_ms,
            self.latency_config.place_std_ms,
            self.latency_config.p_fat_tail,
            self.latency_config.fat_tail_mult
        )
        await asyncio.sleep(latency)
        
        self._order_counter += 1
        order_id = f"paper_{self._order_counter}"
        
        # Estimate queue ahead
        queue_ahead = 0.0
        book = self.latest_books.get(market_id)
        if book:
            queue_ahead = book.depth_at(price, side)

        self.live_orders[order_id] = PaperLiveOrder(
            id=order_id,
            market_id=market_id,
            side=side,
            price=price,
            size=size,
            remaining_size=size,
            queue_ahead=queue_ahead,
            created_at=time.time()
        )
        
        self.forensic.log_event("order_placed", {
            "order_id": order_id,
            "market_id": market_id,
            "side": side,
            "price": price,
            "size": size,
            "queue_ahead": queue_ahead,
            "latency_ms": latency * 1000.0
        })
        
        return order_id

    async def cancel_order(self, order_id: str, market_id: str) -> bool:
        latency = sample_latency(
            self.latency_config.cancel_mean_ms,
            self.latency_config.cancel_std_ms,
            self.latency_config.p_fat_tail,
            self.latency_config.fat_tail_mult
        )
        await asyncio.sleep(latency)
        
        if order_id in self.live_orders:
            del self.live_orders[order_id]
            self.forensic.log_event("order_cancelled", {
                "order_id": order_id,
                "latency_ms": latency * 1000.0
            })
            return True
        return False

    def process_fill(self, order: PaperLiveOrder, fill_size: float, trade_ts: float):
        order.remaining_size -= fill_size
        if order.side == "BID":
            self.synthetic_inventory[order.market_id] = self.synthetic_inventory.get(order.market_id, 0.0) + fill_size
        else:
            self.synthetic_inventory[order.market_id] = self.synthetic_inventory.get(order.market_id, 0.0) - fill_size
            
        self.forensic.log_event("order_filled", {
            "order_id": order.id,
            "market_id": order.market_id,
            "side": order.side,
            "fill_price": order.price,
            "fill_size": fill_size,
            "remaining_size": order.remaining_size,
            "trade_timestamp": trade_ts
        })
        
        if order.remaining_size <= 1e-6:
            if order.id in self.live_orders:
                del self.live_orders[order.id]

    async def on_trade(self, trade: TradeEvent):
        # We need a list so we can iterate and modify
        active_orders = [o for o in self.live_orders.values() if o.market_id == trade.market_id]
        
        for order in active_orders:
            if order.side == "BID":
                if trade.price < order.price:
                    # Trade crossed through our level - immediate fill
                    self.process_fill(order, order.remaining_size, trade.timestamp)
                elif trade.price == order.price:
                    if order.queue_ahead > 0:
                        consumed = min(order.queue_ahead, trade.size)
                        order.queue_ahead -= consumed
                        remaining_trade = trade.size - consumed
                    else:
                        remaining_trade = trade.size
                        
                    if remaining_trade > 0:
                        filled = min(order.remaining_size, remaining_trade)
                        self.process_fill(order, filled, trade.timestamp)
                        
            elif order.side == "ASK":
                if trade.price > order.price:
                    # Trade crossed through our level - immediate fill
                    self.process_fill(order, order.remaining_size, trade.timestamp)
                elif trade.price == order.price:
                    if order.queue_ahead > 0:
                        consumed = min(order.queue_ahead, trade.size)
                        order.queue_ahead -= consumed
                        remaining_trade = trade.size - consumed
                    else:
                        remaining_trade = trade.size
                        
                    if remaining_trade > 0:
                        filled = min(order.remaining_size, remaining_trade)
                        self.process_fill(order, filled, trade.timestamp)
