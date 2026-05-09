"""
scalper/universal_scanner.py — Universal Market Scanner for V10.

Scans ALL active Polymarket markets (not just crypto 5min) to find
contracts with active price momentum. Uses the Gamma API for discovery
and the CLOB API for granular price history confirmation.

Key Insight: Gamma API already provides oneHourPriceChange, oneDayPriceChange
per market — we use these for initial screening, then confirm with CLOB
price-history for the top candidates.
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from scalper.config import GAMMA_API_BASE

logger = logging.getLogger("polybot.v10.scanner")

# ── Configuration ──────────────────────────────────────────────
SCAN_PAGES = 5                 # Fetch 5 pages × 100 = 500 markets per scan
PAGE_SIZE = 100                # Gamma API max per request
REQUEST_TIMEOUT = 12           # seconds
CLOB_BASE = "https://clob.polymarket.com"

# Filters
MIN_LIQUIDITY = 500            # Minimum $500 liquidity
MIN_ENTRY_PRICE = 0.15         # Avoid nearly dead contracts
MAX_ENTRY_PRICE = 0.70         # Avoid nearly resolved contracts
MAX_SPREAD = 0.05              # Max 5 cents spread (must have exit liquidity)
MIN_VOLUME_24H = 1000          # Minimum $1000 in 24h volume

# Momentum thresholds
MIN_1H_PRICE_CHANGE = 0.03    # +$0.03 in last hour
MIN_1D_PRICE_CHANGE = 0.05    # +$0.05 in last day

# Exclusion patterns (already covered by V1-V9)
EXCLUDE_SLUG_PATTERNS = [
    "-updown-5m-",
    "-updown-15m-",
]

# ── Cache ──────────────────────────────────────────────────────
_market_cache: dict = {}
_cache_timestamp: float = 0
CACHE_TTL_SEC = 240            # Cache markets for 4 minutes


def _is_excluded(market: dict) -> bool:
    """Check if market should be excluded (crypto 5m/15m already covered)."""
    slug = market.get("slug", "")
    for pattern in EXCLUDE_SLUG_PATTERNS:
        if pattern in slug:
            return True
    return False


def _parse_float(val, default=0.0) -> float:
    """Safely parse a float from API response."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _extract_market_info(raw: dict) -> dict | None:
    """Extract relevant fields from a raw Gamma API market object."""
    if _is_excluded(raw):
        return None

    # Parse outcome prices
    outcomes_raw = raw.get("outcomePrices", '["0.5", "0.5"]')
    if isinstance(outcomes_raw, str):
        try:
            outcome_prices = json.loads(outcomes_raw)
        except json.JSONDecodeError:
            outcome_prices = ["0.5", "0.5"]
    else:
        outcome_prices = outcomes_raw or ["0.5", "0.5"]

    yes_price = _parse_float(outcome_prices[0] if outcome_prices else "0.5")
    no_price = _parse_float(outcome_prices[1] if len(outcome_prices) > 1 else "0.5")

    # Parse CLOB token IDs
    clob_raw = raw.get("clobTokenIds", "[]")
    if isinstance(clob_raw, str):
        try:
            clob_ids = json.loads(clob_raw)
        except json.JSONDecodeError:
            clob_ids = []
    else:
        clob_ids = clob_raw or []

    # Parse end date
    end_date_str = raw.get("endDate", "")
    try:
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        days_to_resolution = (end_date - datetime.now(timezone.utc)).days
    except (ValueError, TypeError):
        end_date = None
        days_to_resolution = 999

    return {
        "id": raw.get("id", ""),
        "question": raw.get("question", "Unknown"),
        "slug": raw.get("slug", ""),
        "yes_price": yes_price,
        "no_price": no_price,
        "best_bid": _parse_float(raw.get("bestBid")),
        "best_ask": _parse_float(raw.get("bestAsk")),
        "spread": _parse_float(raw.get("spread")),
        "liquidity": _parse_float(raw.get("liquidityNum")),
        "volume_24h": _parse_float(raw.get("volume24hr")),
        "volume_1w": _parse_float(raw.get("volume1wk")),
        "price_change_1h": _parse_float(raw.get("oneHourPriceChange")),
        "price_change_1d": _parse_float(raw.get("oneDayPriceChange")),
        "price_change_1w": _parse_float(raw.get("oneWeekPriceChange")),
        "end_date": end_date,
        "days_to_resolution": days_to_resolution,
        "clob_token_ids": clob_ids,
        "yes_token_id": clob_ids[0] if len(clob_ids) > 0 else "",
        "no_token_id": clob_ids[1] if len(clob_ids) > 1 else "",
        "accepting_orders": raw.get("acceptingOrders", False),
        "gamma_id": raw.get("id", ""),
        "event_title": "",
    }


