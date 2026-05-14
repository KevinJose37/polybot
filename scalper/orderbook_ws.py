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
from collections import deque

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

# token_id → deque of (timestamp, mid_price) — 2 minutes of history at max
_mid_history: dict[str, deque] = {}
_ask_history: dict[str, deque] = {}
_callbacks: list = []

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


def update_mid_history(token_id: str, mid_price: float, best_ask: float = 0.0) -> None:
    """
    Append a (timestamp, mid_price) sample to the circular buffer for token_id.
    Also tracks best_ask history for V12 Event-Driven Engine.
    Called internally every time the book snapshot produces a new mid-price.
    Buffer holds up to 120 samples (~2 minutes at 1 sample/sec).
    """
    # NOTE: must be called with _lock already held
    if token_id not in _mid_history:
        _mid_history[token_id] = deque(maxlen=120)
    _mid_history[token_id].append((time.time(), mid_price))
    
    if best_ask > 0:
        if token_id not in _ask_history:
            _ask_history[token_id] = deque(maxlen=200) # Holds ~200 events for ultra-fast ms tracking
        _ask_history[token_id].append((time.time(), best_ask))


def get_ask_velocity(token_id: str, window_ms: int = 500) -> float:
    """
    V12 Microstructure: Compute how much the Polymarket best_ask has moved in the last `window_ms`.
    Returns:
        float: (current_ask - oldest_ask_in_window)
               Positive -> ask price is shooting up (bullish explosion or toxicity)
               0.0 -> healthy, stable, or missing data.
    """
    now = time.time()
    cutoff = now - (window_ms / 1000.0)

    with _lock:
        history = _ask_history.get(token_id)
        if not history or len(history) < 2:
            return 0.0

        current_ask = history[-1][1]
        
        # Find the oldest ask within the time window
        older_samples = [(t, p) for t, p in history if t >= cutoff]
        if older_samples:
            oldest_ask = older_samples[0][1]
            return round(current_ask - oldest_ask, 4)
        return 0.0


def register_book_callback(func) -> None:
    """V12: Register a callback to be fired on every tick of the orderbook."""
    with _lock:
        if func not in _callbacks:
            _callbacks.append(func)

def _fire_book_callbacks(token_id: str, book: dict) -> None:
    """Fire all registered callbacks with the new orderbook state."""
    # Run outside the lock to avoid deadlocks
    callbacks = []
    with _lock:
        callbacks = list(_callbacks)
    
    for cb in callbacks:
        try:
            cb(token_id, book)
        except Exception as e:
            logger.error("Error in WS book callback: %s", e)

def get_mid_velocity(token_id: str, window_sec: int = 30) -> float:
    """
    Compute how much the Polymarket mid-price has moved in the last `window_sec`.

    Returns:
        float: (current_mid - oldest_mid_in_window)
               Positive → price rising (orderbook leans UP)
               Negative → price falling (orderbook leans DOWN)
               0.0      → insufficient data (<3 samples in window)

    Thread-safe; can be called from any thread.
    """
    now = time.time()
    cutoff = now - window_sec

    with _lock:
        history = _mid_history.get(token_id)
        if not history:
            return 0.0

        current_price = history[-1][1]
        
        # Find the last known price at or before cutoff
        older_samples = [(t, p) for t, p in history if t <= cutoff]
        if older_samples:
            oldest_price = older_samples[-1][1]
        else:
            # If no samples before cutoff, the earliest sample we have is the oldest price
            oldest_price = history[0][1]

    return round(current_price - oldest_price, 4)


def debug_mid_history(token_id: str) -> dict:
    """Return debug info about the mid_history buffer for a token."""
    now = time.time()
    with _lock:
        history = _mid_history.get(token_id)
        in_prices = token_id in _prices
        if not history:
            return {"entries": 0, "in_prices": in_prices}

        samples_10s = [(t, p) for t, p in history if t >= now - 10]
        samples_30s = [(t, p) for t, p in history if t >= now - 30]
        latest_mid = history[-1][1]
        age_newest_sec = round(now - history[-1][0], 1)
        entries = len(history)

    prices_10 = [p for _, p in samples_10s]
    unique_10 = len(set(prices_10))

    return {
        "entries": entries,
        "in_10s": len(samples_10s),
        "in_30s": len(samples_30s),
        "unique_prices_10s": unique_10,
        "latest_mid": latest_mid,
        "age_newest_sec": age_newest_sec,
        "in_prices": in_prices,
    }

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


