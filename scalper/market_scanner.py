"""
scalper/market_scanner.py — Descubre mercados activos de 5 minutos
en Polymarket usando la Gamma API.

ESTRATEGIA: Calcula los slugs de los próximos mercados de 5 minutos
basándose en la hora UTC actual, y los busca directamente por slug.
Esto es mucho más fiable que buscar entre miles de eventos abiertos.
"""

import json
import logging
import math
from datetime import datetime, timezone, timedelta

import requests

from scalper.config import GAMMA_API_BASE, HFT_ASSETS

logger = logging.getLogger("polybot.scalper.scanner")


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _parse_iso(dt_str: str) -> datetime:
    """Parse ISO datetime string to timezone-aware datetime."""
    cleaned = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)


def _compute_5m_slots(now: datetime, count: int = 6) -> list[int]:
    """
    Compute the unix timestamps for the current and upcoming
    5-minute market slots.

    Markets use 5-minute boundaries aligned to the clock:
    :00, :05, :10, :15, :20, :25, :30, :35, :40, :45, :50, :55

    Returns list of unix timestamps, e.g.:
    [1777651200, 1777651500, 1777651800, ...]
    """
    # Convert to unix timestamp
    ts = int(now.timestamp())

    # Round DOWN to the previous 5-minute boundary
    slot_seconds = 300  # 5 minutes
    current_slot = (ts // slot_seconds) * slot_seconds

    # Generate current + next N slots
    # Start from 1 slot BEFORE current to catch in-progress markets
    slots = []
    for i in range(-1, count):
        slots.append(current_slot + (i * slot_seconds))

    return slots


def _slug_for_asset(asset_key: str, timestamp: int) -> str:
    """
    Construct the event slug for a given asset and timestamp.

    Pattern: {asset}-updown-5m-{timestamp}
    """
    prefix_map = {
        "BTC": "btc-updown-5m",
        "ETH": "eth-updown-5m",
        "SOL": "sol-updown-5m",
        "XRP": "xrp-updown-5m",
    }
    prefix = prefix_map.get(asset_key, f"{asset_key.lower()}-updown-5m")
    return f"{prefix}-{timestamp}"


# ═══════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════


def _fetch_event_by_slug(slug: str) -> dict | None:
    """Fetch a specific event by its slug from Gamma API."""
    url = f"{GAMMA_API_BASE}/events"
    params = {"slug": slug}

    try:
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        events = resp.json()
        if events and len(events) > 0:
            return events[0]
        return None
    except requests.RequestException as exc:
        logger.debug("No event found for slug %s: %s", slug, exc)
        return None


def _extract_market_data(event: dict, asset: str) -> dict | None:
    """Extract relevant market data from a Gamma API event object."""
    markets = event.get("markets", [])
    if not markets:
        return None

    market = markets[0]  # Single-market events for 5m up/down

    # Parse outcome prices
    try:
        outcome_prices_raw = market.get("outcomePrices", "[\"0.5\", \"0.5\"]")
        if isinstance(outcome_prices_raw, str):
            outcome_prices = json.loads(outcome_prices_raw)
        else:
            outcome_prices = outcome_prices_raw
        up_price = float(outcome_prices[0])
        down_price = float(outcome_prices[1])
    except (json.JSONDecodeError, IndexError, TypeError, ValueError):
        up_price = 0.5
        down_price = 0.5

    # Parse eventStartTime (when the 5-minute window begins)
    event_start_str = market.get("eventStartTime")
    if not event_start_str:
        event_start_str = event.get("startTime")
    if not event_start_str:
        return None

    event_start = _parse_iso(event_start_str)

    # Parse endDate (when the 5-minute window ends)
    end_date_str = market.get("endDate") or event.get("endDate")
    if not end_date_str:
        return None
    event_end = _parse_iso(end_date_str)

    # Extract best bid/ask (API returns these for the UP outcome)
    up_best_bid = float(market.get("bestBid", 0) or 0)
    up_best_ask = float(market.get("bestAsk", 0) or 0)

    # Calculate DOWN-side bid/ask from UP-side (inverse relationship)
    # DOWN best_bid ≈ 1 - UP best_ask
    # DOWN best_ask ≈ 1 - UP best_bid
    down_best_bid = round(1.0 - up_best_ask, 4) if up_best_ask > 0 else down_price
    down_best_ask = round(1.0 - up_best_bid, 4) if up_best_bid > 0 else down_price

    # Extract CLOB token IDs for live trading (UP=0, DOWN=1)
    clob_token_ids_raw = market.get("clobTokenIds", "[]")
    if isinstance(clob_token_ids_raw, str):
        try:
            clob_token_ids = json.loads(clob_token_ids_raw)
        except json.JSONDecodeError:
            clob_token_ids = []
    else:
        clob_token_ids = clob_token_ids_raw or []

    up_token_id = clob_token_ids[0] if len(clob_token_ids) > 0 else ""
    down_token_id = clob_token_ids[1] if len(clob_token_ids) > 1 else ""

    return {
        "asset": asset,
        "gamma_id": market.get("id", ""),
        "event_id": event.get("id", ""),
        "slug": market.get("slug", ""),
        "title": market.get("question", event.get("title", "")),
        "event_start": event_start,
        "event_end": event_end,
        "up_price": up_price,
        "down_price": down_price,
        # Per-side bid/ask for correct entry/exit pricing
        "up_best_bid": up_best_bid,
        "up_best_ask": up_best_ask,
        "down_best_bid": down_best_bid,
        "down_best_ask": down_best_ask,
        # CLOB token IDs for live trading
        "up_token_id": up_token_id,
        "down_token_id": down_token_id,
        # Legacy (UP side) — kept for backward compat
        "best_bid": up_best_bid,
        "best_ask": up_best_ask,
        "accepting_orders": market.get("acceptingOrders", False),
        "closed": market.get("closed", False),
        "volume": float(market.get("volumeNum", 0) or 0),
        "liquidity": float(market.get("liquidityNum", 0) or 0),
        "last_trade_price": float(market.get("lastTradePrice", 0) or 0),
    }


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════


def scan_active_markets(
    assets: dict | None = None,
) -> dict[str, dict]:
    """
    Scan all configured assets and find the best active/upcoming
    5-minute market for each.

    Hybrid strategy:
    1. REST discovery: find active markets by slug (needed for metadata)
    2. WS subscription: subscribe token_ids for real-time price updates
    3. WS price overlay: on subsequent calls, use WS prices instead of
       stale REST outcomePrices (eliminates ~24 API calls per cycle)

    Returns dict keyed by asset symbol with market data.
    """
    target_assets = assets or HFT_ASSETS
    now = datetime.now(timezone.utc)
    slots = _compute_5m_slots(now, count=6)
    results = {}

    # Collect token_ids for WS subscription
    ws_token_ids = []

    for asset_key, asset_cfg in target_assets.items():
        best_market = None
        best_time_to_start = None

        for ts in slots:
            slug = _slug_for_asset(asset_key, ts)
            event = _fetch_event_by_slug(slug)
            if not event:
                continue

            market_data = _extract_market_data(event, asset_key)
            if not market_data:
                continue

            # Skip closed markets
            if market_data["closed"]:
                continue

            event_start = market_data["event_start"]
            event_end = market_data["event_end"]
            time_to_start = (event_start - now).total_seconds()

            is_upcoming = time_to_start > 0
            is_in_progress = (event_start <= now <= event_end)

            if not (is_upcoming or is_in_progress):
                continue

            # Pick the one closest to starting (or currently running)
            if is_in_progress:
                effective_time = -1  # Active market takes priority
            else:
                effective_time = time_to_start

            if best_time_to_start is None or effective_time < best_time_to_start:
                best_time_to_start = effective_time
                best_market = market_data
                best_market["time_to_start_sec"] = time_to_start
                best_market["is_in_progress"] = is_in_progress

        if best_market:
            # Collect token IDs for WS subscription
            up_tid = best_market.get("up_token_id", "")
            dn_tid = best_market.get("down_token_id", "")
            if up_tid:
                ws_token_ids.append(up_tid)
            if dn_tid:
                ws_token_ids.append(dn_tid)

            # Overlay WS prices if available (fresher than REST)
            try:
                from scalper.orderbook_ws import get_price, is_running as ws_running
                if ws_running() and up_tid and dn_tid:
                    ws_up = get_price(up_tid)
                    ws_dn = get_price(dn_tid)
                    if ws_up is not None:
                        best_market["up_price"] = ws_up
                        best_market["down_price"] = 1.0 - ws_up
                        best_market["_price_source"] = "ws"
                    elif ws_dn is not None:
                        best_market["down_price"] = ws_dn
                        best_market["up_price"] = 1.0 - ws_dn
                        best_market["_price_source"] = "ws"
                    if ws_up is not None and ws_dn is not None:
                        best_market["up_price"] = ws_up
                        best_market["down_price"] = ws_dn
                        best_market["_price_source"] = "ws"
            except ImportError:
                pass

            results[asset_key] = best_market
            source = best_market.get("_price_source", "rest")
            state = "IN PROGRESS" if best_market["is_in_progress"] else f"in {best_market['time_to_start_sec']:.0f}s"
            logger.debug(
                "Found market for %s: %s (%s) [prices: %s]",
                asset_key, best_market["slug"], state, source,
            )

    # Subscribe all discovered tokens to WS (idempotent)
    if ws_token_ids:
        try:
            from scalper.orderbook_ws import start as ws_start, subscribe as ws_subscribe, is_running as ws_running
            if not ws_running():
                ws_start(ws_token_ids)
            else:
                ws_subscribe(ws_token_ids)
        except ImportError:
            pass

    return results


def get_market_current_price(gamma_id: str) -> dict | None:
    """
    Fetch current prices for a specific market by gamma_id.
    Used for monitoring open positions.
    """
    url = f"{GAMMA_API_BASE}/markets/{gamma_id}"
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        outcome_prices_raw = data.get("outcomePrices", "[\"0.5\", \"0.5\"]")
        if isinstance(outcome_prices_raw, str):
            outcome_prices = json.loads(outcome_prices_raw)
        else:
            outcome_prices = outcome_prices_raw

        up_price = float(outcome_prices[0])
        down_price = float(outcome_prices[1])
        up_best_bid = float(data.get("bestBid", 0) or 0)
        up_best_ask = float(data.get("bestAsk", 0) or 0)

        # Calculate DOWN-side bid/ask from UP-side
        down_best_bid = round(1.0 - up_best_ask, 4) if up_best_ask > 0 else down_price
        down_best_ask = round(1.0 - up_best_bid, 4) if up_best_bid > 0 else down_price

        return {
            "up_price": up_price,
            "down_price": down_price,
            "up_best_bid": up_best_bid,
            "up_best_ask": up_best_ask,
            "down_best_bid": down_best_bid,
            "down_best_ask": down_best_ask,
            "best_bid": up_best_bid,
            "best_ask": up_best_ask,
            "closed": data.get("closed", False),
            "outcome_prices": outcome_prices,
        }
    except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Error fetching market %s: %s", gamma_id, exc)
        return None
