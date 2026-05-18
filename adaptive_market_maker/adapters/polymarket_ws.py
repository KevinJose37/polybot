"""Polymarket WebSocket adapter."""

import asyncio
import json
from collections.abc import Awaitable, Callable

import structlog
import websockets

from .base import OrderBook, OrderBookAdapter

logger = structlog.get_logger(__name__)


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

        # Internal state: market_id -> (bids_dict, asks_dict)
        # dicts are price(float) -> size(float)
        self._books: dict[str, tuple[dict[float, float], dict[float, float]]] = {}

    def subscribe(self, market_ids: list[str]) -> None:
        """Add market IDs (token IDs) to subscriptions."""
        self._subs.update(market_ids)
        for mid in market_ids:
            if mid not in self._books:
                self._books[mid] = ({}, {})

        if self._ws and self._running:
            asyncio.create_task(self._send_subscriptions())

    def set_callback(self, callback: Callable[[OrderBook], Awaitable[None]]) -> None:
        self._callback = callback

    async def _send_subscriptions(self) -> None:
        if self._ws and self._subs:
            msg = {"assets_ids": list(self._subs), "type": "market"}
            await self._ws.send(json.dumps(msg))
            logger.info("polymarket_ws_subscribed", count=len(self._subs))

    def _process_message(self, data: dict) -> None:
        """Process incoming L2 message and update internal orderbook state."""
        market_id = data.get("asset_id")
        if not market_id or market_id not in self._books:
            return

        bids_dict, asks_dict = self._books[market_id]

        # Polymarket WS sends lists of dicts: {"price": "0.5", "size": "100"}
        # If size is 0, the level is removed.
        for b in data.get("bids", []):
            try:
                price = float(b["price"])
                size = float(b["size"])
                if size == 0:
                    bids_dict.pop(price, None)
                else:
                    bids_dict[price] = size
            except (KeyError, ValueError):
                continue

        for a in data.get("asks", []):
            try:
                price = float(a["price"])
                size = float(a["size"])
                if size == 0:
                    asks_dict.pop(price, None)
                else:
                    asks_dict[price] = size
            except (KeyError, ValueError):
                continue

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
                    
                    await self._send_subscriptions()

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            # Some messages might be a list of events if batched
                            events = data if isinstance(data, list) else [data]

                            for event in events:
                                self._process_message(event)
                                if self._callback and event.get("asset_id") in self._books:
                                    book = self._get_orderbook(event["asset_id"])
                                    await self._callback(book)

                        except json.JSONDecodeError:
                            pass
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
