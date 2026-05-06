import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import requests

LEADERBOARD_URL = "https://polymarket.com/es/leaderboard/crypto/weekly/profit"
WALLETS_FILE = Path("profile_wallets.json")
REPORT_FILE = Path("profile_wallets_report.json")
RANKED_FILE = Path("profile_wallets_ranked.json")


def fetch_leaderboard_wallets(url: str = LEADERBOARD_URL) -> list[str]:
    """
    Scrape wallet addresses (0x...) from the leaderboard page HTML.
    Returns unique lowercase addresses.
    """
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    html = resp.text
    wallets = re.findall(r"0x[a-fA-F0-9]{40}", html)
    unique = sorted({w.lower() for w in wallets})
    return unique


def save_wallets(wallets: list[str], path: Path = WALLETS_FILE) -> None:
    path.write_text(json.dumps(wallets, indent=2), encoding="utf-8")


def _safe_ts(raw_ts) -> int:
    try:
        ts = int(float(raw_ts))
    except (TypeError, ValueError):
        return 0
    if ts > 1_000_000_000_000:
        ts //= 1000
    return ts


def _slug_outcome(
    slug: str,
    slug_cache: dict[str, tuple[bool, float, float] | None],
) -> tuple[bool, float, float] | None:
    """
    Return (closed, up_price, down_price) for a market slug.
    Cached per run to avoid repeated Gamma calls.
    """
    if slug in slug_cache:
        return slug_cache[slug]

    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={"slug": slug},
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json()
        if not events or not events[0].get("markets"):
            slug_cache[slug] = None
            return None

        market = events[0]["markets"][0]
        closed = bool(market.get("closed", False))
        raw = market.get("outcomePrices", ["0.5", "0.5"])
        outcome_prices = json.loads(raw) if isinstance(raw, str) else raw
        up_price = float(outcome_prices[0])
        down_price = float(outcome_prices[1])
        slug_cache[slug] = (closed, up_price, down_price)
        return slug_cache[slug]
    except Exception:
        slug_cache[slug] = None
        return None


def _wallet_performance_metrics(
    trades: list[dict],
    slug_cache: dict[str, tuple[bool, float, float] | None],
    max_resolve_trades: int,
) -> dict:
    """
    Build $1-stake backtest style metrics from wallet trades.
    """
    ordered = sorted(trades, key=lambda t: _safe_ts(t.get("timestamp", 0)))
    if max_resolve_trades > 0:
        ordered = ordered[-max_resolve_trades:]

    resolved_pnls = []
    unresolved = 0
    for t in ordered:
        slug = str(t.get("slug", ""))
        side = str(t.get("outcome", "")).upper()
        price = float(t.get("price", 0) or 0)
        if not slug or side not in ("UP", "DOWN") or price <= 0 or price >= 1:
            unresolved += 1
            continue

        outcome = _slug_outcome(slug, slug_cache)
        if not outcome:
            unresolved += 1
            continue
        closed, up_price, down_price = outcome
        if not closed:
            unresolved += 1
            continue

        won = (up_price > 0.9) if side == "UP" else (down_price > 0.9)
        pnl = (1.0 / price - 1.0) if won else -1.0
        resolved_pnls.append(pnl)

    n = len(resolved_pnls)
    wins = sum(1 for p in resolved_pnls if p > 0)
    losses = n - wins
    spent = float(n)
    recovered = sum((1.0 + p) for p in resolved_pnls)
    pnl_total = recovered - spent
    win_rate = (wins / n * 100) if n else 0.0
    avg_pnl = (pnl_total / n) if n else 0.0

    pos_sum = sum(p for p in resolved_pnls if p > 0)
    neg_sum = abs(sum(p for p in resolved_pnls if p < 0))
    profit_factor = (pos_sum / neg_sum) if neg_sum > 0 else 0.0

    std_pnl = 0.0
    if n > 1:
        mean = avg_pnl
        var = sum((p - mean) ** 2 for p in resolved_pnls) / (n - 1)
        std_pnl = math.sqrt(var)

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in resolved_pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    # Higher is better; drawdown/std penalize unstable wallets.
    sample_bonus = min(20.0, n / 10.0)
    stability_score = (
        (win_rate - 50.0)
        + (avg_pnl * 100.0)
        + (profit_factor * 12.0)
        - (std_pnl * 15.0)
        - (max_dd * 1.5)
        + sample_bonus
    )

    return {
        "resolved_trades": n,
        "unresolved_trades": unresolved,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "spent_1usd_stake": spent,
        "recovered_1usd_stake": recovered,
        "pnl_1usd_stake": pnl_total,
        "avg_pnl_per_trade": avg_pnl,
        "profit_factor": profit_factor,
        "std_pnl": std_pnl,
        "max_drawdown_1usd": max_dd,
        "stability_score": stability_score,
    }


