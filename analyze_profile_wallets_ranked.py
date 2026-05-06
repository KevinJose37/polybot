import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests


RANKED_FILE = Path("profile_wallets_ranked.json")
DETAILED_OUT = Path("profile_wallets_ranked_detailed.json")

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
DATA_TRADES_URL = "https://data-api.polymarket.com/trades"


def duration_from_slug(slug: str) -> str | None:
    m = re.search(r"-(5|15)m-", slug or "")
    return f"{m.group(1)}m" if m else None


def bucket_entry(p: float) -> str:
    if p <= 0.2:
        return "<=0.20"
    if p <= 0.4:
        return "0.21-0.40"
    if p <= 0.6:
        return "0.41-0.60"
    if p <= 0.8:
        return "0.61-0.80"
    return ">0.80"


def asset_key_from_trade(trade: dict) -> str:
    title = str(trade.get("title", "") or "").lower()
    slug = str(trade.get("slug", "") or "").lower()
    s = f"{title} {slug}"
    if "bitcoin" in s or re.search(r"\bbtc\b", s):
        return "BTC"
    if "ethereum" in s or re.search(r"\beth\b", s):
        return "ETH"
    if "solana" in s or re.search(r"\bsol\b", s):
        return "SOL"
    if "xrp" in s:
        return "XRP"
    # fallback by slug tokens
    for k in ["btc", "eth", "sol", "xrp"]:
        if k in slug:
            return k.upper()
    return "UNK"


def resolve_slug_prices(slug: str, gamma_cache: dict) -> tuple[bool, float, float] | None:
    """
    Returns (closed, up_price, down_price) from Gamma for a given event slug.
    Cache is shared across wallets to reduce requests.
    """
    if slug in gamma_cache:
        return gamma_cache[slug]

    try:
        r = requests.get(GAMMA_EVENTS_URL, params={"slug": slug}, timeout=12)
        r.raise_for_status()
        events = r.json()
        if not events or not events[0].get("markets"):
            gamma_cache[slug] = None
            return None

        market = events[0]["markets"][0]
        closed = bool(market.get("closed", False))
        op = market.get("outcomePrices", ["0.5", "0.5"])
        if isinstance(op, str):
            op = json.loads(op)
        up_price = float(op[0])
        down_price = float(op[1])
        gamma_cache[slug] = (closed, up_price, down_price)
        return gamma_cache[slug]
    except Exception:
        gamma_cache[slug] = None
        return None


