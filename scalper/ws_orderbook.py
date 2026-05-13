import asyncio
import json
import logging
import threading
import time
from typing import Dict, List, Optional
import websockets

logger = logging.getLogger("polybot.scalper.ws_orderbook")

class WebSocketL2Daemon:
    def __init__(self):
        self.uri = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.books: Dict[str, Dict] = {}  # token_id -> {"bids": {price: size}, "asks": {price: size}}
        self.subscribed: set = set()
        self.loop = asyncio.new_event_loop()
        self.ws_queue = asyncio.Queue()
        
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self._connected = False

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._maintain_connection())

    async def _maintain_connection(self):
        while True:
            try:
                async with websockets.connect(self.uri, ping_interval=20, ping_timeout=20) as ws:
                    self._connected = True
                    logger.info("[WS-L2] Connected to Polymarket WebSocket")
                    
                    # Resubscribe to existing tokens if reconnected
                    if self.subscribed:
                        await self._send_subscribe(ws, list(self.subscribed))

                    # Process incoming messages and outgoing requests concurrently
                    receiver_task = asyncio.create_task(self._receive_messages(ws))
                    sender_task = asyncio.create_task(self._process_queue(ws))
                    
                    done, pending = await asyncio.wait(
                        [receiver_task, sender_task], 
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    for task in pending:
                        task.cancel()
                        
            except Exception as e:
                logger.error(f"[WS-L2] Connection error: {e}")
            finally:
                self._connected = False
                logger.info("[WS-L2] Disconnected. Reconnecting in 3 seconds...")
                await asyncio.sleep(3)

    async def _receive_messages(self, ws):
        async for msg in ws:
            try:
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8")
                if not msg or msg.strip() == "" or msg == "OK" or msg == "INVALID OPERATION":
                    continue
                
                # Check if it starts with { or [ before parsing to avoid JSON decode errors on random strings
                if not msg.startswith('{') and not msg.startswith('['):
                    continue
                    
                data = json.loads(msg)
                # Polymarket WS can send lists of events or single objects
                if isinstance(data, list):
                    for event in data:
                        self._handle_event(event)
                elif isinstance(data, dict):
                    self._handle_event(data)
            except Exception as e:
                logger.error(f"[WS-L2] Error parsing message: {e} | Raw: {msg[:100]}")

    async def _process_queue(self, ws):
        while True:
            token_ids = await self.ws_queue.get()
            if not token_ids:
                continue
            await self._send_subscribe(ws, token_ids)

    async def _send_subscribe(self, ws, token_ids: List[str]):
        req = {
            "assets_ids": token_ids,
            "type": "market"
        }
        await ws.send(json.dumps(req))
        logger.debug(f"[WS-L2] Sent subscription for {len(token_ids)} tokens")

    def _handle_event(self, event: dict):
        event_type = event.get("event_type")
        asset_id = event.get("asset_id")
        
        if not asset_id or not event_type:
            return

        if asset_id not in self.books:
            self.books[asset_id] = {"bids": {}, "asks": {}}

        book = self.books[asset_id]

        if event_type == "book":
            # Initial snapshot
            book["bids"] = {float(b["price"]): float(b["size"]) for b in event.get("bids", [])}
            book["asks"] = {float(a["price"]): float(a["size"]) for a in event.get("asks", [])}
        elif event_type in ("price_change", "order_added", "order_deleted", "order_matched"):
            # Incremental updates
            self._update_levels(book["bids"], event.get("bids", []))
            self._update_levels(book["asks"], event.get("asks", []))

    def _update_levels(self, book_side: dict, updates: list):
        for update in updates:
            px = float(update.get("price", 0))
            sz = float(update.get("size", 0))
            if sz <= 0:
                book_side.pop(px, None)
            else:
                book_side[px] = sz

    def subscribe(self, token_id: str):
        """Request WS subscription for a token. Does not block."""
        if token_id not in self.subscribed:
            self.subscribed.add(token_id)
            self.loop.call_soon_threadsafe(self.ws_queue.put_nowait, [token_id])

    def get_book(self, token_id: str) -> Optional[dict]:
        """
        Returns the local L2 book formatted exactly like the REST API response.
        Returns None if the book is not yet initialized or has no data.
        """
        if token_id not in self.books:
            return None
            
        book = self.books[token_id]
        
        if not book["bids"] and not book["asks"]:
            return None
            
        return {
            "bids": [{"price": str(px), "size": str(sz)} for px, sz in book["bids"].items()],
            "asks": [{"price": str(px), "size": str(sz)} for px, sz in book["asks"].items()]
        }

# Global singleton instance
_daemon = None

def get_ws_daemon() -> WebSocketL2Daemon:
    global _daemon
    if _daemon is None:
        _daemon = WebSocketL2Daemon()
    return _daemon

def get_live_book(token_id: str) -> Optional[dict]:
    """
    Get the real-time L2 orderbook via WebSocket.
    Automatically subscribes if not currently tracked.
    """
    daemon = get_ws_daemon()
    
    # Always ensure we are subscribed
    daemon.subscribe(token_id)
    
    # Return the current snapshot from RAM
    return daemon.get_book(token_id)
