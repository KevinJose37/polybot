"""
scalper/orderbook_ws.py — WebSocket market data monitor.

Maintains real-time buffers for:
  1. Market prices (up/down outcome prices) — used by scanner
  2. Orderbook depth (bids/asks) — used for pre-sell liquidity checks

Connection: wss://ws-subscriptions-clob.polymarket.com/ws/market
No authentication required for public market data.

Architecture:
  - Background thread runs async WS event loop
  - Thread-safe dict buffers updated on every WS message
  - Scanner/trader read from buffers (no REST calls needed)
  - Loop cycle remains synchronous (10s decisions)
"""

import asyncio
import json
import logging
import threading
import time

import websockets

logger = logging.getLogger("polybot.scalper.orderbook_ws")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# ═══════════════════════════════════════════════════════════════
# State Buffers (thread-safe via _lock)
# ═══════════════════════════════════════════════════════════════

# token_id → {"bids": [(price, size), ...], "asks": [(price, size), ...], "updated": ts}
_orderbooks: dict[str, dict] = {}

# token_id → {"price": float, "updated": ts}
_prices: dict[str, dict] = {}

_subscribed_tokens: set[str] = set()
_lock = threading.Lock()
_ws_thread: threading.Thread | None = None
_ws_running = False
_ws_loop: asyncio.AbstractEventLoop | None = None
_ws_connection = None  # Reference for hot-subscribing


# ═══════════════════════════════════════════════════════════════
# Public Read API (called from scanner/trader, thread-safe)
# ═══════════════════════════════════════════════════════════════


def get_price(token_id: str) -> float | None:
    """Get the latest price for a token from WS buffer."""
    with _lock:
        entry = _prices.get(token_id)
        if entry and (time.time() - entry.get("updated", 0)) < 60:
            return entry["price"]
        return None


def get_prices_for_market(up_token_id: str, down_token_id: str) -> dict | None:
    """
    Get both UP and DOWN prices for a market from WS buffer.
    Returns dict with up_price, down_price, or None if no data.
    """
    up = get_price(up_token_id)
    down = get_price(down_token_id)
    if up is not None or down is not None:
        return {
            "up_price": up if up is not None else (1.0 - down if down is not None else 0.5),
            "down_price": down if down is not None else (1.0 - up if up is not None else 0.5),
            "source": "ws",
            "updated": time.time(),
        }
    return None


def get_orderbook(token_id: str) -> dict | None:
    """Get the current orderbook snapshot for a token."""
    with _lock:
        book = _orderbooks.get(token_id)
        if book and (time.time() - book.get("updated", 0)) < 60:
            return book
        return None


def get_best_bid(token_id: str) -> tuple[float, float] | None:
    """Get the best bid (highest buy price). Returns (price, size) or None."""
    with _lock:
        book = _orderbooks.get(token_id)
        if not book:
            return None
        bids = book.get("bids", [])
        if not bids:
            return None
        return bids[0]


def get_total_bid_depth(token_id: str) -> float:
    """Get total bid depth (sum of all bid sizes)."""
    with _lock:
        book = _orderbooks.get(token_id)
        if not book:
            return 0.0
        return sum(size for _price, size in book.get("bids", []))


def check_sell_liquidity(
    token_id: str,
    shares: float,
    entry_price: float,
    max_slippage: float = 0.15,
) -> dict:
    """
    Check if there's enough liquidity to sell.

    Returns dict with:
        can_sell, reason, best_bid, bid_depth, slippage_pct
    """
    best = get_best_bid(token_id)
    depth = get_total_bid_depth(token_id)

    if best is None:
        return {
            "can_sell": False,
            "reason": "No orderbook data (WS not connected or no bids)",
            "best_bid": 0,
            "bid_depth": 0,
            "slippage_pct": 1.0,
        }

    bid_price, bid_size = best

    if bid_price <= 0.01:
        return {
            "can_sell": False,
            "reason": f"Best bid is dust (${bid_price:.2f})",
            "best_bid": bid_price,
            "bid_depth": depth,
            "slippage_pct": 1.0,
        }

    slippage = (entry_price - bid_price) / entry_price if entry_price > 0 else 1.0

    if slippage > max_slippage:
        return {
            "can_sell": False,
            "reason": f"Slippage too high ({slippage:.0%}): bid ${bid_price:.2f} vs entry ${entry_price:.2f}",
            "best_bid": bid_price,
            "bid_depth": depth,
            "slippage_pct": slippage,
        }

    if depth < shares * 0.5:
        return {
            "can_sell": False,
            "reason": f"Thin depth ({depth:.1f} shares) vs sell size ({shares:.1f})",
            "best_bid": bid_price,
            "bid_depth": depth,
            "slippage_pct": slippage,
        }

    return {
        "can_sell": True,
        "reason": f"Bid ${bid_price:.2f} (depth {depth:.1f}) — OK",
        "best_bid": bid_price,
        "bid_depth": depth,
        "slippage_pct": slippage,
    }


