"""
Reconnecting WebSocket client.
"""
import asyncio
import json
import structlog
import websockets
from websockets.client import WebSocketClientProtocol
from typing import Callable, Awaitable

from bot.utils.clocks import current_timestamp_ms

logger = structlog.get_logger(__name__)


class StaleFeedError(Exception):
    """Raised when the WebSocket feed has not received a message within the timeout."""
    pass


class PolymarketWSClient:
    """
    WebSocket client that reconnects with exponential backoff.
    """
    def __init__(self, wss_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"):
        self.wss_url = wss_url
        self._ws: WebSocketClientProtocol | None = None
        self._running = False
        self._subs: set[str] = set()
        self._callback: Callable[[dict], Awaitable[None]] | None = None
        
        self.reconnect_count = 0
        self.last_message_ts = 0

    def subscribe(self, token_ids: list[str]) -> None:
        """Add token IDs to subscriptions."""
        self._subs.update(token_ids)
        if self._ws and self._running:
            task = asyncio.create_task(self._send_subscriptions())
            task.add_done_callback(
                lambda t: logger.error("ws_subscribe_task_failed", error=str(t.exception())) 
                if not t.cancelled() and t.exception() else None
            )

    def set_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set the async callback for incoming messages."""
        self._callback = callback

    async def _send_subscriptions(self) -> None:
        if self._ws and self._subs:
            msg = {
                "assets_ids": list(self._subs),
                "type": "market"
            }
            await self._ws.send(json.dumps(msg))
            logger.info("ws_subscribed", count=len(self._subs))

    async def connect_and_run(self) -> None:
        """Run the WebSocket loop with exponential backoff reconnects."""
        self._running = True
        backoff = 1.0

        while self._running:
            logger.info("ws_connecting", url=self.wss_url)
            try:
                async with websockets.connect(self.wss_url) as ws:
                    self._ws = ws
                    logger.info("ws_connected")
                    backoff = 1.0  # reset backoff on successful connect
                    self.last_message_ts = current_timestamp_ms()
                    
                    await self._send_subscriptions()
                    
                    async for message in ws:
                        self.last_message_ts = current_timestamp_ms()
                        if self._callback:
                            try:
                                data = json.loads(message)
                                await self._callback(data)
                            except json.JSONDecodeError:
                                pass
            except asyncio.CancelledError:
                self._running = False
                logger.info("ws_cancelled")
                break
            except Exception as e:
                logger.error("ws_error", error=str(e))
            finally:
                self._ws = None

            if self._running:
                self.reconnect_count += 1
                logger.info("ws_disconnecting", next_reconnect_s=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def check_stale(self, silence_window_ms: int = 30000) -> None:
        """Check if the feed is stale and raise StaleFeedError if so."""
        if not self._running:
            return
            
        now = current_timestamp_ms()
        if self.last_message_ts > 0 and (now - self.last_message_ts) > silence_window_ms:
            logger.error("ws_stale_feed", silence_ms=now - self.last_message_ts)
            raise StaleFeedError("WebSocket feed is stale")

    async def close(self) -> None:
        """Stop the loop and close the connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            logger.info("ws_closed")
