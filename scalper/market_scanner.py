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
import re
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


def _compute_market_slots(now: datetime, duration_minutes: int = 5, count: int = 6) -> list[int]:
    """
    Compute the unix timestamps for the current and upcoming market slots.
    
    If duration is 5, boundaries are :00, :05, :10...
    If duration is 15, boundaries are :00, :15, :30...
    """
    # Convert to unix timestamp
    ts = int(now.timestamp())

    # Round DOWN to the previous boundary
    slot_seconds = duration_minutes * 60
    current_slot = (ts // slot_seconds) * slot_seconds

    # Generate current + next N slots
    # Start from 1 slot BEFORE current to catch in-progress markets
    slots = []
    for i in range(-1, count):
        slots.append(current_slot + (i * slot_seconds))

    return slots


def _slug_for_asset(asset_key: str, timestamp: int, duration_str: str = "5m") -> str:
    """
    Construct the event slug for a given asset and timestamp.

    Pattern: {asset}-updown-{duration}-{timestamp}
    """
    prefix_map = {
        "BTC": f"btc-updown-{duration_str}",
        "ETH": f"eth-updown-{duration_str}",
        "SOL": f"sol-updown-{duration_str}",
        "XRP": f"xrp-updown-{duration_str}",
    }
    prefix = prefix_map.get(asset_key, f"{asset_key.lower()}-updown-{duration_str}")
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

    # Safely parse outcomes array (sometimes ["Yes", "No"], sometimes ["No", "Yes"])
    outcomes_raw = market.get("outcomes", "[\"Yes\", \"No\"]")
    if isinstance(outcomes_raw, str):
        try:
            outcomes = json.loads(outcomes_raw)
        except json.JSONDecodeError:
            outcomes = ["Yes", "No"]
    else:
        outcomes = outcomes_raw

    up_idx = 0
    down_idx = 1
    for i, o in enumerate(outcomes):
        ol = str(o).lower()
        if ol in ("yes", "up"):
            up_idx = i
        elif ol in ("no", "down"):
            down_idx = i

    # Parse outcome prices
    try:
        outcome_prices_raw = market.get("outcomePrices", "[\"0.5\", \"0.5\"]")
        if isinstance(outcome_prices_raw, str):
            outcome_prices = json.loads(outcome_prices_raw)
        else:
            outcome_prices = outcome_prices_raw
        
        up_price = float(outcome_prices[up_idx]) if len(outcome_prices) > up_idx else 0.5
        down_price = float(outcome_prices[down_idx]) if len(outcome_prices) > down_idx else 0.5
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

    # Extract CLOB token IDs for live trading
    clob_token_ids_raw = market.get("clobTokenIds", "[]")
    if isinstance(clob_token_ids_raw, str):
        try:
            clob_token_ids = json.loads(clob_token_ids_raw)
        except json.JSONDecodeError:
            clob_token_ids = []
    else:
        clob_token_ids = clob_token_ids_raw or []

    up_token_id = clob_token_ids[up_idx] if len(clob_token_ids) > up_idx else ""
    down_token_id = clob_token_ids[down_idx] if len(clob_token_ids) > down_idx else ""

    # Gamma API returns bestBid/bestAsk for outcome at index 0
    raw_best_bid = float(market.get("bestBid", 0) or 0)
    raw_best_ask = float(market.get("bestAsk", 0) or 0)

    # Assign correctly based on which outcome is at index 0
    if up_idx == 0:
        up_best_bid = raw_best_bid
        up_best_ask = raw_best_ask
        down_best_bid = round(1.0 - up_best_ask, 4) if up_best_ask > 0 else down_price
        down_best_ask = round(1.0 - up_best_bid, 4) if up_best_bid > 0 else down_price
    else:
        down_best_bid = raw_best_bid
        down_best_ask = raw_best_ask
        up_best_bid = round(1.0 - down_best_ask, 4) if down_best_ask > 0 else up_price
        up_best_ask = round(1.0 - down_best_bid, 4) if down_best_bid > 0 else up_price

    title = market.get("question", event.get("title", ""))
    strike_price = 0.0
    match = re.search(r'\$([\d,]+(?:\.\d+)?)', title)
    if match:
        try:
            strike_price = float(match.group(1).replace(',', ''))
        except ValueError:
            pass

    return {
        "asset": asset,
        "gamma_id": market.get("id", ""),
        "event_id": event.get("id", ""),
        "slug": market.get("slug", ""),
        "title": market.get("question", event.get("title", "")),
        "strike_price": strike_price,
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
    duration_minutes: int = 5,
) -> dict[str, dict]:
    """
    Scan all configured assets and find the best active/upcoming
    market for each.

    Hybrid strategy:
    1. REST discovery: find active markets by slug (needed for metadata)
    2. WS subscription: subscribe token_ids for real-time price updates
    3. WS price overlay: on subsequent calls, use WS prices instead of
       stale REST outcomePrices (eliminates ~24 API calls per cycle)

    Returns dict keyed by asset symbol with market data.
    """
    target_assets = assets or HFT_ASSETS
    now = datetime.now(timezone.utc)
    slots = _compute_market_slots(now, duration_minutes=duration_minutes, count=6)
    results = {}

    # Collect token_ids for WS subscription
    ws_token_ids = []

    for asset_key, asset_cfg in target_assets.items():
        best_market = None
        best_time_to_start = None

        for ts in slots:
            duration_str = f"{duration_minutes}m"
            slug = _slug_for_asset(asset_key, ts, duration_str=duration_str)
            event = _fetch_event_by_slug(slug)
            if not event:
                continue

            market_data = _extract_market_data(event, asset_key)
            if not market_data:
                continue

            event_start = market_data["event_start"]
            event_end = market_data["event_end"]
            
            # Allow keeping markets that just closed (up to 30s ago)
            # so the Oracle Sniper can catch them at the exact expiry second
            time_since_end = (now - event_end).total_seconds()
            if market_data["closed"] and time_since_end > 30:
                continue

            time_to_start = (event_start - now).total_seconds()

            is_upcoming = time_to_start > 0
            is_in_progress = (event_start <= now <= event_end)
            is_recently_ended = (0 < time_since_end <= 30)

            if not (is_upcoming or is_in_progress or is_recently_ended):
                continue

            # Pick the one closest to starting (or currently running)
            if is_in_progress or is_recently_ended:
                effective_time = -1  # Active/resolving market takes priority
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
                logger.info("WS started with %d tokens", len(ws_token_ids))
            else:
                ws_subscribe(ws_token_ids)
        except Exception as ws_exc:
            logger.warning("WS start/subscribe failed: %s", ws_exc)
            print(f"  [WS] ⚠️ Failed to start: {ws_exc}")
    else:
        logger.debug("No token IDs found for WS subscription")

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


def prefetch_next_market_tokens(
    assets: dict | None = None,
    duration_minutes: int = 5,
) -> list[str]:
    """
    Proactively fetch token IDs for the NEXT market slot.

    Called every cycle to pre-subscribe tokens to the WS ~60s before
    the market opens. This eliminates the "cold start" problem where
    velocity is always 0 because the WS has no history at cycle start.

    Returns list of token_ids (up + down for each asset).
    """
    target_assets = assets or HFT_ASSETS
    now = datetime.now(timezone.utc)
    ts = int(now.timestamp())
    slot_seconds = duration_minutes * 60

    # Next slot = first boundary AFTER current time
    next_slot = ((ts // slot_seconds) + 1) * slot_seconds

    token_ids = []
    duration_str = f"{duration_minutes}m"

    for asset_key in target_assets:
        slug = _slug_for_asset(asset_key, next_slot, duration_str=duration_str)
        try:
            event = _fetch_event_by_slug(slug)
            if not event:
                continue

            markets = event.get("markets", [])
            if not markets:
                continue

            market = markets[0]
            clob_raw = market.get("clobTokenIds", "[]")
            if isinstance(clob_raw, str):
                clob_ids = json.loads(clob_raw)
            else:
                clob_ids = clob_raw or []

            for tid in clob_ids:
                if tid:
                    token_ids.append(tid)
        except Exception:
            continue

    if token_ids:
        logger.info(
            "Prefetched %d tokens for next slot %d (%ds ahead)",
            len(token_ids), next_slot,
            next_slot - ts,
        )

    return token_ids