def profile_wallet(
    wallet: str,
    limit: int = 500,
    slug_cache: dict[str, tuple[bool, float, float] | None] | None = None,
    max_resolve_trades: int = 250,
) -> dict | None:
    if slug_cache is None:
        slug_cache = {}

    url = f"https://data-api.polymarket.com/trades?user={wallet}&limit={limit}"
    try:
        resp = requests.get(url, timeout=15)
        trades = resp.json()
    except Exception as exc:
        print(f"Error fetching {wallet}: {exc}")
        return None

    if not isinstance(trades, list) or not trades:
        print(f"Wallet {wallet[:10]}... : No trades found or API error.\n")
        return None

    sizes = []
    markets = set()
    categories = defaultdict(int)

    oldest_ts = 99999999999
    newest_ts = 0

    for t in trades:
        sizes.append(float(t.get("size", 0)))
        markets.add(t.get("conditionId", ""))

        ts = _safe_ts(t.get("timestamp", 0))
        oldest_ts = min(oldest_ts, ts)
        newest_ts = max(newest_ts, ts)

        slug = str(t.get("slug", "")).lower()

        if "-5m-" in slug or "-15m-" in slug or "-1m-" in slug or "updown" in slug:
            categories["HFT / Crypto Scalp"] += 1
        elif any(k in slug for k in ("trump", "biden", "election", "politics")):
            categories["Politics"] += 1
        elif any(k in slug for k in ("nba", "nfl", "champions", "sports")):
            categories["Sports"] += 1
        else:
            categories["Others (Pop Culture/Macro)"] += 1

    days_active = max(1, (newest_ts - oldest_ts) / 86400)
    trades_per_day = len(trades) / days_active
    avg_size = sum(sizes) / len(sizes) if sizes else 0
    max_size = max(sizes) if sizes else 0

    profile = "Desconocido"
    safety = "Baja"
    reason = ""

    if categories["HFT / Crypto Scalp"] > 0.8 * len(trades):
        if trades_per_day > 100:
            profile = "HFT Sniper / Market Maker (Crypto)"
            safety = "MUY PELIGROSO de copiar (Latencia)"
            reason = (
                "Hace demasiadas operaciones por dia en mercados de minutos. "
                "Probablemente caza liquidez."
            )
        else:
            profile = "Crypto Scalper"
            safety = "Peligroso de copiar"
            reason = "Opera criptomonedas a corto plazo. Alta probabilidad de slippage."
    elif trades_per_day < 10 and avg_size > 50:
        profile = "Swing Trader Direccional (Ballena)"
        safety = "EXCELENTE para copiar"
        reason = "Hace pocas apuestas pero muy fuertes en eventos que toman dias o semanas en resolverse."
    elif len(markets) > len(trades) * 0.8:
        profile = "Francotirador de Noticias / Macro"
        safety = "BUENO para copiar"
        reason = "Entra en muchos mercados distintos una sola vez con fuerte conviccion."
    else:
        if trades_per_day > 50:
            profile = "Market Maker (Diversificado)"
            safety = "Malo para copiar"
            reason = "Demasiado volumen de operaciones pequenas en muchos mercados."
        else:
            profile = "Trader Casual / Mixto"
            safety = "Moderado"
            reason = "Patron mixto. Necesitariamos ver su WinRate real."

    print(f"Wallet: {wallet}")
    print(f"Analyzing last {len(trades)} trades...")
    print(f"  -> Trades per day : {trades_per_day:.1f}")
    print(f"  -> Unique Markets : {len(markets)}")
    print(f"  -> Avg Bet Size   : ${avg_size:.2f} (Max: ${max_size:.2f})")
    print("  -> Main Focus     :")
    for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            print(f"       {cat}: {count / len(trades) * 100:.1f}%")
    print(f"  [DIAGNOSTICO] {profile}")
    print(f"  [COPIABILIDAD] {safety}")
    print(f"  [POR QUE] {reason}\n")

    perf = _wallet_performance_metrics(
        trades=trades,
        slug_cache=slug_cache,
        max_resolve_trades=max_resolve_trades,
    )
    print(
        "  [PERF] resolved={resolved_trades} WR={win_rate:.1f}% "
        "PnL($1 stake)={pnl_1usd_stake:+.2f} DD={max_drawdown_1usd:.2f} "
        "PF={profit_factor:.2f} Stability={stability_score:.2f}\n".format(**perf)
    )

    return {
        "wallet": wallet,
        "trades": len(trades),
        "trades_per_day": trades_per_day,
        "unique_markets": len(markets),
        "avg_size": avg_size,
        "max_size": max_size,
        "categories": dict(categories),
        "diagnostic": profile,
        "copyability": safety,
        "reason": reason,
        "performance": perf,
    }


