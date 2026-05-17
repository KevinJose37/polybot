"""
sniper_bot/ws_manager.py — Pure-async WebSocket manager for Polymarket CLOB.

Maintains L5 orderbook state per token. Zero threads.
Architecture: recv loop → asyncio.Queue → parse worker → callbacks

All book reads are O(1) dict lookups. No locks needed (single-threaded async).
"""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Any

import websockets

logger = logging.getLogger("sniper_bot.ws")


# ═══════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════

@dataclass
class BookSnapshot:
    """Immutable snapshot of L5 orderbook for one token."""
    token_id: str
    bids: list[tuple[float, float]] = field(default_factory=list)  # [(price, size), ...] descending
    asks: list[tuple[float, float]] = field(default_factory=list)  # [(price, size), ...] ascending
    best_bid: float = 0.0
    best_bid_size: float = 0.0
    best_ask: float = 0.0
    best_ask_size: float = 0.0
    spread: float = 0.0
    mid_price: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    imbalance: float = 0.0       # (bid_depth - ask_depth) / total
    updated: float = 0.0         # time.time()

    @property
    def age_ms(self) -> float:
        return (time.time() - self.updated) * 1000 if self.updated > 0 else 999999


@dataclass
class WSHealth:
    """WebSocket health metrics."""
    connected: bool = False
    uptime_s: float = 0.0
    msgs_received: int = 0
    msgs_parsed: int = 0
    msgs_per_second: float = 0.0
    slow_parses: int = 0
    dropped_msgs: int = 0
    reconnect_count: int = 0
    queue_backlog: int = 0
    avg_parse_ms: float = 0.0
    last_msg_age_ms: float = 0.0
    tokens_tracked: int = 0


# ═══════════════════════════════════════════════════════════════
# OrderbookManager
# ═══════════════════════════════════════════════════════════════

BookCallback = Callable[[str, "BookSnapshot"], Any]