# ═══════════════════════════════════════════════════════════════
# WS Message Parsers
# ═══════════════════════════════════════════════════════════════


def _parse_book_message(data: dict):
    """Parse a 'book' event: full orderbook snapshot."""
    asset_id = data.get("asset_id", "")
    if asset_id not in _subscribed_tokens:
        return

    bids = []
    for b in data.get("bids", []):
        price = float(b.get("price", 0))
        size = float(b.get("size", 0))
        if size > 0:
            bids.append((price, size))

    asks = []
    for a in data.get("asks", []):
        price = float(a.get("price", 0))
        size = float(a.get("size", 0))
        if size > 0:
            asks.append((price, size))

    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    now = time.time()
    with _lock:
        _orderbooks[asset_id] = {
            "bids": bids,
            "asks": asks,
            "updated": now,
        }
        # Derive mid-price from book
        if bids and asks:
            mid = (bids[0][0] + asks[0][0]) / 2.0
            _prices[asset_id] = {"price": mid, "updated": now}
        elif bids:
            _prices[asset_id] = {"price": bids[0][0], "updated": now}
        elif asks:
            _prices[asset_id] = {"price": asks[0][0], "updated": now}


def _parse_price_change(data: dict):
    """Parse 'price_change' event: incremental orderbook update."""
    changes = data.get("changes", [])
    now = time.time()

    for change in changes:
        asset_id = change.get("asset_id", "")
        if asset_id not in _subscribed_tokens:
            continue

        price = float(change.get("price", 0))
        size = float(change.get("size", 0))
        side = change.get("side", "").lower()

        with _lock:
            if asset_id not in _orderbooks:
                _orderbooks[asset_id] = {"bids": [], "asks": [], "updated": now}

            book = _orderbooks[asset_id]

            if side == "buy":
                book["bids"] = [
                    (p, s) for p, s in book["bids"] if abs(p - price) > 0.0001
                ]
                if size > 0:
                    book["bids"].append((price, size))
                book["bids"].sort(key=lambda x: x[0], reverse=True)
            elif side == "sell":
                book["asks"] = [
                    (p, s) for p, s in book["asks"] if abs(p - price) > 0.0001
                ]
                if size > 0:
                    book["asks"].append((price, size))
                book["asks"].sort(key=lambda x: x[0])

            book["updated"] = now

            # Update mid-price
            bids = book["bids"]
            asks = book["asks"]
            if bids and asks:
                mid = (bids[0][0] + asks[0][0]) / 2.0
                _prices[asset_id] = {"price": mid, "updated": now}


def _parse_last_trade_price(data: dict):
    """Parse 'last_trade_price' event: latest trade execution price."""
    asset_id = data.get("asset_id", "")
    if asset_id not in _subscribed_tokens:
        return

    ltp = float(data.get("price", 0))
    if ltp > 0:
        with _lock:
            _prices[asset_id] = {"price": ltp, "updated": time.time()}


# ═══════════════════════════════════════════════════════════════
# WebSocket Connection
# ═══════════════════════════════════════════════════════════════

_pending_subscribe: list[str] = []