def main():
    parser = argparse.ArgumentParser(description="Scrape leaderboard wallets and profile them.")
    parser.add_argument("--no-profile", action="store_true", help="Only scrape+save wallets.")
    parser.add_argument("--limit", type=int, default=500, help="Trades limit per wallet (default: 500).")
    parser.add_argument("--max-resolve", type=int, default=250, help="Max recent trades used for WR/stability resolution.")
    parser.add_argument("--min-winrate", type=float, default=52.0, help="Minimum WR filter for ranked candidates.")
    parser.add_argument("--min-stability", type=float, default=0.0, help="Minimum stability score filter.")
    parser.add_argument("--min-resolved", type=int, default=40, help="Minimum resolved sample size.")
    parser.add_argument("--min-profit-factor", type=float, default=1.05, help="Minimum profit factor.")
    parser.add_argument("--top", type=int, default=20, help="Top N ranked candidates to keep.")
    args = parser.parse_args()

    print("=== POLYMARKET WALLET PROFILER ===\n")
    wallets = fetch_leaderboard_wallets()
    save_wallets(wallets)
    print(f"Scraped wallets: {len(wallets)}")
    print(f"Saved to: {WALLETS_FILE}\n")

    if args.no_profile:
        return

    summaries = []
    slug_cache: dict[str, tuple[bool, float, float] | None] = {}
    for wallet in wallets:
        summary = profile_wallet(
            wallet,
            limit=args.limit,
            slug_cache=slug_cache,
            max_resolve_trades=args.max_resolve,
        )
        if summary:
            summaries.append(summary)

    REPORT_FILE.write_text(
        json.dumps(summaries, indent=2),
        encoding="utf-8",
    )
    print(f"Saved profile report: {REPORT_FILE} ({len(summaries)} wallets with trades)")

    ranked = sorted(
        summaries,
        key=lambda s: s.get("performance", {}).get("stability_score", -9999),
        reverse=True,
    )
    filtered = [
        s for s in ranked
        if s.get("performance", {}).get("resolved_trades", 0) >= args.min_resolved
        and s.get("performance", {}).get("win_rate", 0.0) >= args.min_winrate
        and s.get("performance", {}).get("profit_factor", 0.0) >= args.min_profit_factor
        and s.get("performance", {}).get("stability_score", -9999) >= args.min_stability
    ][: args.top]

    RANKED_FILE.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
    print(f"Saved ranked candidates: {RANKED_FILE} ({len(filtered)} selected)\n")

    if filtered:
        print("=== TOP CANDIDATES ===")
        for i, s in enumerate(filtered, start=1):
            p = s["performance"]
            print(
                f"{i:>2}. {s['wallet']} | WR {p['win_rate']:.1f}% | "
                f"PF {p['profit_factor']:.2f} | DD {p['max_drawdown_1usd']:.2f} | "
                f"Stab {p['stability_score']:.2f} | PnL {p['pnl_1usd_stake']:+.2f} | "
                f"resolved {p['resolved_trades']}"
            )
    else:
        print("No wallets passed current thresholds. Lower filters and retry.")


if __name__ == "__main__":
    main()