def get_book_summary(token_id: str) -> dict | None:
    """
    Get a full orderbook summary for a token.

    Returns dict with:
        best_bid, best_bid_size, best_ask, best_ask_size,
        spread, spread_pct, bid_depth, ask_depth
    Or None if no orderbook data available.
    """
    with _lock:
        book = _orderbooks.get(token_id)
        if not book or (time.time() - book.get("updated", 0)) > 60:
            return None

        bids = book.get("bids", [])
        asks = book.get("asks", [])

    best_bid = bids[0][0] if bids else 0.0
    best_bid_size = bids[0][1] if bids else 0.0
    best_ask = asks[0][0] if asks else 0.0
    best_ask_size = asks[0][1] if asks else 0.0

    spread = round(best_ask - best_bid, 4) if (best_bid > 0 and best_ask > 0) else 0.0
    mid = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0
    spread_pct = round(spread / mid, 4) if mid > 0 else 0.0

    bid_depth = sum(s for _p, s in bids)
    ask_depth = sum(s for _p, s in asks)

    return {
        "best_bid": best_bid,
        "best_bid_size": best_bid_size,
        "best_ask": best_ask,
        "best_ask_size": best_ask_size,
        "spread": spread,
        "spread_pct": spread_pct,
        "bid_depth": round(bid_depth, 2),
        "ask_depth": round(ask_depth, 2),
    }


def get_imbalance(token_id: str) -> dict:
    """
    Get the orderbook imbalance ratio.
    Returns:
        {"up_imbalance": float, "down_imbalance": float}
        up_imbalance = ask_depth / max(bid_depth, 1)  (>1 means more sellers, against UP)
        down_imbalance = bid_depth / max(ask_depth, 1) (>1 means more buyers, against DOWN)
    """
    summary = get_book_summary(token_id)
    if not summary:
        return {"up_imbalance": 1.0, "down_imbalance": 1.0}
    
    bid_depth = summary["bid_depth"]
    ask_depth = summary["ask_depth"]
    
    up_imbalance = ask_depth / max(bid_depth, 1.0)
    down_imbalance = bid_depth / max(ask_depth, 1.0)
    
    return {"up_imbalance": round(up_imbalance, 2), "down_imbalance": round(down_imbalance, 2)}


def get_price_change(token_id: str, window_sec: int = 120) -> float:
    """
    Compute % change in Polymarket mid-price over the last `window_sec`.
    Returns:
        float: % change (e.g. 0.05 for +5%, -0.02 for -2%)
    """
    with _lock:
        history = _mid_history.get(token_id)
        if not history:
            return 0.0

    now = time.time()
    cutoff = now - window_sec
    window_samples = [(t, p) for t, p in history if t >= cutoff]

    if len(window_samples) < 2:
        return 0.0

    oldest_price = window_samples[0][1]
    current_price = window_samples[-1][1]
    
    if oldest_price <= 0:
        return 0.0
        
    return round((current_price - oldest_price) / oldest_price, 4)