class OrderbookManager:
    """
    Pure-async WebSocket orderbook manager.

    Usage:
        mgr = OrderbookManager(ws_url)
        mgr.on_book_update(my_callback)
        await mgr.connect(["token_id_1", "token_id_2"])
    """

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._subscribed: set[str] = set()
        self._books: dict[str, dict] = {}     # token_id → raw book dict
        self._snapshots: dict[str, BookSnapshot] = {}
        self._callbacks: list[BookCallback] = []
        self._ask_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))

        # Health counters
        self._connected = False
        self._connect_time = 0.0
        self._msgs_received = 0
        self._msgs_parsed = 0
        self._slow_parses = 0
        self._dropped_msgs = 0
        self._reconnect_count = 0
        self._last_msg_time = 0.0
        self._parse_times: deque = deque(maxlen=100)
        self._msg_timestamps: deque = deque(maxlen=60)  # For msgs/sec calc
        self._ws: Any = None
        self._queue: asyncio.Queue | None = None
        self._pending_subscribe: list[str] = []
        self._force_reconnect = False
        self._running = False

    # ── Public API ────────────────────────────────────────────

    def on_book_update(self, callback: BookCallback) -> None:
        """Register a callback fired on every book tick."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def get_book(self, token_id: str) -> BookSnapshot | None:
        """Get the latest book snapshot. O(1) lookup."""
        snap = self._snapshots.get(token_id)
        if snap and snap.age_ms < 60_000:
            return snap
        return None

    def get_ask_velocity(self, token_id: str, window_ms: int = 500) -> float:
        """How much the best_ask moved in the last window_ms."""
        history = self._ask_history.get(token_id)
        if not history or len(history) < 2:
            return 0.0

        now = time.time()
        cutoff = now - (window_ms / 1000.0)
        current_ask = history[-1][1]

        older = [(t, p) for t, p in history if t >= cutoff]
        if older:
            return round(current_ask - older[0][1], 4)
        return 0.0

    def health(self) -> WSHealth:
        """Current health metrics snapshot."""
        now = time.time()
        # Messages per second
        recent = [t for t in self._msg_timestamps if now - t < 5.0]
        mps = len(recent) / 5.0 if recent else 0.0

        avg_parse = 0.0
        if self._parse_times:
            avg_parse = sum(self._parse_times) / len(self._parse_times)

        return WSHealth(
            connected=self._connected,
            uptime_s=now - self._connect_time if self._connected else 0.0,
            msgs_received=self._msgs_received,
            msgs_parsed=self._msgs_parsed,
            msgs_per_second=round(mps, 1),
            slow_parses=self._slow_parses,
            dropped_msgs=self._dropped_msgs,
            reconnect_count=self._reconnect_count,
            queue_backlog=self._queue.qsize() if self._queue else 0,
            avg_parse_ms=round(avg_parse * 1000, 2),
            last_msg_age_ms=round((now - self._last_msg_time) * 1000, 0) if self._last_msg_time > 0 else 99999,
            tokens_tracked=len(self._subscribed),
        )

    async def subscribe(self, token_ids: list[str]) -> None:
        """
        Suscripción dinámica SIN reconectar.
        Solo envía los tokens nuevos, no la lista completa.
        """
        new_set = set(t for t in token_ids if t)
        
        if new_set == self._subscribed:
            return

        # Tokens que se agregan y quitan
        added   = new_set - self._subscribed
        removed = self._subscribed - new_set

        # Limpiar datos de tokens removidos
        for tid in removed:
            self._books.pop(tid, None)
            # CRITICAL: Do NOT pop _snapshots here! 
            # The executor needs the final snapshot to know if we won or lost 
            # after the market resolves and is removed from the websocket.
            # self._snapshots.pop(tid, None)
            self._ask_history.pop(tid, None)

        self._subscribed = new_set

        # Polymarket DOES NOT send initial book snapshots for dynamic subscriptions on an open connection.
        # It only sends price_change deltas. If we only get deltas, our local book is empty and corrupted.
        # We MUST reconnect to get the full snapshots.
        if self._ws and self._connected:
            self._force_reconnect = True
            logger.info("Tokens changed. Forcing reconnect to fetch fresh snapshots...")

    async def pre_subscribe(self, token_ids: list[str], seconds_before: float = 30.0) -> None:
        """
        Suscribirse ANTES de que abra el mercado.
        Cuando el mercado abre, el book ya está caliente.
        """
        await asyncio.sleep(max(0, seconds_before))
        await self.subscribe(token_ids)
        logger.info("Pre-suscripcion completada %ds antes de apertura", seconds_before)

    # ── Connection loop ───────────────────────────────────────

    async def run(self, initial_tokens: list[str] | None = None) -> None:
        """Main connection loop. Runs forever with auto-reconnect."""
        self._running = True
        if initial_tokens:
            self._subscribed.update(t for t in initial_tokens if t)

        retry_delay = 1.0
        import random

        while self._running:
            self._queue = asyncio.Queue(maxsize=5000)
            parse_task = None

            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=10,
                    ping_timeout=10,
                    close_timeout=0.1,  # INSTANT CLOSE, no waiting for Polymarket ack
                    max_size=2**22,  # 4 MB max message
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self._connect_time = time.time()
                    retry_delay = 1.0
                    logger.info("WS connected to %s", self._ws_url)

                    # Start parse worker
                    parse_task = asyncio.create_task(self._parse_worker())

                    # Subscribe
                    if self._subscribed:
                        await self._send_subscribe(list(self._subscribed))
                        logger.info("WS subscribed to %d tokens", len(self._subscribed))

                    # Health timeout
                    health_timeout = 90
                    last_msg = time.time()

                    while self._running:
                        # Health check
                        if time.time() - last_msg > health_timeout:
                            logger.warning("WS health FAIL — no msgs for %ds, reconnecting", health_timeout)
                            break
                            
                        # Force reconnect request
                        if self._force_reconnect:
                            self._force_reconnect = False
                            break

                        # Pending subscribes
                        if self._pending_subscribe:
                            pending = list(self._pending_subscribe)
                            self._pending_subscribe.clear()
                            await self._send_subscribe(pending)

                        # Receive
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        self._msgs_received += 1
                        self._last_msg_time = time.time()
                        self._msg_timestamps.append(self._last_msg_time)
                        last_msg = self._last_msg_time

                        # Non-blocking queue put
                        if self._queue.full():
                            try:
                                self._queue.get_nowait()
                                self._dropped_msgs += 1
                            except asyncio.QueueEmpty:
                                pass
                        await self._queue.put(raw)

            except Exception as exc:
                self._connected = False
                self._ws = None
                self._reconnect_count += 1
                if self._running:
                    jitter = random.uniform(0, retry_delay * 0.5)
                    wait = retry_delay + jitter
                    logger.warning("WS disconnected (#%d): %s — reconnecting in %.1fs",
                                   self._reconnect_count, exc, wait)
                    await asyncio.sleep(wait)
                    retry_delay = min(retry_delay * 2, 30)
            finally:
                self._connected = False
                self._ws = None
                if parse_task and not parse_task.done():
                    await self._queue.put(None)  # Poison pill
                    try:
                        await asyncio.wait_for(parse_task, timeout=2.0)
                    except asyncio.TimeoutError:
                        parse_task.cancel()

    def stop(self):
        self._running = False

    # ── Internal ──────────────────────────────────────────────

    async def _send_subscribe(self, tokens: list[str]) -> None:
        """Envía suscripción solo para los tokens especificados."""
        if not self._ws or not tokens:
            return
            
        msg = json.dumps({
            "assets_ids": tokens,        # Solo los nuevos, no todos
            "type": "market",
            "custom_feature_enabled": True,
        })
        
        try:
            await self._ws.send(msg)
            logger.debug("WS subscribe sent: %d tokens", len(tokens))
        except Exception as e:
            logger.error("WS subscribe failed: %s", e)
            self._pending_subscribe.extend(tokens)

    async def _parse_worker(self) -> None:
        """Pulls raw JSON from queue and processes."""
        while True:
            raw = await self._queue.get()
            if raw is None:
                break

            t0 = time.perf_counter()
            try:
                data = json.loads(raw)
                
                # Polymarket sometimes sends messages as a list of dicts (e.g. initial book snapshots)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            self._process_single_msg(item)
                elif isinstance(data, dict):
                    self._process_single_msg(data)
                
                self._msgs_parsed += 1

            except json.JSONDecodeError:
                pass

            dt = time.perf_counter() - t0
            self._parse_times.append(dt)
            if dt > 0.01:
                self._slow_parses += 1

            self._queue.task_done()

    def _process_single_msg(self, data: dict) -> None:
        """Process a single message dictionary."""
        event_type = data.get("event_type", "")
        if event_type == "book":
            self._handle_book(data)
        elif event_type == "price_change":
            self._handle_price_change(data)
        elif event_type == "best_bid_ask":
            self._handle_best_bid_ask(data)

    def _handle_book(self, data: dict) -> None:
        """Full book snapshot."""
        asset_id = data.get("asset_id", data.get("market", ""))
        if asset_id not in self._subscribed:
            return

        bids = []
        for b in data.get("bids", []):
            p, s = float(b.get("price", 0)), float(b.get("size", 0))
            if s > 0:
                bids.append((p, s))
        asks = []
        for a in data.get("asks", []):
            p, s = float(a.get("price", 0)), float(a.get("size", 0))
            if s > 0:
                asks.append((p, s))

        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        self._books[asset_id] = {"bids": bids, "asks": asks}
        self._update_snapshot(asset_id)

    def _handle_price_change(self, data: dict) -> None:
        """Incremental book update."""
        changes = data.get("price_changes", data.get("changes", []))
        for change in changes:
            asset_id = str(change.get("asset_id", ""))
            if asset_id not in self._subscribed:
                continue

            price = float(change.get("price", 0))
            size = float(change.get("size", 0))
            side = str(change.get("side", "")).lower()

            if asset_id not in self._books:
                self._books[asset_id] = {"bids": [], "asks": []}

            book = self._books[asset_id]

            if side == "buy":
                book["bids"] = [(p, s) for p, s in book["bids"] if abs(p - price) > 0.0001]
                if size > 0:
                    book["bids"].append((price, size))
                book["bids"].sort(key=lambda x: x[0], reverse=True)
            elif side == "sell":
                book["asks"] = [(p, s) for p, s in book["asks"] if abs(p - price) > 0.0001]
                if size > 0:
                    book["asks"].append((price, size))
                book["asks"].sort(key=lambda x: x[0])

            # Use server-provided best bid/ask if available
            msg_bb = float(change.get("best_bid", 0) or 0)
            msg_ba = float(change.get("best_ask", 0) or 0)

            self._update_snapshot(asset_id, server_bid=msg_bb, server_ask=msg_ba)

    def _handle_best_bid_ask(self, data: dict) -> None:
        """Handle 'best_bid_ask' event — direct bid/ask update without book delta."""
        asset_id = str(data.get("asset_id", ""))
        if asset_id not in self._subscribed:
            return

        bb = float(data.get("best_bid", 0) or 0)
        ba = float(data.get("best_ask", 0) or 0)

        if bb > 0 or ba > 0:
            self._update_snapshot(asset_id, server_bid=bb, server_ask=ba)

    def _update_snapshot(self, token_id: str,
                         server_bid: float = 0, server_ask: float = 0) -> None:
        """Rebuild BookSnapshot from raw book and fire callbacks."""
        book = self._books.get(token_id)
        if not book:
            return

        bids = book.get("bids", [])
        asks = book.get("asks", [])

        bb = server_bid if server_bid > 0 else (bids[0][0] if bids else 0.0)
        bb_sz = bids[0][1] if bids else 0.0
        ba = server_ask if server_ask > 0 else (asks[0][0] if asks else 0.0)
        ba_sz = asks[0][1] if asks else 0.0

        spread = round(ba - bb, 4) if (bb > 0 and ba > 0) else 0.0
        mid = round((bb + ba) / 2, 6) if (bb > 0 and ba > 0) else 0.0

        bid_depth = sum(s for _, s in bids[:10])
        ask_depth = sum(s for _, s in asks[:10])
        total = bid_depth + ask_depth
        imbalance = round((bid_depth - ask_depth) / total, 4) if total > 0 else 0.0

        now = time.time()
        snap = BookSnapshot(
            token_id=token_id,
            bids=bids[:10],
            asks=asks[:10],
            best_bid=bb, best_bid_size=bb_sz,
            best_ask=ba, best_ask_size=ba_sz,
            spread=spread, mid_price=mid,
            bid_depth=round(bid_depth, 2),
            ask_depth=round(ask_depth, 2),
            imbalance=imbalance,
            updated=now,
        )
        self._snapshots[token_id] = snap

        # Track ask history for velocity
        if ba > 0:
            self._ask_history[token_id].append((now, ba))

        # Fire callbacks
        for cb in self._callbacks:
            try:
                cb(token_id, snap)
            except Exception as e:
                logger.error("Book callback error: %s", e)
