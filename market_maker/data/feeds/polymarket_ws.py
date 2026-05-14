"""
data/feeds/polymarket_ws.py — Polymarket order book feed.
Primary: WebSocket connection for real-time L2 book updates via price_change events.
Fallback: REST polling for initial snapshot and reconnection recovery.
"""

import asyncio
import json
import time
from typing import Optional, Callable, Awaitable

import aiohttp
import websockets
from loguru import logger

from config.settings import config
from utils.schemas import MarketOdds


class PolymarketFeed:
    """
    Polymarket L2 book feed with WebSocket primary and REST fallback.

    WebSocket events handled:
    - 'book': Full orderbook snapshot (received on subscribe and periodically)
    - 'price_change': Incremental updates (order placed/cancelled)

    The feed maintains a local copy of the L2 book and applies incremental
    updates from price_change events to keep it current.
    """

    CLOB_URL = "https://clob.polymarket.com"

    def __init__(self, token_id_yes: str, token_id_no: str, market_id: str):
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.market_id = market_id

        # REST session for fallback
        self._session: Optional[aiohttp.ClientSession] = None

        # Current book state (thread-safe via asyncio single-threaded loop)
        self._bids: list[dict] = []  # [{"price": "0.5", "size": "100"}, ...]
        self._asks: list[dict] = []
        self._last_odds: Optional[MarketOdds] = None
        self._last_update_ms: int = 0

        # REST cache
        self._cache_ttl = 1.5
        self._last_fetch_ts = 0.0

        # WebSocket state
        self._ws_connected = False
        self._ws_running = False
        self._ws_task: Optional[asyncio.Task] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._update_count = 0

        # Callback for book updates (set by main.py)
        self.on_book_update: Optional[Callable[[str, MarketOdds], Awaitable[None]]] = None

    # ═══════════════════════════════════════════════════════════
    # WebSocket — Primary Feed
    # ═══════════════════════════════════════════════════════════

    async def start_ws(self):
        """Start the WebSocket connection in the background."""
        if self._ws_running:
            return
        self._ws_running = True
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def _ws_loop(self):
        """Main WebSocket loop with reconnection and exponential backoff."""
        reconnect_delay = 1.0

        while self._ws_running:
            try:
                async with websockets.connect(
                    config.poly_ws_url,
                    ping_interval=config.poly_ws_ping_interval_s,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._ws_connected = True
                    reconnect_delay = 1.0

                    # Subscribe to this token
                    subscribe_msg = {
                        "assets_ids": [self.token_id_yes],
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(
                        f"[PolyWS] Connected & subscribed: {self.market_id} "
                        f"(token={self.token_id_yes[:12]}...)"
                    )

                    async for message in ws:
                        if not self._ws_running:
                            break
                        await self._process_ws_message(message)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(
                    f"[PolyWS] Disconnected ({self.market_id}): {e.code} {e.reason}"
                )
            except Exception as e:
                logger.error(f"[PolyWS] Error ({self.market_id}): {e}")

            self._ws_connected = False
            self._ws = None

            if self._ws_running:
                logger.info(
                    f"[PolyWS] Reconnecting {self.market_id} in {reconnect_delay:.0f}s..."
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)

    async def _process_ws_message(self, raw_message: str):
        """Process a single WebSocket message."""
        try:
            data = json.loads(raw_message)

            # Polymarket WS can send arrays (e.g., batch updates) — process each item
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        await self._dispatch_event(item)
                return

            if isinstance(data, dict):
                await self._dispatch_event(data)

        except (json.JSONDecodeError, KeyError, ValueError, AttributeError, TypeError) as e:
            logger.debug(f"[PolyWS] Parse error ({self.market_id}): {e}")

    async def _dispatch_event(self, data: dict):
        """Route a single event dict to the appropriate handler."""
        event_type = data.get("event_type", "")

        if event_type == "book":
            await self._handle_book_snapshot(data)
        elif event_type == "price_change":
            await self._handle_price_change(data)
        # Ignore other event types (best_bid_ask, new_market, market_resolved, etc.)

    async def _handle_book_snapshot(self, data: dict):
        """Handle full book snapshot from WebSocket."""
        asset_id = data.get("asset_id", "")
        
        if asset_id == self.token_id_yes:
            bids = data.get("bids", [])
            self._bids = self._normalize_levels(bids, descending=True)
        elif asset_id == self.token_id_no:
            no_bids = data.get("bids", [])
            inverted_asks = []
            for b in no_bids:
                if isinstance(b, dict):
                    p = 1.0 - float(b.get("price", "0"))
                    inverted_asks.append({"price": str(round(p, 4)), "size": b.get("size", "0")})
                elif isinstance(b, (list, tuple)) and len(b) >= 2:
                    p = 1.0 - float(b[0])
                    inverted_asks.append({"price": str(round(p, 4)), "size": str(b[1])})
            self._asks = self._normalize_levels(inverted_asks, descending=False)
        else:
            return

        self._rebuild_odds()
        self._update_count += 1

        logger.debug(
            f"[PolyWS] Book snapshot {self.market_id}: "
            f"{len(self._bids)} bids, {len(self._asks)} asks"
        )

    async def _handle_price_change(self, data: dict):
        """Handle incremental price_change event."""
        changes = data.get("price_changes", [])

        for change in changes:
            if not isinstance(change, dict):
                continue

            asset_id = change.get("asset_id", "")
            price = change.get("price", "0")
            size = change.get("size", "0")
            side = change.get("side", "").upper()

            if asset_id == self.token_id_yes:
                if side == "BUY":
                    self._apply_level_update(self._bids, price, size, descending=True)
            elif asset_id == self.token_id_no:
                if side == "BUY":
                    inverted_price = str(round(1.0 - float(price), 4))
                    self._apply_level_update(self._asks, inverted_price, size, descending=False)

        self._rebuild_odds()
        self._update_count += 1

    def _apply_level_update(
        self, levels: list[dict], price: str, size: str, descending: bool
    ):
        """Apply a single level update to the local book copy.
        If size is "0", remove the level. Otherwise insert/update.
        """
        price_f = float(price)
        size_f = float(size)

        # Find existing level
        for i, level in enumerate(levels):
            if abs(float(level["price"]) - price_f) < 1e-6:
                if size_f <= 0:
                    levels.pop(i)
                else:
                    level["size"] = size
                return

        # Level not found — insert if size > 0
        if size_f > 0:
            levels.append({"price": price, "size": size})
            # Re-sort
            levels.sort(
                key=lambda x: float(x["price"]),
                reverse=descending,
            )

    def _normalize_levels(self, levels: list, descending: bool) -> list[dict]:
        """Normalize levels to list of {"price": str, "size": str} dicts, sorted."""
        result = []
        for level in levels:
            if isinstance(level, dict):
                result.append({
                    "price": str(level.get("price", "0")),
                    "size": str(level.get("size", "0")),
                })
            elif isinstance(level, (list, tuple)) and len(level) >= 2:
                result.append({"price": str(level[0]), "size": str(level[1])})

        result.sort(key=lambda x: float(x["price"]), reverse=descending)
        return result

    def _rebuild_odds(self):
        """Rebuild MarketOdds from current local book state."""
        now_ms = int(time.time() * 1000)

        best_bid = float(self._bids[0]["price"]) if self._bids else 0.0
        best_ask = float(self._asks[0]["price"]) if self._asks else 1.0
        yes_price = (best_bid + best_ask) / 2 if best_bid > 0 else best_ask

        self._last_odds = MarketOdds(
            market_id=self.market_id,
            token_id_yes=self.token_id_yes,
            token_id_no=self.token_id_no,
            yes_price=yes_price,
            bid_yes=best_bid,
            ask_yes=best_ask,
            timestamp_ms=now_ms,
            book_depth_bid=len(self._bids),
            book_depth_ask=len(self._asks),
            bids=list(self._bids),  # Copy to prevent mutation
            asks=list(self._asks),
        )
        self._last_update_ms = now_ms

    # ═══════════════════════════════════════════════════════════
    # REST — Fallback Feed
    # ═══════════════════════════════════════════════════════════

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_market_odds(self) -> Optional[MarketOdds]:
        """
        Get current market odds.
        Returns WS-maintained book if connected, otherwise falls back to REST.
        """
        # If WebSocket is connected and we have data, use it directly
        if self._ws_connected and self._last_odds and self._update_count > 0:
            return self._last_odds

        # Fallback: REST poll with cache
        now = time.time()
        if self._last_odds and (now - self._last_fetch_ts) < self._cache_ttl:
            return self._last_odds

        try:
            session = await self._get_session()
            url = f"{self.CLOB_URL}/book"

            async with session.get(
                url,
                params={"token_id": self.token_id_yes},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return self._last_odds
                data = await resp.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            # Also update local book state so WS can build on it
            self._bids = self._normalize_levels(bids, descending=True)
            self._asks = self._normalize_levels(asks, descending=False)

            self._rebuild_odds()
            self._last_fetch_ts = now
            return self._last_odds

        except Exception as e:
            logger.debug(f"[Poly] Error fetching odds for {self.market_id}: {e}")
            return self._last_odds

    def get_book_spread(self) -> Optional[float]:
        """Get the current book spread (ask - bid) for YES token."""
        if self._last_odds:
            return self._last_odds.ask_yes - self._last_odds.bid_yes
        return None

    @property
    def is_ws_connected(self) -> bool:
        """Whether the WebSocket is currently connected."""
        return self._ws_connected

    @property
    def book_age_ms(self) -> int:
        """Age of the last book update in milliseconds."""
        if self._last_update_ms == 0:
            return 999999
        return int(time.time() * 1000) - self._last_update_ms

    def stop(self):
        """Stop the WebSocket connection."""
        self._ws_running = False
        if self._ws_task:
            self._ws_task.cancel()
        logger.debug(f"[PolyWS] Stopping: {self.market_id}")

    async def close(self):
        """Close all connections."""
        self.stop()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