def simulate_market_sell(token_id: str, shares: float) -> dict:
    """
    Walk the bid side of the orderbook to simulate a market sell (FOK).

    Returns dict with:
        can_fill:   bool — True if the full `shares` amount can be filled
        vwap:       float — Volume-Weighted Average Price of the fill
        best_bid:   float — Top-of-book bid price
        filled_qty: float — How many shares could actually be filled
        total_depth: float — Total bid depth on book
        levels_used: int — How many price levels were consumed
    """
    with _lock:
        book = _orderbooks.get(token_id)
        if not book or (time.time() - book.get("updated", 0)) > 60:
            return {
                "can_fill": False, "vwap": 0.0, "best_bid": 0.0,
                "filled_qty": 0.0, "total_depth": 0.0, "levels_used": 0,
            }
        # Copy bids to avoid holding the lock during computation
        bids = list(book.get("bids", []))

    if not bids:
        return {
            "can_fill": False, "vwap": 0.0, "best_bid": 0.0,
            "filled_qty": 0.0, "total_depth": 0.0, "levels_used": 0,
        }

    best_bid = bids[0][0]
    total_depth = sum(s for _, s in bids)

    remaining = shares
    cost_accum = 0.0  # sum of (price * qty_filled_at_that_level)
    levels_used = 0

    for price, size in bids:
        if remaining <= 0:
            break
        fill_at_level = min(remaining, size)
        cost_accum += price * fill_at_level
        remaining -= fill_at_level
        levels_used += 1

    filled_qty = shares - remaining
    vwap = cost_accum / filled_qty if filled_qty > 0 else 0.0

    return {
        "can_fill": remaining <= 0,
        "vwap": round(vwap, 6),
        "best_bid": best_bid,
        "filled_qty": round(filled_qty, 4),
        "total_depth": round(total_depth, 2),
        "levels_used": levels_used,
    }


def simulate_market_buy(token_id: str, spend_usd: float) -> dict:
    """
    Walk the ask side of the orderbook to simulate a market buy (FOK).

    Given a dollar amount to spend, walks through ask levels accumulating
    shares until the budget is exhausted or the book runs out.

    Returns dict with:
        can_fill:    bool — True if the full budget could be deployed
        vwap:        float — Volume-Weighted Average Price of the fill
        best_ask:    float — Top-of-book ask price
        shares:      float — Total shares acquired
        total_depth: float — Total ask depth on book
        levels_used: int
    """
    with _lock:
        book = _orderbooks.get(token_id)
        if not book or (time.time() - book.get("updated", 0)) > 60:
            return {
                "can_fill": False, "vwap": 0.0, "best_ask": 0.0,
                "shares": 0.0, "total_depth": 0.0, "levels_used": 0,
            }
        asks = list(book.get("asks", []))

    if not asks:
        return {
            "can_fill": False, "vwap": 0.0, "best_ask": 0.0,
            "shares": 0.0, "total_depth": 0.0, "levels_used": 0,
        }

    best_ask = asks[0][0]
    total_depth = sum(s for _, s in asks)

    remaining_usd = spend_usd
    shares_accum = 0.0
    cost_accum = 0.0
    levels_used = 0

    for price, size in asks:
        if remaining_usd <= 0 or price <= 0:
            break
        # Max shares we can buy at this level with remaining budget
        max_shares_at_level = remaining_usd / price
        fill_at_level = min(max_shares_at_level, size)
        level_cost = fill_at_level * price
        cost_accum += level_cost
        shares_accum += fill_at_level
        remaining_usd -= level_cost
        levels_used += 1

    vwap = cost_accum / shares_accum if shares_accum > 0 else 0.0
    # "can_fill" = we deployed at least 95% of the budget (small rounding tolerance)
    can_fill = remaining_usd <= spend_usd * 0.05

    return {
        "can_fill": can_fill,
        "vwap": round(vwap, 6),
        "best_ask": best_ask,
        "shares": round(shares_accum, 4),
        "total_depth": round(total_depth, 2),
        "levels_used": levels_used,
    }


