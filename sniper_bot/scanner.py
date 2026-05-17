"""
sniper_bot/scanner.py — Market discovery via Gamma REST API.

This is the ONLY module that touches Gamma API.
Discovers 5-minute markets by computing slugs from UTC timestamps.
Returns token IDs for WS subscription.
"""
import json
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass

import requests

from .config import SniperConfig

logger = logging.getLogger("sniper_bot.scanner")


@dataclass
class MarketInfo:
    """Discovered market metadata."""
    asset: str
    condition_id: str
    slug: str
    title: str
    event_start: datetime
    event_end: datetime
    up_token_id: str
    down_token_id: str
    strike_price: float = 0.0
    accepting_orders: bool = True
    is_in_progress: bool = False
    time_to_start_sec: float = 0.0


def _parse_iso(dt_str: str) -> datetime:
    cleaned = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)


def _compute_market_slots(now: datetime, duration_minutes: int = 5, count: int = 6) -> list[int]:
    """Compute unix timestamps for current and upcoming market slot boundaries."""
    ts = int(now.timestamp())
    slot_seconds = duration_minutes * 60
    current_slot = (ts // slot_seconds) * slot_seconds

    slots = []
    for i in range(-1, count):
        slots.append(current_slot + (i * slot_seconds))
    return slots


def _slug_for_asset(asset_key: str, timestamp: int, duration_str: str = "5m") -> str:
    """Construct event slug: btc-updown-5m-{timestamp}"""
    prefix = f"{asset_key.lower()}-updown-{duration_str}"
    return f"{prefix}-{timestamp}"


# Simple memory cache for Gamma API events (slug -> (timestamp, data))
_GAMMA_CACHE = {}

def _fetch_event_by_slug(slug: str, gamma_base: str) -> dict | None:
    """Fetch a specific event by its slug from Gamma API."""
    now = time.time()
    
    # Return from cache if less than 60s old
    if slug in _GAMMA_CACHE:
        cache_ts, data = _GAMMA_CACHE[slug]
        if now - cache_ts < 60:
            return data

    url = f"{gamma_base}/events"
    try:
        resp = requests.get(url, params={"slug": slug}, timeout=5)
        resp.raise_for_status()
        events = resp.json()
        data = events[0] if (events and len(events) > 0) else None
        
        # Cache the result
        _GAMMA_CACHE[slug] = (now, data)
        return data
    except requests.RequestException as exc:
        logger.debug("No event for slug %s: %s", slug, exc)
        return None


def _extract_market(event: dict, asset: str) -> MarketInfo | None:
    """Extract MarketInfo from a Gamma API event."""
    markets = event.get("markets", [])
    if not markets:
        return None

    market = markets[0]

    # Parse outcomes
    outcomes_raw = market.get("outcomes", '["Yes", "No"]')
    if isinstance(outcomes_raw, str):
        try:
            outcomes = json.loads(outcomes_raw)
        except json.JSONDecodeError:
            outcomes = ["Yes", "No"]
    else:
        outcomes = outcomes_raw

    up_idx, down_idx = 0, 1
    for i, o in enumerate(outcomes):
        ol = str(o).lower()
        if ol in ("yes", "up"):
            up_idx = i
        elif ol in ("no", "down"):
            down_idx = i

    # Parse times
    start_str = market.get("eventStartTime") or event.get("startTime")
    end_str = market.get("endDate") or event.get("endDate")
    if not start_str or not end_str:
        return None

    event_start = _parse_iso(start_str)
    event_end = _parse_iso(end_str)

    # Token IDs
    clob_raw = market.get("clobTokenIds", "[]")
    if isinstance(clob_raw, str):
        try:
            clob_ids = json.loads(clob_raw)
        except json.JSONDecodeError:
            clob_ids = []
    else:
        clob_ids = clob_raw or []

    up_token = clob_ids[up_idx] if len(clob_ids) > up_idx else ""
    down_token = clob_ids[down_idx] if len(clob_ids) > down_idx else ""

    # Strike price from title
    import re
    title = market.get("question", event.get("title", ""))
    strike = 0.0
    match = re.search(r'\$([\d,]+(?:\.\d+)?)', title)
    if match:
        try:
            strike = float(match.group(1).replace(',', ''))
        except ValueError:
            pass

    return MarketInfo(
        asset=asset,
        condition_id=market.get("id", ""),
        slug=market.get("slug", ""),
        title=title,
        event_start=event_start,
        event_end=event_end,
        up_token_id=up_token,
        down_token_id=down_token,
        strike_price=strike,
        accepting_orders=market.get("acceptingOrders", False),
    )


def scan_markets(config: SniperConfig) -> dict[str, MarketInfo]:
    """
    Scan all configured assets for active/upcoming 5-minute markets.
    Returns dict keyed by asset symbol.
    """
    now = datetime.now(timezone.utc)
    slots = _compute_market_slots(now, duration_minutes=config.market_duration_min, count=6)
    results = {}

    for asset_key in config.assets:
        best_market = None
        best_time = None

        duration_str = f"{config.market_duration_min}m"

        for ts in slots:
            slug = _slug_for_asset(asset_key, ts, duration_str=duration_str)
            event = _fetch_event_by_slug(slug, config.gamma_api_base)
            if not event:
                continue

            info = _extract_market(event, asset_key)
            if not info:
                continue

            time_since_end = (now - info.event_end).total_seconds()
            if time_since_end > 30:
                continue

            time_to_start = (info.event_start - now).total_seconds()
            is_in_progress = (info.event_start <= now <= info.event_end)
            is_recently_ended = 0 < time_since_end <= 30

            if not (time_to_start > 0 or is_in_progress or is_recently_ended):
                continue

            if is_in_progress:
                effective = -2
            elif is_recently_ended:
                effective = -1
            else:
                effective = time_to_start

            if best_time is None or effective < best_time:
                best_time = effective
                info.time_to_start_sec = time_to_start
                info.is_in_progress = is_in_progress
                best_market = info

        if best_market:
            results[asset_key] = best_market

    return results

