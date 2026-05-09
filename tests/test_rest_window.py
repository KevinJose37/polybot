"""
test_rest_window.py — Capture REST orderbook snapshots across a full 5-min window.

Waits for the next 5-minute boundary, then takes snapshots at:
  0s, 15s, 30s, 60s, 120s, 180s, 240s, 280s

Shows how the book evolves from open to close.
"""

import json
import sys
import time
from datetime import datetime, timezone

import requests

CLOB_REST_BOOK = "https://clob.polymarket.com/book"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

ASSETS = {
    "BTC": "btc-updown-5m",
    "ETH": "eth-updown-5m",
    "SOL": "sol-updown-5m",
    "XRP": "xrp-updown-5m",
}

# Snapshot times (seconds after window open)
SNAPSHOT_TIMES = [0, 15, 30, 60, 120, 180, 240, 280]


def get_next_5m_boundary():
    """Get the unix timestamp of the next 5-minute boundary."""
    now = int(time.time())
    current_slot = (now // 300) * 300
    next_slot = current_slot + 300
    return next_slot


def discover_tokens_for_slot(slot_ts):
    """Find token IDs for a specific slot timestamp."""
    results = {}
    for asset_key, prefix in ASSETS.items():
        slug = f"{prefix}-{slot_ts}"
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

            token_ids_raw = market.get("clobTokenIds", "[]")
            if isinstance(token_ids_raw, str):
                token_ids = json.loads(token_ids_raw)
            else:
                token_ids = token_ids_raw or []

            up_token = token_ids[0] if len(token_ids) > 0 else ""
            dn_token = token_ids[1] if len(token_ids) > 1 else ""

            if up_token:
                results[asset_key] = {
                    "slug": slug,
                    "up_token_id": up_token,
                    "down_token_id": dn_token,
                }
        except Exception:
            continue
    return results


def fetch_book_snapshot(token_id):
    """Fetch orderbook and return condensed summary."""
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
            p, s = float(b.get("price", 0)), float(b.get("size", 0))
            if s > 0:
                bids.append((p, s))
        bids.sort(key=lambda x: x[0], reverse=True)

        asks = []
        for a in data.get("asks", []):
            p, s = float(a.get("price", 0)), float(a.get("size", 0))
            if s > 0:
                asks.append((p, s))
        asks.sort(key=lambda x: x[0])

        best_bid = bids[0][0] if bids else 0
        best_bid_sz = bids[0][1] if bids else 0
        best_ask = asks[0][0] if asks else 0
        best_ask_sz = asks[0][1] if asks else 0
        bid_depth = sum(s for _, s in bids)
        ask_depth = sum(s for _, s in asks)
        bid_levels = len(bids)
        ask_levels = len(asks)

        spread = best_ask - best_bid if (best_bid > 0 and best_ask > 0) else 0
        mid = (best_bid + best_ask) / 2 if (best_bid > 0 and best_ask > 0) else (best_bid or best_ask)

        return {
            "best_bid": best_bid,
            "best_bid_sz": best_bid_sz,
            "best_ask": best_ask,
            "best_ask_sz": best_ask_sz,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "bid_levels": bid_levels,
            "ask_levels": ask_levels,
            "spread": spread,
            "mid": mid,
            "latency_ms": round(elapsed_ms, 0),
            # Top 5 bids/asks for detailed view
            "top_bids": bids[:5],
            "top_asks": asks[:5],
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"error": str(exc), "latency_ms": round(elapsed_ms, 0)}


def format_snapshot_line(asset, side, snap, elapsed_s):
    """Format a single snapshot as a compact line."""
    if "error" in snap:
        return f"  {elapsed_s:>4}s | {asset:>3} {side:>4} | ERROR: {snap['error']}"

    bid_str = f"${snap['best_bid']:.2f}" if snap['best_bid'] > 0 else " NONE"
    ask_str = f"${snap['best_ask']:.2f}" if snap['best_ask'] > 0 else " NONE"
    spread_str = f"${snap['spread']:.2f}" if snap['spread'] > 0 else "  N/A"

    return (
        f"  {elapsed_s:>4}s | {asset:>3} {side:>4} | "
        f"bid={bid_str}({snap['best_bid_sz']:>9.0f}) "
        f"ask={ask_str}({snap['best_ask_sz']:>9.0f}) "
        f"spread={spread_str} "
        f"depth: {snap['bid_levels']}b/{snap['ask_levels']}a "
        f"| {snap['latency_ms']:.0f}ms"
    )


def main():
    next_slot = get_next_5m_boundary()
    wait_secs = next_slot - time.time()

    print()
    print("=" * 80)
    print("  REST ORDERBOOK: Full 5-Minute Window Capture")
    print("=" * 80)
    print(f"  Next 5-min window starts at: {datetime.fromtimestamp(next_slot, tz=timezone.utc).strftime('%H:%M:%S UTC')}")
    print(f"  Snapshots at: {SNAPSHOT_TIMES} seconds into window")

    # Pre-discover tokens for the upcoming slot
    print(f"\n  Discovering tokens for slot {next_slot}...")
    tokens = discover_tokens_for_slot(next_slot)

    if not tokens:
        # Market might not be created yet, try again closer to start
        print("  Market not found yet. Will retry at window open...")

    # Wait for window to open
    if wait_secs > 0:
        print(f"\n  Waiting {wait_secs:.0f}s for window to open...")
        # Countdown
        while True:
            remaining = next_slot - time.time()
            if remaining <= 0:
                break
            if remaining > 10:
                print(f"    ... {remaining:.0f}s remaining", end="\r")
                time.sleep(5)
            else:
                print(f"    ... {remaining:.1f}s remaining", end="\r")
                time.sleep(0.5)
        print()

    # Re-discover if needed
    if not tokens:
        print("  Re-discovering tokens...")
        tokens = discover_tokens_for_slot(next_slot)
        if not tokens:
            print("  ERROR: Still no markets found. Exiting.")
            return

    print(f"  Found {len(tokens)} markets:")
    for asset, info in tokens.items():
        print(f"    {asset}: {info['slug']}")

    # Collect all snapshots
    all_snapshots = []  # list of (elapsed_s, asset, side, snapshot_data)

    print(f"\n  {'='*76}")
    print(f"  {'Time':>5} | {'Asset':>3} {'Side':>4} | {'Best Bid':>20} {'Best Ask':>20} {'Spread':>8} {'Levels':>10} | {'ms':>4}")
    print(f"  {'-'*76}")

    window_start = next_slot

    for target_elapsed in SNAPSHOT_TIMES:
        # Wait until we reach this snapshot time
        target_time = window_start + target_elapsed
        now = time.time()
        if target_time > now:
            time.sleep(target_time - now)

        actual_elapsed = time.time() - window_start

        # Fetch all assets at this point
        for asset, info in tokens.items():
            # Fetch UP token
            up_snap = fetch_book_snapshot(info["up_token_id"])
            print(format_snapshot_line(asset, "UP", up_snap, int(actual_elapsed)))
            all_snapshots.append((int(actual_elapsed), asset, "UP", up_snap))

            # Fetch DOWN token
            dn_snap = fetch_book_snapshot(info["down_token_id"])
            print(format_snapshot_line(asset, "DN", dn_snap, int(actual_elapsed)))
            all_snapshots.append((int(actual_elapsed), asset, "DN", dn_snap))

        print(f"  {'-'*76}")

    # Save raw data for the artifact
    output_file = "rest_window_results.json"
    serializable = []
    for elapsed, asset, side, snap in all_snapshots:
        entry = {"elapsed_s": elapsed, "asset": asset, "side": side}
        for k, v in snap.items():
            if k in ("top_bids", "top_asks"):
                entry[k] = [{"price": p, "size": s} for p, s in v]
            else:
                entry[k] = v
        serializable.append(entry)

    with open(output_file, "w") as f:
        json.dump(serializable, f, indent=2)

    # Print summary evolution
    print(f"\n  {'='*80}")
    print(f"  EVOLUTION SUMMARY (BTC UP as example)")
    print(f"  {'='*80}")

    btc_snaps = [(e, s) for e, a, side, s in all_snapshots if a == "BTC" and side == "UP" and "error" not in s]
    if btc_snaps:
        print(f"  {'Time':>5} | {'Best Bid':>10} | {'Best Ask':>10} | {'Spread':>8} | {'Bid Depth':>12} | {'Ask Depth':>12} | {'Bilateral?':>10}")
        print(f"  {'-'*80}")
        for elapsed, snap in btc_snaps:
            bilateral = "YES" if snap["best_bid"] > 0.01 and snap["best_ask"] < 0.99 and snap["best_ask"] > 0.01 else "NO"
            print(
                f"  {elapsed:>4}s | ${snap['best_bid']:.4f}  | ${snap['best_ask']:.4f}  | "
                f"${snap['spread']:.4f}  | {snap['bid_depth']:>10.0f}  | {snap['ask_depth']:>10.0f}  | "
                f"{'>> YES <<' if bilateral == 'YES' else '   no   '}"
            )

    print(f"\n  Raw data saved to: {output_file}")
    print(f"  Done! Window captured from 0s to {SNAPSHOT_TIMES[-1]}s")
    print()


if __name__ == "__main__":
    main()
