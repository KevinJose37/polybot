"""
test_rest_vs_ws.py — Diagnostic: REST orderbook snapshot vs WS cached data.

Compares the CLOB REST endpoint (real-time) against the WS buffer
to show how stale the WS data is, and how fast REST responds.

Usage:
    python test_rest_vs_ws.py

No authentication required — both endpoints are public.
"""

import json
import sys
import time
from datetime import datetime, timezone

import requests

# ── Config ──────────────────────────────────────────────────────
CLOB_REST_BOOK = "https://clob.polymarket.com/book"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

ASSETS = {
    "BTC": "btc-updown-5m",
    "ETH": "eth-updown-5m",
    "SOL": "sol-updown-5m",
    "XRP": "xrp-updown-5m",
}


def get_current_5m_slot():
    """Get the current 5-minute slot timestamp."""
    now = int(time.time())
    return (now // 300) * 300


def discover_active_tokens() -> dict:
    """
    Find current active market token_ids via Gamma API.
    Returns {asset: {up_token_id, down_token_id, slug, ...}}
    """
    slot = get_current_5m_slot()
    # Try current slot and previous slot (for in-progress markets)
    slots_to_try = [slot - 300, slot, slot + 300]
    results = {}

    for asset_key, prefix in ASSETS.items():
        for ts in slots_to_try:
            slug = f"{prefix}-{ts}"
            try:
                resp = requests.get(
                    f"{GAMMA_API_BASE}/events",
                    params={"slug": slug},
                    timeout=5,
                )
                events = resp.json()
                if not events:
                    continue

                event = events[0]
                market = event.get("markets", [{}])[0]

                if market.get("closed", False):
                    continue

                # Parse token IDs
                token_ids_raw = market.get("clobTokenIds", "[]")
                if isinstance(token_ids_raw, str):
                    token_ids = json.loads(token_ids_raw)
                else:
                    token_ids = token_ids_raw or []

                up_token = token_ids[0] if len(token_ids) > 0 else ""
                dn_token = token_ids[1] if len(token_ids) > 1 else ""

                # Parse prices from Gamma
                outcome_prices_raw = market.get("outcomePrices", '["0.5","0.5"]')
                if isinstance(outcome_prices_raw, str):
                    outcome_prices = json.loads(outcome_prices_raw)
                else:
                    outcome_prices = outcome_prices_raw
                gamma_up = float(outcome_prices[0])
                gamma_dn = float(outcome_prices[1])

                if up_token:
                    results[asset_key] = {
                        "slug": slug,
                        "up_token_id": up_token,
                        "down_token_id": dn_token,
                        "gamma_up_price": gamma_up,
                        "gamma_dn_price": gamma_dn,
                        "gamma_best_bid": float(market.get("bestBid", 0) or 0),
                        "gamma_best_ask": float(market.get("bestAsk", 0) or 0),
                    }
                    break  # Found active market for this asset
            except Exception:
                continue

    return results


def fetch_rest_orderbook(token_id: str) -> dict | None:
    """
    Fetch orderbook from CLOB REST endpoint.
    Returns {bids: [...], asks: [...], latency_ms: float}
    """
    t0 = time.perf_counter()
    try:
        resp = requests.get(
            CLOB_REST_BOOK,
            params={"token_id": token_id},
            timeout=5,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        data = resp.json()

        bids = []
        for b in data.get("bids", []):
            bids.append({
                "price": float(b.get("price", 0)),
                "size": float(b.get("size", 0)),
            })
        bids.sort(key=lambda x: x["price"], reverse=True)

        asks = []
        for a in data.get("asks", []):
            asks.append({
                "price": float(a.get("price", 0)),
                "size": float(a.get("size", 0)),
            })
        asks.sort(key=lambda x: x["price"])

        return {
            "bids": bids,
            "asks": asks,
            "latency_ms": round(elapsed_ms, 1),
            "raw_keys": list(data.keys()),
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"error": str(exc), "latency_ms": round(elapsed_ms, 1)}


def format_book_side(entries, label, max_levels=5):
    """Format bid or ask entries for display."""
    lines = []
    total_size = sum(e["size"] for e in entries)
    for i, e in enumerate(entries[:max_levels]):
        bar = "#" * min(int(e["size"] / 5), 40)
        lines.append(f"    ${e['price']:.4f}  {e['size']:>10.1f}  {bar}")
    if len(entries) > max_levels:
        remaining = len(entries) - max_levels
        lines.append(f"    ... +{remaining} more levels")
    lines.append(f"    Total depth: {total_size:.1f} shares across {len(entries)} levels")
    return "\n".join(lines)


def main():
    print()
    print("=" * 72)
    print("  REST vs WS Orderbook Diagnostic")
    print("  Testing CLOB REST: GET /book?token_id=...")
    print("=" * 72)

    # Step 1: Discover active markets
    print("\n  [1] Discovering active 5m markets via Gamma API...")
    t0 = time.perf_counter()
    markets = discover_active_tokens()
    discovery_ms = (time.perf_counter() - t0) * 1000
    print(f"      Found {len(markets)} active markets in {discovery_ms:.0f}ms")

    if not markets:
        print("\n  ERROR: No active markets found. Markets may be between windows.")
        print("  Try again in a few seconds.\n")
        return

    for asset, info in markets.items():
        print(f"      {asset}: {info['slug']}")

    # Step 2: Fetch REST orderbook for each token
    print(f"\n  [2] Fetching REST orderbooks from CLOB...")
    print("-" * 72)

    all_latencies = []

    for asset, info in markets.items():
        print(f"\n  === {asset} ===")
        print(f"  Slug: {info['slug']}")
        print(f"  Gamma REST prices: UP=${info['gamma_up_price']:.4f}  DOWN=${info['gamma_dn_price']:.4f}")
        print(f"  Gamma bestBid={info['gamma_best_bid']:.4f}  bestAsk={info['gamma_best_ask']:.4f}")

        # Fetch UP token book
        up_token = info["up_token_id"]
        dn_token = info["down_token_id"]

        print(f"\n  -- UP Token ({up_token[:20]}...) --")
        up_book = fetch_rest_orderbook(up_token)
        if "error" in up_book:
            print(f"    ERROR: {up_book['error']} ({up_book['latency_ms']:.0f}ms)")
        else:
            all_latencies.append(up_book["latency_ms"])
            print(f"    REST latency: {up_book['latency_ms']:.0f}ms")
            print(f"    Response keys: {up_book['raw_keys']}")

            if up_book["asks"]:
                best_ask = up_book["asks"][0]
                print(f"    Best ASK (buy price): ${best_ask['price']:.4f} ({best_ask['size']:.1f} shares)")
            else:
                print(f"    Best ASK: NONE (no sellers!)")

            if up_book["bids"]:
                best_bid = up_book["bids"][0]
                print(f"    Best BID (sell price): ${best_bid['price']:.4f} ({best_bid['size']:.1f} shares)")
            else:
                print(f"    Best BID: NONE (no buyers!)")

            if up_book["bids"] and up_book["asks"]:
                spread = up_book["asks"][0]["price"] - up_book["bids"][0]["price"]
                mid = (up_book["asks"][0]["price"] + up_book["bids"][0]["price"]) / 2
                spread_pct = spread / mid * 100 if mid > 0 else 0
                print(f"    Spread: ${spread:.4f} ({spread_pct:.1f}%)")

            print(f"\n    ASKS (sell side — you'd buy at these prices):")
            print(format_book_side(up_book["asks"], "ASK"))
            print(f"\n    BIDS (buy side — you'd sell at these prices):")
            print(format_book_side(up_book["bids"], "BID"))

        # Fetch DOWN token book
        if dn_token:
            print(f"\n  -- DOWN Token ({dn_token[:20]}...) --")
            dn_book = fetch_rest_orderbook(dn_token)
            if "error" in dn_book:
                print(f"    ERROR: {dn_book['error']} ({dn_book['latency_ms']:.0f}ms)")
            else:
                all_latencies.append(dn_book["latency_ms"])
                print(f"    REST latency: {dn_book['latency_ms']:.0f}ms")

                if dn_book["asks"]:
                    best_ask = dn_book["asks"][0]
                    print(f"    Best ASK (buy price): ${best_ask['price']:.4f} ({best_ask['size']:.1f} shares)")
                else:
                    print(f"    Best ASK: NONE")

                if dn_book["bids"]:
                    best_bid = dn_book["bids"][0]
                    print(f"    Best BID (sell price): ${best_bid['price']:.4f} ({best_bid['size']:.1f} shares)")
                else:
                    print(f"    Best BID: NONE")

                if dn_book["bids"] and dn_book["asks"]:
                    spread = dn_book["asks"][0]["price"] - dn_book["bids"][0]["price"]
                    mid = (dn_book["asks"][0]["price"] + dn_book["bids"][0]["price"]) / 2
                    spread_pct = spread / mid * 100 if mid > 0 else 0
                    print(f"    Spread: ${spread:.4f} ({spread_pct:.1f}%)")

                print(f"\n    ASKS:")
                print(format_book_side(dn_book["asks"], "ASK"))
                print(f"\n    BIDS:")
                print(format_book_side(dn_book["bids"], "BID"))

        # Cross-reference: Gamma price vs REST book
        if not up_book.get("error") and up_book["asks"] and up_book["bids"]:
            rest_mid = (up_book["asks"][0]["price"] + up_book["bids"][0]["price"]) / 2
            gamma_price = info["gamma_up_price"]
            drift = abs(rest_mid - gamma_price)
            drift_pct = drift / gamma_price * 100 if gamma_price > 0 else 0
            print(f"\n  >> PRICE DRIFT: Gamma=${gamma_price:.4f} vs REST mid=${rest_mid:.4f} (delta={drift_pct:.1f}%)")

    # Summary
    print(f"\n{'=' * 72}")
    print(f"  SUMMARY")
    print(f"{'=' * 72}")
    if all_latencies:
        avg_lat = sum(all_latencies) / len(all_latencies)
        min_lat = min(all_latencies)
        max_lat = max(all_latencies)
        print(f"  REST orderbook latency:")
        print(f"    Avg: {avg_lat:.0f}ms | Min: {min_lat:.0f}ms | Max: {max_lat:.0f}ms")
        print(f"    Calls made: {len(all_latencies)}")
    print(f"  Gamma discovery: {discovery_ms:.0f}ms")
    print()
    print(f"  CONCLUSION:")
    if all_latencies:
        avg = sum(all_latencies) / len(all_latencies)
        if avg < 200:
            print(f"    REST is FAST ({avg:.0f}ms avg). Safe to use as pre-execution snapshot.")
            print(f"    vs your WS at 13.7s between messages and 107s stale!")
        elif avg < 500:
            print(f"    REST is OK ({avg:.0f}ms avg). Usable for pre-execution but adds latency.")
        else:
            print(f"    REST is SLOW ({avg:.0f}ms avg). May cause fill issues if book moves fast.")
    print()


if __name__ == "__main__":
    main()
