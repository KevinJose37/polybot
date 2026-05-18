"""Binance Spot WebSocket adapter."""

import asyncio
import json
from collections.abc import Awaitable, Callable

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
        self._callback: Callable[[str, float], Awaitable[None]] | None = None

        # Internal state: asset_symbol (e.g. 'ETH') -> mid_price
        self._mids: dict[str, float] = {}

    def subscribe(self, assets: list[str]) -> None:
        """Add assets (e.g. 'ETH', 'BTC') to subscriptions. Must be called before connect."""
        self._assets.update(assets)

    def set_callback(self, callback: Callable[[str, float], Awaitable[None]]) -> None:
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
                                asset = symbol.replace("USDT", "")
                                mid = (float(bid) + float(ask)) / 2.0
                                self._mids[asset] = mid

                                if self._callback:
                                    await self._callback(asset, mid)

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