def check_sell_liquidity(
    token_id: str,
    shares: float,
    entry_price: float,
    max_slippage: float = 0.15,
) -> dict:
    """
    Check if there's enough liquidity to sell using orderbook walking.

    Slippage is measured from the MID-PRICE (fair value), NOT from
    entry_price (which would conflate drawdown with execution cost).

    Returns dict with:
        can_sell, reason, best_bid, bid_depth, slippage_pct, vwap
    """
    sim = simulate_market_sell(token_id, shares)

    if sim["best_bid"] <= 0:
        return {
            "can_sell": False,
            "reason": "No orderbook data (WS not connected or no bids)",
            "best_bid": 0,
            "bid_depth": 0,
            "slippage_pct": 1.0,
            "vwap": 0.0,
        }

    if sim["best_bid"] <= 0.01:
        return {
            "can_sell": False,
            "reason": f"Best bid is dust (${sim['best_bid']:.2f})",
            "best_bid": sim["best_bid"],
            "bid_depth": sim["total_depth"],
            "slippage_pct": 1.0,
            "vwap": 0.0,
        }

    if not sim["can_fill"]:
        return {
            "can_sell": False,
            "reason": (
                f"Insufficient depth: need {shares:.1f} shares, "
                f"book has {sim['total_depth']:.1f} (filled {sim['filled_qty']:.1f})"
            ),
            "best_bid": sim["best_bid"],
            "bid_depth": sim["total_depth"],
            "slippage_pct": 1.0,
            "vwap": sim["vwap"],
        }

    # Slippage = distance from mid-price to VWAP execution price
    # This correctly measures execution cost, NOT position P&L
    summary = get_book_summary(token_id)
    if summary and summary["best_ask"] > 0 and summary["best_bid"] > 0:
        mid_price = (summary["best_bid"] + summary["best_ask"]) / 2.0
    else:
        mid_price = sim["best_bid"]  # fallback

    slippage = (mid_price - sim["vwap"]) / mid_price if mid_price > 0 else 0.0

    if slippage > max_slippage:
        return {
            "can_sell": False,
            "reason": (
                f"Execution slippage too high ({slippage:.1%}): "
                f"VWAP ${sim['vwap']:.4f} vs mid ${mid_price:.4f} "
                f"({sim['levels_used']} levels consumed)"
            ),
            "best_bid": sim["best_bid"],
            "bid_depth": sim["total_depth"],
            "slippage_pct": slippage,
            "vwap": sim["vwap"],
        }

    return {
        "can_sell": True,
        "reason": (
            f"VWAP ${sim['vwap']:.4f} ({sim['levels_used']} lvls, "
            f"depth {sim['total_depth']:.1f}) — OK"
        ),
        "best_bid": sim["best_bid"],
        "bid_depth": sim["total_depth"],
        "slippage_pct": round(slippage, 4),
        "vwap": sim["vwap"],
    }


# ═══════════════════════════════════════════════════════════════
# WS Message Parsers
# ═══════════════════════════════════════════════════════════════


def _parse_book_message(data: dict):
    """Parse a 'book' event: full orderbook snapshot."""
    asset_id = data.get("asset_id", data.get("market", ""))
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
            update_mid_history(asset_id, mid, best_ask=asks[0][0])
            _fire_book_callbacks(asset_id, _orderbooks[asset_id])
        elif bids:
            mid = bids[0][0]
            _prices[asset_id] = {"price": mid, "updated": now}
            update_mid_history(asset_id, mid)
            _fire_book_callbacks(asset_id, _orderbooks[asset_id])
        elif asks:
            mid = asks[0][0]
            _prices[asset_id] = {"price": mid, "updated": now}
            update_mid_history(asset_id, mid, best_ask=asks[0][0])
            _fire_book_callbacks(asset_id, _orderbooks[asset_id])


def _parse_price_change(data: dict):
    """Parse 'price_change' event: incremental orderbook update."""
    changes = data.get("price_changes", data.get("changes", []))
    now = time.time()

    for change in changes:
        asset_id = change.get("asset_id", "")
        if asset_id not in _subscribed_tokens:
            continue

        price = float(change.get("price", 0))
        size = float(change.get("size", 0))
        side = change.get("side", "").lower()

        # Server-provided best bid/ask (most reliable — always present)
        msg_best_bid = float(change.get("best_bid", 0) or 0)
        msg_best_ask = float(change.get("best_ask", 0) or 0)

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

            # Primary: use server's best_bid/best_ask for mid-price
            if msg_best_bid > 0 and msg_best_ask > 0:
                mid = (msg_best_bid + msg_best_ask) / 2.0
                _prices[asset_id] = {"price": mid, "updated": now}
                update_mid_history(asset_id, mid, best_ask=msg_best_ask)
                _fire_book_callbacks(asset_id, book)
            elif msg_best_bid > 0:
                mid = msg_best_bid
                _prices[asset_id] = {"price": mid, "updated": now}
                update_mid_history(asset_id, mid)
                _fire_book_callbacks(asset_id, book)
            elif msg_best_ask > 0:
                mid = msg_best_ask
                _prices[asset_id] = {"price": mid, "updated": now}
                update_mid_history(asset_id, mid, best_ask=msg_best_ask)
                _fire_book_callbacks(asset_id, book)
            else:
                # Fallback: reconstruct from local book
                bids = book["bids"]
                asks = book["asks"]
                if bids and asks:
                    mid = (bids[0][0] + asks[0][0]) / 2.0
                    _prices[asset_id] = {"price": mid, "updated": now}
                    update_mid_history(asset_id, mid, best_ask=asks[0][0])
                    _fire_book_callbacks(asset_id, book)
                elif bids:
                    mid = bids[0][0]
                    _prices[asset_id] = {"price": mid, "updated": now}
                    update_mid_history(asset_id, mid)
                    _fire_book_callbacks(asset_id, book)
                elif asks:
                    mid = asks[0][0]
                    _prices[asset_id] = {"price": mid, "updated": now}
                    update_mid_history(asset_id, mid, best_ask=asks[0][0])
                    _fire_book_callbacks(asset_id, book)


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
# WebSocket Connection (Queue-based architecture)
# ═══════════════════════════════════════════════════════════════

