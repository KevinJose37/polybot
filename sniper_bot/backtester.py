"""
sniper_bot/backtester.py — L2 tick-by-tick backtester.

Replays historical parquet data to evaluate signal parameters,
TP strategies, and fill rates without waiting for live sessions.

Uses the SAME signal gates and TP logic as live mode.
Processes 1 parquet at a time (memory-safe for 16 GB RAM).

Usage:
    python -m sniper_bot.backtester
    python -m sniper_bot.backtester --limit 5
    python -m sniper_bot.backtester --asset BTC
"""
import os
import sys
import gc
import json
import time
import logging
import argparse
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import duckdb

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sniper_bot.backtester")

BASE_DIR = Path(__file__).resolve().parent.parent
PARQUET_DIR = BASE_DIR / "data" / "parquet"
CACHE_FILE = PARQUET_DIR / "known_crypto_markets.json"

ASSETS_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "XRP": ["xrp", "ripple"],
    "SOL": ["solana", "sol"],
}


@dataclass
class BacktestConfig:
    """Backtester-specific config mirrors SniperConfig signal gates."""
    trigger_low: float = 0.49
    trigger_high: float = 0.53
    max_ask: float = 0.55
    min_ask: float = 0.35
    max_spread: float = 0.03
    min_depth: float = 50.0
    entry_window_s: int = 60
    wall_threshold: float = 200.0
    min_tp_increment: float = 0.02
    max_tp: float = 0.65
    tp_fallback_low: float = 0.08
    tp_fallback_mid: float = 0.05
    tp_fallback_high: float = 0.03
    slippage: float = 0.001
    stake: float = 10.0
    market_duration_s: int = 300  # 5 minutes
    assets: tuple = ("BTC", "ETH", "XRP")


@dataclass
class BTrade:
    """A backtested trade."""
    market_id: str
    asset: str
    direction: str
    entry_price: float
    entry_time: float    # seconds_since_start
    tp_price: float
    exit_price: float = 0.0
    exit_time: float = 0.0
    fill_type: str = ""  # MAKER / RESOLUTION_WIN / RESOLUTION_LOSS
    pnl: float = 0.0
    shares: float = 0.0


@dataclass
class BacktestResult:
    """Aggregated backtester results."""
    total_markets: int = 0
    markets_with_signals: int = 0
    total_signals: int = 0
    total_entries: int = 0
    trades: list = field(default_factory=list)
    rejection_reasons: dict = field(default_factory=dict)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def maker_fills(self) -> int:
        return sum(1 for t in self.trades if t.fill_type == "MAKER")

    @property
    def maker_fill_rate(self) -> float:
        total = len(self.trades)
        return self.maker_fills / total if total > 0 else 0.0

    def avg_time_to_fill(self) -> float:
        fills = [t.exit_time - t.entry_time for t in self.trades if t.fill_type == "MAKER"]
        return sum(fills) / len(fills) if fills else 0.0


def load_crypto_cids() -> dict[str, str]:
    """Load crypto CID → asset mapping."""
    if not CACHE_FILE.exists():
        logger.error("Cache not found: %s", CACHE_FILE)
        sys.exit(1)
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)

    cid_to_asset = {}
    for cid, info in cache.items():
        if not info.get("is_crypto"):
            continue
        q = info.get("question", "").lower()
        for asset, keywords in ASSETS_KEYWORDS.items():
            if any(kw in q for kw in keywords):
                cid_to_asset[cid] = asset
                break
    return cid_to_asset