def analyze_wallet(wallet: str, limit: int = 600, side_threshold: float = 0.9) -> dict | None:
    url = DATA_TRADES_URL
    resp = requests.get(url, params={"user": wallet, "limit": limit}, timeout=25)
    resp.raise_for_status()
    trades = resp.json()
    if not isinstance(trades, list) or not trades:
        return None

    # Dedupe by transactionHash (copy bot uses this)
    seen = set()
    uniq = []
    for t in trades:
        h = t.get("transactionHash")
        if h:
            if h in seen:
                continue
            seen.add(h)
        uniq.append(t)

    trades = uniq

    counts_asset = Counter()
    counts_side = Counter()
    counts_dur = Counter()

    # Performance $1 stake analysis (hold to resolution)
    perf_rows = []  # (dur, asset, side, entry_price, won, pnl)
    unresolved = 0

    gamma_cache: dict = {}
    # NOTE: this cache is passed in/out in the caller to share across wallets;
    # we keep a local ref here but caller will reuse a global cache.

    for t in trades:
        slug = str(t.get("slug", "") or "")
        dur = duration_from_slug(slug)
        if dur not in ("5m", "15m"):
            continue

        side = str(t.get("outcome", "") or "").upper()
        if side not in ("UP", "DOWN"):
            continue

        entry_price = float(t.get("price", 0) or 0)
        if entry_price <= 0 or entry_price >= 1:
            continue

        asset = asset_key_from_trade(t)
        counts_asset[asset] += 1
        counts_side[side] += 1
        counts_dur[dur] += 1

        resolved = resolve_slug_prices(slug, gamma_cache)
        if not resolved:
            unresolved += 1
            continue

        closed, up_price, down_price = resolved
        if not closed:
            unresolved += 1
            continue

        won = (up_price > side_threshold) if side == "UP" else (down_price > side_threshold)
        pnl = (1.0 / entry_price - 1.0) if won else -1.0
        perf_rows.append((dur, asset, side, entry_price, won, pnl))

    resolved_n = len(perf_rows)
    if resolved_n == 0:
        return {
            "wallet": wallet,
            "resolved": 0,
            "unresolved_or_unknown": unresolved,
            "counts_asset": dict(counts_asset),
            "counts_side": dict(counts_side),
            "counts_dur": dict(counts_dur),
        }

    wins = sum(1 for r in perf_rows if r[4])
    losses = resolved_n - wins
    wr = wins / resolved_n * 100.0
    pnl_total = sum(r[5] for r in perf_rows)

    pos_sum = sum(r[5] for r in perf_rows if r[5] > 0)
    neg_sum = abs(sum(r[5] for r in perf_rows if r[5] < 0))
    profit_factor = pos_sum / neg_sum if neg_sum > 0 else 0.0

    # equity curve and max drawdown (on per-trade pnl series)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    # For determinism, we don't have per-trade timestamps here; that's ok for relative.
    for r in perf_rows:
        equity += r[5]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    # Aggregations
    def agg_by(idx: int) -> dict:
        m = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
        for r in perf_rows:
            k = r[idx]
            m[k]["n"] += 1
            if r[4]:
                m[k]["w"] += 1
            m[k]["pnl"] += r[5]
        return {
            k: {"n": v["n"], "wr": (v["w"] / v["n"] * 100.0 if v["n"] else 0.0), "pnl": v["pnl"]}
            for k, v in m.items()
        }

    agg_duration = agg_by(0)  # dur
    agg_asset = agg_by(1)  # asset
    agg_side = agg_by(2)  # side
    agg_price = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
    for r in perf_rows:
        b = bucket_entry(r[3])
        agg_price[b]["n"] += 1
        if r[4]:
            agg_price[b]["w"] += 1
        agg_price[b]["pnl"] += r[5]
    agg_price_bucket = {
        k: {"n": v["n"], "wr": (v["w"] / v["n"] * 100.0 if v["n"] else 0.0), "pnl": v["pnl"]}
        for k, v in agg_price.items()
    }

    # Burst pattern: 3+ trades on same slug within 30s
    by_slug_ts = defaultdict(list)
    for t in trades:
        slug = str(t.get("slug", "") or "")
        dur = duration_from_slug(slug)
        if dur not in ("5m", "15m"):
            continue
        ts = t.get("timestamp", 0)
        try:
            ts = int(float(ts))
        except Exception:
            continue
        if ts > 1_000_000_000_000:
            ts //= 1000
        size = float(t.get("size", 0) or 0)
        if ts and size > 0:
            by_slug_ts[slug].append((ts, size))

    bursts = []
    for slug, arr in by_slug_ts.items():
        arr.sort(key=lambda x: x[0])
        for i in range(len(arr)):
            t0 = arr[i][0]
            total = arr[i][1]
            cnt = 1
            j = i + 1
            while j < len(arr) and arr[j][0] - t0 <= 30:
                total += arr[j][1]
                cnt += 1
                j += 1
            if cnt >= 3:
                bursts.append((total, cnt, slug, t0))

    bursts = sorted(bursts, key=lambda x: x[0], reverse=True)[:5]

    # Simple recommendations based on best pnl bucket
    best_side = max(agg_side.items(), key=lambda kv: kv[1]["pnl"])[0]
    best_duration = max(agg_duration.items(), key=lambda kv: kv[1]["pnl"])[0]
    best_price_buckets = sorted(agg_price_bucket.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
    good_buckets = [k for k, v in best_price_buckets if v["n"] >= 10 and v["pnl"] > 0][:2]

    return {
        "wallet": wallet,
        "limit_used": limit,
        "resolved": resolved_n,
        "unresolved_or_unknown": unresolved,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "pnl_1usd_stake": pnl_total,
        "profit_factor": profit_factor,
        "max_drawdown_1usd": max_dd,
        "counts_asset": dict(counts_asset),
        "counts_side": dict(counts_side),
        "counts_duration": dict(counts_dur),
        "agg_duration": agg_duration,
        "agg_side": agg_side,
        "agg_asset": agg_asset,
        "agg_price_bucket": agg_price_bucket,
        "top_bursts": [
            {"total_size": round(b[0], 2), "count": b[1], "slug": b[2], "t0": b[3]} for b in bursts
        ],
        "recommendation": {
            "copy_durations": [best_duration] if best_duration in ("5m", "15m") else [],
            "copy_side": best_side,
            "min_entry_buckets_positive": good_buckets,
        },
    }


def main():
    if not RANKED_FILE.exists():
        raise SystemExit(f"Missing {RANKED_FILE}")
    ranked = json.loads(RANKED_FILE.read_text(encoding="utf-8"))
    if not ranked:
        raise SystemExit("profile_wallets_ranked.json is empty")

    results = []
    # Share gamma cache across wallets for efficiency (huge speedup)
    shared_gamma_cache: dict = {}

    for idx, item in enumerate(ranked, start=1):
        wallet = item.get("wallet")
        print(f"\n=== [{idx}/{len(ranked)}] {wallet} ===")

        # We patch resolve_slug_prices to use shared cache via closure-like approach:
        # easiest is to call analyze_wallet but with its local cache removed;
        # instead, we inline a small variant here to actually share the cache.

        url = DATA_TRADES_URL
        resp = requests.get(url, params={"user": wallet, "limit": 600}, timeout=25)
        resp.raise_for_status()
        trades = resp.json()
        if not isinstance(trades, list) or not trades:
            continue

        seen = set()
        uniq = []
        for t in trades:
            h = t.get("transactionHash")
            if h and h in seen:
                continue
            if h:
                seen.add(h)
            uniq.append(t)
        trades = uniq

        # Performance analysis
        perf_rows = []
        unresolved = 0

        counts_asset = Counter()
        counts_side = Counter()
        counts_dur = Counter()

        for t in trades:
            slug = str(t.get("slug", "") or "")
            dur = duration_from_slug(slug)
            if dur not in ("5m", "15m"):
                continue
            side = str(t.get("outcome", "") or "").upper()
            if side not in ("UP", "DOWN"):
                continue
            entry_price = float(t.get("price", 0) or 0)
            if entry_price <= 0 or entry_price >= 1:
                continue

            asset = asset_key_from_trade(t)
            counts_asset[asset] += 1
            counts_side[side] += 1
            counts_dur[dur] += 1

            # Resolve via shared cache
            info = resolve_slug_prices(slug, shared_gamma_cache)
            if not info:
                unresolved += 1
                continue
            closed, up_price, down_price = info
            if not closed:
                unresolved += 1
                continue

            won = (up_price > 0.9) if side == "UP" else (down_price > 0.9)
            pnl = (1.0 / entry_price - 1.0) if won else -1.0
            perf_rows.append((dur, asset, side, entry_price, won, pnl, up_price, down_price))

        resolved_n = len(perf_rows)
        if resolved_n == 0:
            continue

        wins = sum(1 for r in perf_rows if r[4])
        losses = resolved_n - wins
        wr = wins / resolved_n * 100.0
        pnl_total = sum(r[5] for r in perf_rows)

        pos_sum = sum(r[5] for r in perf_rows if r[5] > 0)
        neg_sum = abs(sum(r[5] for r in perf_rows if r[5] < 0))
        profit_factor = pos_sum / neg_sum if neg_sum > 0 else 0.0

        # max drawdown (un-ordered approximation)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in perf_rows:
            equity += r[5]
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

        def agg_simple(idx: int) -> dict:
            m = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
            for r in perf_rows:
                k = r[idx]
                m[k]["n"] += 1
                if r[4]:
                    m[k]["w"] += 1
                m[k]["pnl"] += r[5]
            return {
                k: {"n": v["n"], "wr": (v["w"] / v["n"] * 100.0 if v["n"] else 0.0), "pnl": v["pnl"]}
                for k, v in m.items()
            }

        agg_duration = agg_simple(0)
        agg_side = agg_simple(2)
        agg_asset = agg_simple(1)

        agg_price_bucket = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
        for r in perf_rows:
            b = bucket_entry(r[3])
            agg_price_bucket[b]["n"] += 1
            if r[4]:
                agg_price_bucket[b]["w"] += 1
            agg_price_bucket[b]["pnl"] += r[5]
        agg_price_bucket = {
            k: {"n": v["n"], "wr": (v["w"] / v["n"] * 100.0 if v["n"] else 0.0), "pnl": v["pnl"]}
            for k, v in agg_price_bucket.items()
        }

        best_side = max(agg_side.items(), key=lambda kv: kv[1]["pnl"])[0]
        best_duration = max(agg_duration.items(), key=lambda kv: kv[1]["pnl"])[0]
        best_price_buckets = sorted(agg_price_bucket.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
        good_buckets = [k for k, v in best_price_buckets if v["n"] >= 10 and v["pnl"] > 0][:2]

        # Bursts: 3+ trades on same slug within 30s
        by_slug_ts = defaultdict(list)
        for t in trades:
            slug = str(t.get("slug", "") or "")
            dur = duration_from_slug(slug)
            if dur not in ("5m", "15m"):
                continue
            ts = t.get("timestamp", 0)
            try:
                ts = int(float(ts))
            except Exception:
                continue
            if ts > 1_000_000_000_000:
                ts //= 1000
            size = float(t.get("size", 0) or 0)
            if ts and size > 0:
                by_slug_ts[slug].append((ts, size))

        bursts = []
        for slug, arr in by_slug_ts.items():
            arr.sort(key=lambda x: x[0])
            for i in range(len(arr)):
                t0 = arr[i][0]
                total = arr[i][1]
                cnt = 1
                j = i + 1
                while j < len(arr) and arr[j][0] - t0 <= 30:
                    total += arr[j][1]
                    cnt += 1
                    j += 1
                if cnt >= 3:
                    bursts.append((total, cnt, slug, t0))

        bursts = sorted(bursts, key=lambda x: x[0], reverse=True)[:5]

        result = {
            "wallet": wallet,
            "resolved": resolved_n,
            "unresolved_or_unknown": unresolved,
            "wins": wins,
            "losses": losses,
            "win_rate_0.9": wr,
            "pnl_1usd_stake": pnl_total,
            "profit_factor": profit_factor,
            "max_drawdown_1usd": max_dd,
            "counts_asset": dict(counts_asset),
            "counts_side": dict(counts_side),
            "counts_duration": dict(counts_dur),
            "agg_duration": agg_duration,
            "agg_side": agg_side,
            "agg_asset": agg_asset,
            "agg_price_bucket": agg_price_bucket,
            "top_bursts": [
                {"total_size": round(b[0], 2), "count": b[1], "slug": b[2], "t0": b[3]} for b in bursts
            ],
            "recommendation": {
                "copy_durations": [best_duration] if best_duration in ("5m", "15m") else [],
                "copy_side": best_side,
                "positive_entry_buckets": good_buckets,
            },
        }

        print(
            f"resolved={resolved_n} WR={wr:.2f}% pnl={pnl_total:+.2f} best_side={best_side} best_duration={best_duration}"
        )
        results.append(result)

        time.sleep(0.2)  # polite pacing

    DETAILED_OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved: {DETAILED_OUT}")


if __name__ == "__main__":
    main()