_pending_subscribe: list[str] = []

# Performance counters (thread-safe reads are fine for ints)
_ws_msgs_received = 0
_ws_msgs_parsed = 0
_ws_slow_parses = 0
_ws_reconnect_count = 0


async def _ws_parse_worker(queue: asyncio.Queue):
    """
    Worker task: pulls raw JSON strings from the queue and parses them.
    Runs as a separate asyncio task so recv() is never blocked by parsing.
    """
    global _ws_msgs_parsed, _ws_slow_parses

    # Track last_useful_msg here so health check can read it
    while True:
        raw_msg = await queue.get()
        if raw_msg is None:  # Poison pill → shutdown
            break

        t0 = time.perf_counter()
        try:
            data = json.loads(raw_msg)

            # Log list messages instead of silently discarding
            if isinstance(data, list):
                if len(data) > 0:
                    logger.debug(
                        "WS list msg (%d items), first=%s",
                        len(data), str(data[0])[:200],
                    )
                continue
            if not isinstance(data, dict):
                logger.debug("WS non-dict msg: type=%s val=%s", type(data).__name__, str(data)[:200])
                continue

            event_type = data.get("event_type", "")

            # One-shot: log the first message keys for debugging
            if not hasattr(_ws_parse_worker, "_first_logged"):
                _ws_parse_worker._first_logged = True
                logger.info(
                    "WS first msg keys=%s event=%s ts=%s",
                    list(data.keys()), event_type,
                    data.get("timestamp", "MISSING"),
                )

            # Record WS latency
            try:
                from scalper.latency import record_polymarket_ws
                server_ts = data.get("timestamp")
                ts_val = int(server_ts) if server_ts else None
                record_polymarket_ws(ts_val)
            except (ValueError, TypeError, ImportError):
                pass

            if event_type == "book":
                _parse_book_message(data)
            elif event_type == "price_change":
                _parse_price_change(data)
            elif event_type == "last_trade_price":
                _parse_last_trade_price(data)
            else:
                logger.debug("WS unknown event_type=%s keys=%s", event_type, list(data.keys()))

            _ws_msgs_parsed += 1

        except json.JSONDecodeError:
            logger.debug("WS invalid JSON: %s", raw_msg[:200])

        dt = time.perf_counter() - t0
        if dt > 0.01:
            _ws_slow_parses += 1
            logger.warning("WS slow parse %.4fs event=%s size=%d",
                           dt, data.get("event_type", "?") if isinstance(data, dict) else "?",
                           len(raw_msg))

        queue.task_done()