def scan_all_markets() -> list[dict]:
    """
    Fetch active markets from Gamma API with pagination.
    Returns list of parsed market info dicts.
    
    Uses cache to avoid hammering the API on every cycle.
    """
    global _market_cache, _cache_timestamp

    now = time.time()
    if _market_cache and (now - _cache_timestamp) < CACHE_TTL_SEC:
        logger.debug("Using cached markets (%d)", len(_market_cache))
        return list(_market_cache.values())

    all_markets = []
    for page in range(SCAN_PAGES):
        offset = page * PAGE_SIZE
        try:
            resp = requests.get(
                f"{GAMMA_API_BASE}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break

            for raw in batch:
                info = _extract_market_info(raw)
                if info:
                    all_markets.append(info)

            if len(batch) < PAGE_SIZE:
                break  # No more pages

        except requests.RequestException as exc:
            logger.warning("Gamma API page %d failed: %s", page, exc)
            break

        # Small delay between pages to avoid rate limiting
        if page < SCAN_PAGES - 1:
            time.sleep(0.3)

    # Update cache
    _market_cache = {m["id"]: m for m in all_markets}
    _cache_timestamp = now

    logger.info("Scanned %d active markets (excl. crypto 5m/15m)", len(all_markets))
    return all_markets


def filter_tradeable(markets: list[dict]) -> list[dict]:
    """
    Apply basic tradeability filters:
    - Minimum liquidity
    - Price in range
    - Spread not too wide
    - Accepting orders
    - Minimum volume
    """
    filtered = []
    for m in markets:
        if not m.get("accepting_orders"):
            continue
        if m["liquidity"] < MIN_LIQUIDITY:
            continue
        if m["volume_24h"] < MIN_VOLUME_24H:
            continue

        # Check both YES and NO prices — we can trade either side
        yes_ok = MIN_ENTRY_PRICE <= m["yes_price"] <= MAX_ENTRY_PRICE
        no_ok = MIN_ENTRY_PRICE <= m["no_price"] <= MAX_ENTRY_PRICE
        if not (yes_ok or no_ok):
            continue

        if m["spread"] > MAX_SPREAD:
            continue

        # Must have at least 1 day to resolution
        if m["days_to_resolution"] < 1:
            continue

        filtered.append(m)

    return filtered


def detect_momentum(markets: list[dict]) -> list[dict]:
    """
    Score markets by momentum and return those with active upward trends.
    
    We look for the YES side trending up (price_change > 0) OR
    the NO side trending up (price_change < 0 for YES = NO going up).
    
    Returns markets with momentum_score > 0, sorted by score descending.
    """
    scored = []

    for m in markets:
        pchg_1h = m["price_change_1h"]
        pchg_1d = m["price_change_1d"]
        pchg_1w = m["price_change_1w"]

        # ── YES side momentum ─────────────────────────────────
        yes_momentum = 0.0
        if pchg_1h >= MIN_1H_PRICE_CHANGE:
            yes_momentum += pchg_1h * 3.0      # 1h is strongest signal
        if pchg_1d >= MIN_1D_PRICE_CHANGE:
            yes_momentum += pchg_1d * 1.5      # 1d confirms trend
        if pchg_1w > 0:
            yes_momentum += pchg_1w * 0.5      # 1w adds context

        # ── NO side momentum (YES price dropping = NO rising) ─
        no_momentum = 0.0
        if pchg_1h <= -MIN_1H_PRICE_CHANGE:
            no_momentum += abs(pchg_1h) * 3.0
        if pchg_1d <= -MIN_1D_PRICE_CHANGE:
            no_momentum += abs(pchg_1d) * 1.5
        if pchg_1w < 0:
            no_momentum += abs(pchg_1w) * 0.5

        # Choose the side with stronger momentum
        if yes_momentum >= no_momentum and yes_momentum > 0:
            side = "YES"
            entry_price = m["best_ask"] if m["best_ask"] > 0 else m["yes_price"]
            token_id = m["yes_token_id"]
            momentum_score = round(yes_momentum, 4)
        elif no_momentum > 0:
            side = "NO"
            entry_price = (1.0 - m["best_bid"]) if m["best_bid"] > 0 else m["no_price"]
            token_id = m["no_token_id"]
            momentum_score = round(no_momentum, 4)
        else:
            continue  # No momentum

        # Verify entry price is in range
        if not (MIN_ENTRY_PRICE <= entry_price <= MAX_ENTRY_PRICE):
            continue

        # Liquidity bonus (more liquid = more reliable)
        liq_bonus = min(m["liquidity"] / 10000, 0.5)  # Cap at 0.5
        vol_bonus = min(m["volume_24h"] / 50000, 0.3)  # Cap at 0.3

        final_score = round(momentum_score + liq_bonus + vol_bonus, 4)

        m_scored = {
            **m,
            "trade_side": side,
            "trade_token_id": token_id,
            "entry_price": round(entry_price, 4),
            "momentum_score": momentum_score,
            "final_score": final_score,
            "momentum_1h": pchg_1h,
            "momentum_1d": pchg_1d,
            "momentum_1w": pchg_1w,
        }
        scored.append(m_scored)

    # Sort by final score descending
    scored.sort(key=lambda x: x["final_score"], reverse=True)
    return scored


def fetch_price_history(token_id: str, interval: str = "1d", fidelity: int = 10) -> list[dict]:
    """
    Fetch price history from CLOB API for momentum confirmation.
    Returns list of {t: timestamp, p: price} dicts.
    """
    try:
        resp = requests.get(
            f"{CLOB_BASE}/prices-history",
            params={
                "market": token_id,
                "interval": interval,
                "fidelity": fidelity,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("history", [])
    except (requests.RequestException, json.JSONDecodeError) as exc:
        logger.warning("CLOB price history failed for %s: %s", token_id[:20], exc)
        return []


def confirm_momentum_with_history(market: dict) -> dict | None:
    """
    For a market that passed momentum screening, fetch granular price
    history to confirm the trend is real (not a stale API field).
    
    Returns the market dict with added confirmation data, or None if
    the trend doesn't confirm.
    """
    token_id = market.get("trade_token_id", "")
    if not token_id:
        return None

    history = fetch_price_history(token_id, interval="1d", fidelity=20)
    if len(history) < 5:
        # Not enough data — trust the Gamma API fields
        market["confirmed"] = False
        market["confirm_reason"] = "insufficient_history"
        return market

    prices = [h["p"] for h in history]
    latest_price = prices[-1]
    price_5_ago = prices[-5] if len(prices) >= 5 else prices[0]
    price_10_ago = prices[-10] if len(prices) >= 10 else prices[0]

    recent_trend = latest_price - price_5_ago
    medium_trend = latest_price - price_10_ago

    # Confirm: recent price should be moving in the expected direction
    if market["trade_side"] == "YES":
        confirmed = recent_trend > 0.01
    else:
        confirmed = recent_trend < -0.01

    market["confirmed"] = confirmed
    market["confirm_recent_trend"] = round(recent_trend, 4)
    market["confirm_medium_trend"] = round(medium_trend, 4)
    market["confirm_latest_price"] = latest_price

    return market


def get_trending_markets(max_results: int = 20) -> list[dict]:
    """
    Full pipeline: scan → filter → momentum detect → rank.
    Returns top trending markets ready for trading signals.
    """
    # Step 1: Scan
    all_markets = scan_all_markets()
    print(f"  [V10-SCAN] {len(all_markets)} active markets scanned")

    # Step 2: Filter
    tradeable = filter_tradeable(all_markets)
    print(f"  [V10-SCAN] {len(tradeable)} pass tradeability filters (liq>${MIN_LIQUIDITY}, vol>${MIN_VOLUME_24H})")

    # Step 3: Momentum detection
    trending = detect_momentum(tradeable)
    print(f"  [V10-SCAN] {len(trending)} have active momentum")

    # Step 4: Confirm top candidates with price history
    confirmed = []
    for m in trending[:max_results]:
        result = confirm_momentum_with_history(m)
        if result:
            confirmed.append(result)
        time.sleep(0.2)  # Rate limit CLOB requests

    confirmed_count = sum(1 for c in confirmed if c.get("confirmed"))
    print(f"  [V10-SCAN] {confirmed_count}/{len(confirmed)} confirmed by price history")

    return confirmed