def load_parquet_for_backtest(path: Path, cid_to_asset: dict,
                               target_assets: tuple) -> pd.DataFrame:
    """Load parquet filtered to target crypto assets via DuckDB."""
    cids = [c for c, a in cid_to_asset.items() if a in target_assets]
    if not cids:
        return pd.DataFrame()

    con = duckdb.connect()
    cid_df = pd.DataFrame({"cid": cids})
    con.register("target_cids", cid_df)

    query = f"""
        SELECT
            CAST(market AS VARCHAR) as cid,
            timestamp,
            CAST(best_bid AS DOUBLE) as best_bid,
            CAST(best_ask AS DOUBLE) as best_ask,
            CAST(price AS DOUBLE) as price,
            CAST(size AS DOUBLE) as size,
            side
        FROM read_parquet('{path}')
        WHERE event_type = 'price_change'
          AND CAST(market AS VARCHAR) IN (SELECT cid FROM target_cids)
        ORDER BY timestamp ASC
    """
    df = con.execute(query).df()
    con.close()
    del cid_df
    return df


def reconstruct_book_state(rows: list, books: dict) -> dict:
    """
    Given a row from the parquet, update the book state and return
    a snapshot with L5 bids/asks, best_bid, best_ask, spread, depth.
    """
    # rows is actually a single row dict
    row = rows
    cid = row["cid"]

    if cid not in books:
        books[cid] = defaultdict(float)

    if pd.notna(row["price"]) and pd.notna(row["size"]):
        books[cid][round(float(row["price"]), 4)] = float(row["size"])

    bb = float(row["best_bid"]) if pd.notna(row["best_bid"]) else 0.0
    ba = float(row["best_ask"]) if pd.notna(row["best_ask"]) else 0.0

    if bb <= 0 or ba <= 0:
        return None

    # L5 depths
    bids = sorted([p for p in books[cid] if p <= bb and books[cid][p] > 0], reverse=True)[:5]
    asks = sorted([p for p in books[cid] if p >= ba and books[cid][p] > 0])[:5]

    bid_depth = sum(books[cid][p] for p in bids)
    ask_depth = sum(books[cid][p] for p in asks)
    spread = round(ba - bb, 4)

    return {
        "best_bid": bb,
        "best_ask": ba,
        "spread": spread,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "asks_l5": [(p, books[cid][p]) for p in asks],
        "bids_l5": [(p, books[cid][p]) for p in bids],
    }


def compute_tp(entry_price: float, asks_l5: list, cfg: BacktestConfig) -> float:
    """Compute dynamic TP from book asks — same logic as executor."""
    min_tp = entry_price + cfg.min_tp_increment
    cumulative = 0.0
    for price, size in asks_l5:
        if price <= min_tp:
            continue
        if price > cfg.max_tp:
            break
        cumulative += size
        if size >= cfg.wall_threshold or cumulative >= cfg.wall_threshold * 1.5:
            return max(round(price - 0.01, 4), min_tp)

    # Fallback
    if entry_price < 0.40:
        return min(entry_price + cfg.tp_fallback_low, cfg.max_tp)
    elif entry_price < 0.50:
        return min(entry_price + cfg.tp_fallback_mid, cfg.max_tp)
    else:
        return min(entry_price + cfg.tp_fallback_high, cfg.max_tp)


