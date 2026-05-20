"""Polymarket WebSocket adapter."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
import websockets

from .base import OrderBook, OrderBookAdapter, TradeEvent

logger = structlog.get_logger(__name__)


class SequenceGapError(Exception):
    """Raised when a sequence gap is detected to force an immediate reconnect."""
    def __init__(self, market_id: str, expected: int, received: int):
        self.market_id = market_id
        self.expected = expected
        self.received = received
        super().__init__(f"Sequence gap on {market_id}: expected {expected}, got {received}")


class PolymarketWSAdapter(OrderBookAdapter):
    """
    WebSocket client that maintains L2 orderbooks for Polymarket.
    Multiplexes multiple asset subscriptions over one connection.
    """

    def __init__(self, wss_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"):
        self.wss_url = wss_url
        self._ws = None
        self._running = False
        self._subs: set[str] = set()
        self._callback: Callable[[OrderBook], Awaitable[None]] | None = None
        self._trade_callback: Callable[[TradeEvent], Awaitable[None]] | None = None
        self._reconnect_callback: Callable[[str], Awaitable[None]] | None = None

        # Internal state: market_id -> (bids_dict, asks_dict)
        # dicts are price(float) -> size(float)
        self._books: dict[str, tuple[dict[float, float], dict[float, float]]] = {}
        self._sequences: dict[str, int] = {}
        
        # [H-2] Track background tasks to prevent exception swallowing and memory leaks
        self._bg_tasks: set[asyncio.Task] = set()

    def _dispatch_task(self, coro: Awaitable[Any], name: str) -> None:
        """[H-2] Safely dispatch a background task and track it."""
        task = asyncio.create_task(coro, name=name)
        self._bg_tasks.add(task)
        
        def _on_done(t: asyncio.Task) -> None:
            self._bg_tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.error("ws_bg_task_failed", task=t.get_name(), error=str(t.exception()))
                
        task.add_done_callback(_on_done)

    def subscribe(self, market_ids: list[str]) -> None:
        """Add market IDs (token IDs) to subscriptions."""
        self._subs.update(market_ids)
        for mid in market_ids:
            if mid not in self._books:
                self._books[mid] = ({}, {})

        if self._ws and self._running:
            # [M-4] Guard subscribe task
            self._dispatch_task(self._send_subscriptions(), "send_subscriptions")

    def set_callback(self, callback: Callable[[OrderBook], Awaitable[None]]) -> None:
        self._callback = callback

    def set_trade_callback(self, callback: Callable[[TradeEvent], Awaitable[None]]) -> None:
        self._trade_callback = callback
        
    def set_reconnect_callback(self, callback: Callable[[str], Awaitable[None]]) -> None:
        self._reconnect_callback = callback

    async def _send_subscriptions(self) -> None:
        if self._ws and self._subs:
            msg = {"assets_ids": list(self._subs), "type": "market"}
            await self._ws.send(json.dumps(msg))
            logger.info("polymarket_ws_subscribed", count=len(self._subs))

    def _process_message(self, data: dict) -> list[str]:
        """Process incoming L2 message and update internal orderbook state.
        Returns a list of updated market IDs.
        """
        updated_ids = []

        # 1. Snapshot element
        if "asset_id" in data and ("bids" in data or "asks" in data):
            market_id = data["asset_id"]
            if market_id in self._books:
                self._update_sequence(market_id, data)
                bids_dict, asks_dict = self._books[market_id]
                for b in data.get("bids", []):
                    self._apply_level(bids_dict, b)
                for a in data.get("asks", []):
                    self._apply_level(asks_dict, a)
                updated_ids.append(market_id)

        # 2. Delta update
        elif "price_changes" in data:
            for pc in data["price_changes"]:
                market_id = pc.get("asset_id")
                if not market_id or market_id not in self._books:
                    continue
                self._update_sequence(market_id, pc)
                bids_dict, asks_dict = self._books[market_id]
                side = pc.get("side", "").upper()
                if side == "BUY":
                    self._apply_level(bids_dict, pc)
                elif side == "SELL":
                    self._apply_level(asks_dict, pc)
                if market_id not in updated_ids:
                    updated_ids.append(market_id)

        return updated_ids

    def _update_sequence(self, market_id: str, item: dict) -> None:
        seq = item.get("hash")
        if "sequence" in item:
            seq = item["sequence"]
            
        if seq is not None and isinstance(seq, int):
            expected = self._sequences.get(market_id)
            if expected is not None and seq > expected + 1:
                self._books[market_id] = ({}, {})
                raise SequenceGapError(market_id, expected + 1, seq)
            self._sequences[market_id] = seq

    def _apply_level(self, levels: dict[float, float], item: dict) -> None:
        try:
            price = float(item["price"])
            size = float(item["size"])
            if size == 0:
                levels.pop(price, None)
            else:
                levels[price] = size
        except (KeyError, ValueError):
            pass

    def _get_orderbook(self, market_id: str) -> OrderBook:
        bids_dict, asks_dict = self._books[market_id]

        # Sort bids descending
        bids = sorted([(p, s) for p, s in bids_dict.items()], key=lambda x: x[0], reverse=True)
        # Sort asks ascending
        asks = sorted([(p, s) for p, s in asks_dict.items()], key=lambda x: x[0])

        return OrderBook(market_id=market_id, bids=bids, asks=asks)

    async def connect_and_run(self) -> None:
        self._running = True
        backoff = 1.0

        while self._running:
            logger.info("polymarket_ws_connecting", url=self.wss_url)
            try:
                async with websockets.connect(self.wss_url) as ws:
                    self._ws = ws
                    logger.info("polymarket_ws_connected")
                    backoff = 1.0
                    
                    # Clear state on reconnect so the new snapshot populates a clean book
                    for mid in self._books:
                        self._books[mid] = ({}, {})
                        self._sequences.pop(mid, None)
                        if self._reconnect_callback:
                            self._dispatch_task(self._reconnect_callback(mid), f"pm_reconnect_{mid}")
                    
                    await self._send_subscriptions()

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            # Some messages might be a list of events if batched
                            events = data if isinstance(data, list) else [data]

                            for event in events:
                                updated_ids = self._process_message(event)

                                if self._callback:
                                    for market_id in updated_ids:
                                        book = self._get_orderbook(market_id)
                                        self._dispatch_task(self._callback(book), "pm_book_cb")

                        except json.JSONDecodeError:
                            pass
                        except SequenceGapError as gap:
                            # F-02: Sequence gap immediately breaks all processing
                            # and forces a clean reconnect. Book is already poisoned.
                            logger.warning(
                                "sequence_gap_forced_reconnect",
                                market=gap.market_id,
                                expected=gap.expected,
                                received=gap.received,
                            )
                            break
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error("polymarket_ws_error", error=str(e))
            finally:
                self._ws = None

            if self._running:
                logger.info("polymarket_ws_reconnecting", next_reconnect_s=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def close(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            
        # Cancel all background tasks
        for task in list(self._bg_tasks):
            if not task.done():
                task.cancel()
        
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()