async def _ws_main():
    """
    Main WebSocket loop with queue-based architecture.

    recv loop → asyncio.Queue → parse worker (separate task)
    This ensures recv() is never blocked by parsing, preventing
    the server from detecting us as a slow consumer.
    """
    global _ws_running, _ws_connection, _ws_msgs_received, _ws_reconnect_count
    import random

    HEALTH_TIMEOUT = 90  # seconds without ANY message → force reconnect
    retry_delay = 1.0    # Exponential backoff base

    while _ws_running:
        # Create a fresh queue for each connection
        msg_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        parse_task = None

        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=10,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                _ws_connection = ws
                retry_delay = 1.0  # Reset backoff on successful connect
                last_any_msg = time.time()
                logger.info("WS connected to %s", WS_URL)

                # Start the parse worker
                parse_task = asyncio.create_task(_ws_parse_worker(msg_queue))

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

                while _ws_running:
                    # ── Health check: no messages at all → dead connection ──
                    if time.time() - last_any_msg > HEALTH_TIMEOUT:
                        n_tokens = len(_subscribed_tokens)
                        logger.warning(
                            "WS health FAILED — no msgs for %ds (%d tokens). Reconnecting.",
                            HEALTH_TIMEOUT, n_tokens,
                        )
                        print(f"  [WS] No data for {HEALTH_TIMEOUT}s — forcing reconnect")
                        break

                    # ── Check for pending subscriptions ──
                    if _pending_subscribe:
                        _pending_subscribe.clear()
                        all_tokens = list(_subscribed_tokens)
                        sub_msg = {
                            "assets_ids": all_tokens,
                            "type": "market",
                            "custom_feature_enabled": True,
                        }
                        await ws.send(json.dumps(sub_msg))
                        logger.info("WS re-subscribed %d tokens (queue=%d)",
                                    len(all_tokens), msg_queue.qsize())

                    # ── Fast recv: grab message and push to queue immediately ──
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    _ws_msgs_received += 1
                    last_any_msg = time.time()

                    # Non-blocking put: if queue is full, drop oldest
                    if msg_queue.full():
                        try:
                            msg_queue.get_nowait()
                            logger.warning("WS queue full (%d) — dropped oldest msg",
                                           msg_queue.maxsize)
                        except asyncio.QueueEmpty:
                            pass
                    await msg_queue.put(raw_msg)

        except Exception as exc:
            _ws_connection = None
            _ws_reconnect_count += 1
            if _ws_running:
                # Exponential backoff with jitter
                jitter = random.uniform(0, retry_delay * 0.5)
                wait = retry_delay + jitter
                logger.warning(
                    "WS disconnected (#%d): %s — reconnecting in %.1fs",
                    _ws_reconnect_count, exc, wait,
                )
                await asyncio.sleep(wait)
                retry_delay = min(retry_delay * 2, 30)  # Cap at 30s
        finally:
            # Shutdown parse worker cleanly
            if parse_task and not parse_task.done():
                await msg_queue.put(None)  # Poison pill
                try:
                    await asyncio.wait_for(parse_task, timeout=2.0)
                except asyncio.TimeoutError:
                    parse_task.cancel()

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


def replace_subscriptions(token_ids: list[str]):
    """
    Replace the subscription set with only these tokens.
    Only triggers a WS re-subscribe if the set actually changed.
    Clears stale data for removed tokens.
    """
    new_set = set(t for t in token_ids if t)

    # Fast path: no change → skip entirely (preserves existing data flow)
    if new_set == _subscribed_tokens:
        return

    removed = _subscribed_tokens - new_set
    added = new_set - _subscribed_tokens

    # Clean up stale data for removed tokens
    if removed:
        with _lock:
            for tid in removed:
                _mid_history.pop(tid, None)
                _prices.pop(tid, None)
                _orderbooks.pop(tid, None)

    _subscribed_tokens.clear()
    _subscribed_tokens.update(new_set)

    # Only re-subscribe if tokens actually changed
    _pending_subscribe.append("__resub__")
    logger.info("WS: subscription updated → %d tokens (+%d/-%d)",
                len(new_set), len(added), len(removed))


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
    """Get status summary including performance counters."""
    with _lock:
        stale = time.time() - 30
        active_books = sum(1 for b in _orderbooks.values() if b.get("updated", 0) > stale)
        active_prices = sum(1 for p in _prices.values() if p.get("updated", 0) > stale)

    return {
        "running": _ws_running,
        "subscribed": len(_subscribed_tokens),
        "active_books": active_books,
        "active_prices": active_prices,
        "msgs_received": _ws_msgs_received,
        "msgs_parsed": _ws_msgs_parsed,
        "slow_parses": _ws_slow_parses,
        "reconnects": _ws_reconnect_count,
    }