def backtest_market(ticks: list[dict], asset: str, market_id: str,
                    cfg: BacktestConfig) -> list[BTrade]:
    """
    Backtest a single 5-minute market.
    Replays ticks chronologically, looking for entry signals in first 60s,
    then monitoring for maker fills or resolution.
    """
    if len(ticks) < 10:
        return []

    # Reconstruct timeline
    books = {}
    trades = []
    active_trade: BTrade | None = None
    entry_made = False
    prev_ask = 0.0
    ask_history = deque(maxlen=20)

    # Determine market start time (first tick)
    start_time = ticks[0]["timestamp"]
    if isinstance(start_time, str):
        start_time = pd.Timestamp(start_time)
    start_ts = start_time.timestamp() if hasattr(start_time, 'timestamp') else float(start_time)

    for tick in ticks:
        ts = tick["timestamp"]
        if isinstance(ts, str):
            ts = pd.Timestamp(ts)
        tick_ts = ts.timestamp() if hasattr(ts, 'timestamp') else float(ts)
        elapsed = tick_ts - start_ts

        # Update book
        snap = reconstruct_book_state(tick, books)
        if not snap:
            continue

        ba = snap["best_ask"]
        bb = snap["best_bid"]

        # Track ask velocity
        ask_history.append((elapsed, ba))
        velocity = 0.0
        if len(ask_history) >= 2:
            old = [p for t, p in ask_history if elapsed - t >= 0.5]
            if old:
                velocity = ba - old[-1]

        # ── Check active trade for maker fill ────────────────
        if active_trade:
            if bb >= active_trade.tp_price:
                active_trade.exit_price = active_trade.tp_price
                active_trade.exit_time = elapsed
                active_trade.fill_type = "MAKER"
                active_trade.pnl = round(
                    active_trade.shares * (active_trade.exit_price - active_trade.entry_price), 4)
                trades.append(active_trade)
                active_trade = None
            elif elapsed >= cfg.market_duration_s:
                # Resolution
                if bb > active_trade.entry_price:
                    active_trade.exit_price = min(bb, 1.0)
                    active_trade.fill_type = "RESOLUTION_WIN"
                else:
                    active_trade.exit_price = max(bb, 0.0)
                    active_trade.fill_type = "RESOLUTION_LOSS"
                active_trade.exit_time = elapsed
                active_trade.pnl = round(
                    active_trade.shares * (active_trade.exit_price - active_trade.entry_price), 4)
                trades.append(active_trade)
                active_trade = None
            continue

        # ── Check for entry signal (only in entry window) ────
        if entry_made or elapsed > cfg.entry_window_s:
            continue

        # Signal gates (same as signal_engine.py)
        if ba < cfg.min_ask or ba > cfg.max_ask:
            continue
        if not (cfg.trigger_low <= ba <= cfg.trigger_high):
            continue
        if snap["spread"] > cfg.max_spread:
            continue
        if snap["ask_depth"] < cfg.min_depth:
            continue
        if abs(velocity) > 0.05:
            continue

        # Entry!
        fill_price = round(ba + cfg.slippage, 4)
        shares = round(cfg.stake / fill_price, 4)
        tp = compute_tp(fill_price, snap["asks_l5"], cfg)

        active_trade = BTrade(
            market_id=market_id,
            asset=asset,
            direction="UP",
            entry_price=fill_price,
            entry_time=elapsed,
            tp_price=tp,
            shares=shares,
        )
        entry_made = True

    # Handle trade still open at end
    if active_trade:
        snap = reconstruct_book_state(ticks[-1], books)
        if snap:
            final_bid = snap["best_bid"]
        else:
            final_bid = 0.0
        active_trade.exit_price = final_bid
        active_trade.exit_time = cfg.market_duration_s
        active_trade.fill_type = "RESOLUTION_WIN" if final_bid > active_trade.entry_price else "RESOLUTION_LOSS"
        active_trade.pnl = round(
            active_trade.shares * (active_trade.exit_price - active_trade.entry_price), 4)
        trades.append(active_trade)

    return trades


