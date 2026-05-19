"""Binance Spot WebSocket adapter."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
import websockets

from .base import SpotReferenceAdapter

logger = structlog.get_logger(__name__)


class BinanceWSAdapter(SpotReferenceAdapter):
    """
    WebSocket client that maintains spot mid-prices for underlying assets via Binance.
    """

    def __init__(self):
        self._ws = None
        self._running = False
        self._assets: set[str] = set()
        self._callback: Callable[[str, float, float], Awaitable[None]] | None = None

        # Internal state: asset_symbol (e.g. 'ETH') -> mid_price
        self._mids: dict[str, float] = {}

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

    def subscribe(self, assets: list[str]) -> None:
        """Add assets (e.g. 'ETH', 'BTC') to subscriptions. Must be called before connect."""
        self._assets.update(assets)

    def set_callback(self, callback: Callable[[str, float, float], Awaitable[None]]) -> None:
        self._callback = callback

    def _get_stream_url(self) -> str:
        """Construct the combined stream URL."""
        if not self._assets:
            return "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"

        streams = [f"{a.lower()}usdt@bookTicker" for a in self._assets]
        if len(streams) == 1:
            return f"wss://stream.binance.com:9443/ws/{streams[0]}"
        else:
            return f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

    async def connect_and_run(self) -> None:
        if not self._assets:
            logger.warning("binance_ws_no_assets_configured")
            return

        self._running = True
        backoff = 1.0
        url = self._get_stream_url()

        while self._running:
            logger.info("binance_ws_connecting", url=url)
            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    logger.info("binance_ws_connected")
                    backoff = 1.0

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            # If using multiplexed stream, payload is under 'data'
                            payload = data.get("data", data)

                            # Binance bookTicker format:
                            # {
                            #   "s": "ETHUSDT",
                            #   "b": "3000.00", # best bid price
                            #   "B": "10.0",    # best bid qty
                            #   "a": "3000.10", # best ask price
                            #   "A": "10.0"     # best ask qty
                            # }
                            symbol = payload.get("s")
                            bid = payload.get("b")
                            ask = payload.get("a")

                            if symbol and bid and ask:
                                # F-17: Capture receive timestamp as close to
                                # message receipt as possible for staleness tracking
                                recv_time = time.time()
                                asset = symbol.replace("USDT", "")
                                mid = (float(bid) + float(ask)) / 2.0
                                self._mids[asset] = mid

                                if self._callback:
                                    # [H-2] Decouple from WS read loop
                                    self._dispatch_task(self._callback(asset, mid, recv_time), "binance_cb")

                        except json.JSONDecodeError:
                            pass
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error("binance_ws_error", error=str(e))
            finally:
                self._ws = None

            if self._running:
                logger.info("binance_ws_reconnecting", next_reconnect_s=backoff)
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