async def _ws_main():
    """Main WebSocket loop with auto-reconnection."""
    global _ws_running, _ws_connection

    while _ws_running:
        try:
            async with websockets.connect(
                WS_URL, ping_interval=20, ping_timeout=10
            ) as ws:
                _ws_connection = ws
                logger.info("WS connected to %s", WS_URL)

                # Subscribe to all tracked tokens
                tokens = list(_subscribed_tokens)
                if tokens:
                    sub_msg = {
                        "assets_ids": tokens,
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info("WS subscribed to %d tokens", len(tokens))
                    print(f"  [WS] Connected — tracking {len(tokens)} tokens")

                async for raw_msg in ws:
                    if not _ws_running:
                        break

                    # Check for pending subscriptions (hot-add)
                    if _pending_subscribe:
                        new_tokens = list(_pending_subscribe)
                        _pending_subscribe.clear()
                        sub_msg = {
                            "assets_ids": new_tokens,
                            "type": "market",
                            "custom_feature_enabled": True,
                        }
                        await ws.send(json.dumps(sub_msg))
                        logger.info("WS hot-subscribed %d new tokens", len(new_tokens))

                    try:
                        data = json.loads(raw_msg)

                        # WS sends arrays (e.g. empty [] as subscription ACK)
                        if isinstance(data, list):
                            continue

                        if not isinstance(data, dict):
                            continue

                        event_type = data.get("event_type", "")

                        if event_type == "book":
                            _parse_book_message(data)
                        elif event_type == "price_change":
                            _parse_price_change(data)
                        elif event_type == "last_trade_price":
                            _parse_last_trade_price(data)
                        # new_market, tick_size_change, market_resolved — ignored
                    except json.JSONDecodeError:
                        pass

        except Exception as exc:
            _ws_connection = None
            if _ws_running:
                logger.warning("WS disconnected: %s — reconnecting in 3s", exc)
                await asyncio.sleep(3)

    _ws_connection = None


def _ws_thread_target():
    """Thread target that runs the async WS event loop."""
    global _ws_loop
    _ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_ws_loop)
    _ws_loop.run_until_complete(_ws_main())


# ═══════════════════════════════════════════════════════════════
# Public Control API
# ═══════════════════════════════════════════════════════════════


def start(token_ids: list[str] | None = None):
    """Start the WebSocket monitor in a background daemon thread."""
    global _ws_thread, _ws_running

    if token_ids:
        _subscribed_tokens.update(t for t in token_ids if t)

    if _ws_running:
        # Already running — hot-add any new tokens
        if token_ids:
            new = [t for t in token_ids if t and t not in _subscribed_tokens]
            if new:
                _subscribed_tokens.update(new)
                _pending_subscribe.extend(new)
        return

    _ws_running = True
    _ws_thread = threading.Thread(
        target=_ws_thread_target, daemon=True, name="market-ws"
    )
    _ws_thread.start()
    logger.info("WS monitor started (tracking %d tokens)", len(_subscribed_tokens))


def subscribe(token_ids: list[str]):
    """Add new token_ids to track. Can be called while running."""
    new_tokens = [t for t in token_ids if t and t not in _subscribed_tokens]
    if not new_tokens:
        return

    _subscribed_tokens.update(new_tokens)
    _pending_subscribe.extend(new_tokens)
    logger.info("WS: queued %d tokens for subscription", len(new_tokens))


def stop():
    """Stop the WebSocket monitor."""
    global _ws_running, _ws_thread, _ws_connection
    _ws_running = False
    _ws_connection = None
    if _ws_thread:
        _ws_thread.join(timeout=5)
        _ws_thread = None
    logger.info("WS monitor stopped")


def is_running() -> bool:
    """Check if the WS monitor is running."""
    return _ws_running


def get_status() -> dict:
    """Get status summary."""
    with _lock:
        stale = time.time() - 30
        active_books = sum(1 for b in _orderbooks.values() if b.get("updated", 0) > stale)
        active_prices = sum(1 for p in _prices.values() if p.get("updated", 0) > stale)

    return {
        "running": _ws_running,
        "subscribed": len(_subscribed_tokens),
        "active_books": active_books,
        "active_prices": active_prices,
    }