def run_backtest(cfg: BacktestConfig, limit: int = 0) -> BacktestResult:
    """Run full backtest over all available parquet files."""
    result = BacktestResult()

    print("=" * 70)
    print("  SNIPER BOT — L2 TICK-BY-TICK BACKTESTER")
    print(f"  Trigger: [{cfg.trigger_low}, {cfg.trigger_high}]")
    print(f"  Assets: {cfg.assets} | Stake: ${cfg.stake}")
    print("=" * 70)

    # Load CID mapping
    cid_to_asset = load_crypto_cids()
    logger.info("Loaded %d crypto CIDs", len(cid_to_asset))

    # Get parquet files
    files = sorted(PARQUET_DIR.glob("polymarket_orderbook_*.parquet"))
    if limit > 0:
        files = files[:limit]

    if not files:
        logger.error("No parquet files found in %s", PARQUET_DIR)
        return result

    print(f"  Files to process: {len(files)}")

    t_global = time.time()

    for idx, pf in enumerate(files):
        t_file = time.time()
        label = pf.stem.replace("polymarket_orderbook_", "")
        print(f"\n  [{idx+1}/{len(files)}] {label}...", end=" ", flush=True)

        # Load data
        df = load_parquet_for_backtest(pf, cid_to_asset, cfg.assets)
        if len(df) == 0:
            print("empty")
            continue

        # Map CID → asset
        df["asset"] = df["cid"].map(cid_to_asset)
        df = df.dropna(subset=["asset"])

        # Group by market (CID) and process each
        markets_processed = 0
        for cid, group in df.groupby("cid"):
            asset = cid_to_asset.get(cid, "UNK")
            if asset not in cfg.assets:
                continue

            ticks = group.to_dict("records")
            result.total_markets += 1

            trades = backtest_market(ticks, asset, cid, cfg)
            if trades:
                result.markets_with_signals += 1
                result.total_entries += len(trades)
                result.trades.extend(trades)
            markets_processed += 1

        del df
        gc.collect()

        elapsed = time.time() - t_file
        print(f"{markets_processed} markets, {len(result.trades)} trades total ({elapsed:.1f}s)")

    # ── Print report ──────────────────────────────────────────
    elapsed_total = time.time() - t_global
    print(f"\n{'=' * 70}")
    print(f"  BACKTEST COMPLETE — {elapsed_total:.0f}s")
    print(f"{'=' * 70}")
    print(f"  Markets analyzed:    {result.total_markets}")
    print(f"  Markets w/ signals:  {result.markets_with_signals}")
    print(f"  Total trades:        {len(result.trades)}")
    print(f"  Win Rate:            {result.win_rate*100:.1f}%"
          f"  ({result.wins}W / {result.losses}L)")
    print(f"  Total P&L:           ${result.total_pnl:+.2f}")
    print(f"  Maker Fill Rate:     {result.maker_fill_rate*100:.0f}%")
    print(f"  Avg Time to Fill:    {result.avg_time_to_fill():.0f}s")

    if result.trades:
        pnls = [t.pnl for t in result.trades]
        wins_pnl = [p for p in pnls if p > 0]
        loss_pnl = [p for p in pnls if p <= 0]
        avg_win = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
        avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
        print(f"  Avg Win:             ${avg_win:+.4f}")
        print(f"  Avg Loss:            ${avg_loss:+.4f}")
        print(f"  Expectancy:          ${avg_win * result.win_rate + avg_loss * (1 - result.win_rate):+.4f}")

        # Per-asset breakdown
        print(f"\n  Per-Asset Breakdown:")
        for asset in cfg.assets:
            at = [t for t in result.trades if t.asset == asset]
            if not at:
                continue
            aw = sum(1 for t in at if t.pnl > 0)
            al = sum(1 for t in at if t.pnl <= 0)
            apnl = sum(t.pnl for t in at)
            awr = aw / (aw + al) if (aw + al) > 0 else 0
            print(f"    {asset}: {len(at)} trades | WR: {awr*100:.1f}% | P&L: ${apnl:+.2f}")

    print(f"{'=' * 70}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Sniper Bot L2 Backtester")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N parquet files")
    parser.add_argument("--assets", type=str, default="BTC,ETH,XRP",
                        help="Comma-separated assets")
    parser.add_argument("--trigger-low", type=float, default=0.49)
    parser.add_argument("--trigger-high", type=float, default=0.53)
    parser.add_argument("--stake", type=float, default=10.0)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--min-depth", type=float, default=50.0)
    args = parser.parse_args()

    cfg = BacktestConfig(
        trigger_low=args.trigger_low,
        trigger_high=args.trigger_high,
        assets=tuple(a.strip().upper() for a in args.assets.split(",")),
        stake=args.stake,
        max_spread=args.max_spread,
        min_depth=args.min_depth,
    )
    run_backtest(cfg, limit=args.limit)


if __name__ == "__main__":
    main()
