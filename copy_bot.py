import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import setup_logging
from scalper.trader import (
    open_trade,
    set_gain_protection_enabled,
    check_open_positions_profiled,
    resolve_trade,
)
import scalper.config as scalper_cfg
from scalper.strategy_profiles import get_profile
from scalper.trader import set_active_trades_file, load_trades
from scalper.config import GAMMA_API_BASE

# Tracking file for processed transactions
SEEN_FILE = Path("copy_bot_seen.json")

def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def _safe_timestamp(raw_ts) -> int:
    """Normalize timestamps from API payload (sec/ms/string) to unix seconds."""
    try:
        value = int(float(raw_ts))
    except (TypeError, ValueError):
        return 0
    if value > 1_000_000_000_000:
        value = value // 1000
    return value


def _print_group_outcome_summary() -> None:
    """
    Print outcome summary by copied market group.
    Group definition: one market slug = one group.
    """
    trades = load_trades()
    grouped = {}

    for trade in trades:
        slug = trade.get("market_slug", "unknown")
        bucket = grouped.setdefault(
            slug,
            {"open": 0, "resolved": 0, "pnl": 0.0, "wins": 0, "losses": 0},
        )
        status = trade.get("status", "open")
        if status == "open":
            bucket["open"] += 1
            continue

        if status in ("won", "lost", "sold"):
            bucket["resolved"] += 1
            pnl = float(trade.get("pnl", 0) or 0)
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
            else:
                bucket["losses"] += 1

    if not grouped:
        return

    print("\n📊 Group outcome check (copied bets)")
    for slug, stats in grouped.items():
        if stats["resolved"] == 0:
            verdict = "PENDING"
        elif stats["pnl"] > 0:
            verdict = "PROFIT"
        elif stats["pnl"] < 0:
            verdict = "LOSS"
        else:
            verdict = "BREAKEVEN"
        short_slug = slug[:60]
        print(
            f"  - {verdict:<9} | pnl=${stats['pnl']:+.2f} | "
            f"resolved={stats['resolved']} (W:{stats['wins']}/L:{stats['losses']}) "
            f"| open={stats['open']} | {short_slug}"
        )


def _print_session_cashflow_summary() -> None:
    """
    Print money flow during the full copy session:
    - spent: total stakes opened
    - recovered: payouts/proceeds from resolved trades
    - open_exposure: still at risk
    """
    trades = load_trades()
    spent = 0.0
    recovered = 0.0
    open_exposure = 0.0

    for t in trades:
        stake = float(t.get("stake", 0) or 0)
        spent += stake
        status = t.get("status", "open")
        if status == "open":
            open_exposure += stake
            continue
        pnl = float(t.get("pnl", 0) or 0)
        recovered += stake + pnl

    net_realized = recovered - (spent - open_exposure)
    print(
        "\n💵 Session cashflow | "
        f"spent=${spent:.2f} | recovered=${recovered:.2f} | "
        f"open_exposure=${open_exposure:.2f} | net_realized=${net_realized:+.2f}"
    )


def _resolve_copy_positions(copy_profile) -> None:
    """
    Resolve closed markets for copy positions (hold-to-resolution mode).
    Uses market slug (Gamma events endpoint) because copy trades come from Data API
    and conditionId is not valid as Gamma market id.
    """
    _ = copy_profile  # kept for interface consistency
    trades = load_trades()
    changed = False

    open_trades = [t for t in trades if t.get("status") == "open"]
    for t in open_trades:
        slug = t.get("market_slug", "")
        if not slug:
            continue
        try:
            resp = requests.get(f"{GAMMA_API_BASE}/events", params={"slug": slug}, timeout=8)
            resp.raise_for_status()
            events = resp.json()
            if not events:
                continue
            event = events[0]
            markets = event.get("markets", [])
            if not markets:
                continue
            market = markets[0]
            closed = bool(market.get("closed", False))
            if not closed:
                continue

            outcome_prices_raw = market.get("outcomePrices", ["0.5", "0.5"])
            if isinstance(outcome_prices_raw, str):
                outcome_prices = json.loads(outcome_prices_raw)
            else:
                outcome_prices = outcome_prices_raw

            up_price = float(outcome_prices[0])
            down_price = float(outcome_prices[1])
            side = t.get("side", "")
            won = (up_price > 0.9) if side == "UP" else (down_price > 0.9)
            result = resolve_trade(t["id"], won)
            if result:
                changed = True
                status = result.get("status", "").upper()
                asset = result.get("asset", "?")
                pnl = float(result.get("pnl", 0) or 0)
                print(f"  [RESOLVED] {asset} {side} -> {status} | pnl=${pnl:+.2f}")
        except Exception:
            continue

    if changed:
        # Optional sync pass for non-copy positions in same file (harmless)
        try:
            actions = check_open_positions_profiled(signal_scores=None, profile=copy_profile, markets_data=None)
            for action in actions:
                trade = action.get("trade", {})
                status = trade.get("status", "").upper()
                asset = trade.get("asset", "?")
                side = trade.get("side", "?")
                pnl = float(trade.get("pnl", 0) or 0)
                print(f"  [RESOLVED] {asset} {side} -> {status} | pnl=${pnl:+.2f}")
        except Exception:
            pass


def run_copy_bot(
    target_wallet: str,
    stake: float,
    duration_filter: int = 5,
    is_live: bool = False,
    catch_up_seconds: int = 300,
    allowed_durations: tuple[int, ...] = (5, 15),
    asset_filter: str = None,
    side_filter: str = None,
    min_original_size: float = 0.0,
    max_original_size: float = 0.0,
    stake_multiplier: float = 0.0,
    poll_interval: float = 0.5,
):
    setup_logging()
    logger = logging.getLogger("polybot.copy")

    # Copy strategy must persist in isolated file
    copy_profile = get_profile("copy")
    set_active_trades_file(copy_profile.trades_file)

    # Initialize live client if requested
    if is_live:
        from scalper.live_client import init_live_client
        if not init_live_client(dry_run=False):
            print("❌ Failed to initialize live client. Check .env credentials.")
            return
        
    print(f"🚀 Starting Copy Bot tracking: {target_wallet}")
    durations_label = ",".join(str(d) for d in sorted(set(allowed_durations)))
    print(f"💰 Stake per trade: ${stake} | Filter: {durations_label}m markets")
    print(f"⚙️  Mode: {'LIVE' if is_live else 'PAPER'}")
    print("──────────────────────────────────────────────────")
    
    seen_txs = load_seen()
    
    # Override global configs for the copy session
    scalper_cfg.HFT_STAKE = stake
    
    # User requested ONLY buy, holding to resolution. We disable gain protection and sells.
    set_gain_protection_enabled(False)
    scalper_cfg.HOLD_ONLY = True

    # Copy only trades newer than this moving threshold.
    # catch_up_seconds lets us copy "very recent" trades right after startup.
    start_time_ts = int(time.time()) - max(0, int(catch_up_seconds))
    last_summary_ts = 0

    session = requests.Session()

    while True:
        try:
            url = f"https://data-api.polymarket.com/trades?user={target_wallet}&limit=10"
            resp = session.get(url, timeout=5)
            if resp.status_code == 200:
                trades = resp.json()
                if trades:
                    newest_ts = _safe_timestamp(trades[0].get("timestamp", 0))
                    age = int(time.time()) - newest_ts if newest_ts else -1
                    # print(f"  [POLL] fetched={len(trades)} | newest_age={age}s | wallet={target_wallet[:8]}...")
                else:
                    pass # print(f"  [POLL] fetched=0 | wallet={target_wallet[:8]}... (maybe this is not proxyWallet)")
                
                # Process oldest first so we replay in correct order if multiple arrive
                for t in reversed(trades):
                    tx_hash = t.get("transactionHash")
                    if not tx_hash or tx_hash in seen_txs:
                        continue
                        
                    seen_txs.add(tx_hash)
                    save_seen(seen_txs)
                    
                    # We only want to execute on trades that happened after we started the bot
                    trade_ts = _safe_timestamp(t.get("timestamp", 0))
                    if trade_ts < start_time_ts - 10:
                        # Skip old trades from before we started (give 10s buffer)
                        continue
                    
                    side_action = str(t.get("side", "")).upper()
                    if side_action != "BUY":
                        continue
                        
                    slug = t.get("slug", "")
                    
                    # Filter by accepted durations (e.g. 5m + 15m)
                    is_allowed_duration = any(f"-{d}m-" in slug for d in allowed_durations)
                    if not is_allowed_duration:
                        allowed_txt = "/".join(f"{d}m" for d in allowed_durations)
                        print(f"  [SKIP] Not a {allowed_txt} market: {slug}")
                        continue
                        
                    # Determine asset and direction
                    title = t.get("title", "")
                    asset_key = "UNKNOWN"
                    if "Bitcoin" in title: asset_key = "BTC"
                    elif "Ethereum" in title: asset_key = "ETH"
                    elif "Solana" in title: asset_key = "SOL"
                    elif "XRP" in title: asset_key = "XRP"
                    
                    if asset_key == "UNKNOWN":
                        print(f"  [SKIP] Unknown asset in title: {title}")
                        continue
                        
                    if asset_filter and asset_key != asset_filter.upper():
                        print(f"  [SKIP] Filtering out {asset_key} (only copying {asset_filter.upper()})")
                        continue
                        
                    direction = str(t.get("outcome", "")).upper()  # "UP" or "DOWN"
                    if direction not in ["UP", "DOWN"]:
                        continue

                    if side_filter and direction != side_filter.upper():
                        print(f"  [SKIP] Filtering out {direction} (only copying {side_filter.upper()})")
                        continue
                        
                    token_id = str(t.get("asset") or "")
                    api_price = float(t.get("price", 0.5))
                    entry_price = api_price
                    original_size = float(t.get("size", 0))
                    
                    if min_original_size > 0 and original_size < min_original_size:
                        print(f"  [SKIP] Original bet (${original_size:.2f}) smaller than --min-original-size (${min_original_size:.2f})")
                        continue
                        
                    if max_original_size > 0 and original_size > max_original_size:
                        print(f"  [SKIP] Original bet (${original_size:.2f}) larger than --max-original-size (${max_original_size:.2f})")
                        continue
                        
                    actual_stake = stake
                    if stake_multiplier > 0:
                        actual_stake = original_size * stake_multiplier
                    
                    print(
                        f"\n  🎯 [COPY DETECTED] {target_wallet[:6]}... bought "
                        f"{asset_key} {direction} @ {api_price:.2f} (Target size: ${original_size:.2f})"
                    )

                    # REST pre-entry snapshot to approximate live execution even when WS is cold.
                    if token_id:
                        try:
                            from scalper.live_client import check_entry_conditions
                            max_spread = float(getattr(scalper_cfg, "HFT_MAX_SPREAD", 0.03))
                            rest_check = check_entry_conditions(
                                token_id=token_id,
                                max_spread=max_spread,
                                asset=asset_key,
                                side=direction,
                            )
                            rest_best_ask = rest_check.get("best_ask")
                            if rest_check.get("can_enter"):
                                entry_price = float(rest_check.get("best_ask") or api_price)
                                best_bid = rest_check.get("best_bid")
                                spread = rest_check.get("spread")
                                print(
                                    "  [REST ENTRY] OK | "
                                    f"bid=${best_bid:.4f} ask=${entry_price:.4f} "
                                    f"spread=${spread:.4f} | using ask as entry"
                                )
                            else:
                                reason = rest_check.get("reason", "unknown")
                                if rest_best_ask is not None:
                                    # Copy mode override: informational block only.
                                    entry_price = float(rest_best_ask)
                                    print(
                                        f"  [REST ENTRY] WARN-OVERRIDE | {reason} | "
                                        f"using ask=${entry_price:.4f}"
                                    )
                                else:
                                    print(f"  [REST ENTRY] BLOCKED-HARD | {reason}")
                                    continue
                        except Exception as rest_exc:
                            print(f"  [REST ENTRY] ERROR | {rest_exc} | fallback api_price=${api_price:.4f}")
                    
                    event_start = datetime.fromtimestamp(trade_ts, tz=timezone.utc)
                    event_minutes = 5
                    if "-15m-" in slug:
                        event_minutes = 15
                    elif "-5m-" in slug:
                        event_minutes = 5
                    event_end = event_start + timedelta(minutes=event_minutes)

                    # Execute using trader API signature
                    opened = open_trade(
                        asset=asset_key,
                        side=direction,
                        entry_price=entry_price,
                        stake=actual_stake,
                        signal_score=1.0,
                        market_slug=slug,
                        gamma_id="",
                        event_start=event_start,
                        event_end=event_end,
                        token_id=token_id,
                        bypass_checks=True,
                    )

                    if opened:
                        print(
                            f"  ✅ Copied as {opened['id']} | {asset_key} {direction} "
                            f"@ {opened['entry_price']:.4f} | stake ${opened['stake']:.2f} "
                            f"| source={'REST/WS' if token_id else 'API'}"
                        )
                    else:
                        print("  ⚠️ Copy detected but trade was not opened (liquidity/price constraints).")

            # Keep portfolio state fresh: resolves copied positions once markets close.
            _resolve_copy_positions(copy_profile)

            now_ts = int(time.time())
            if now_ts - last_summary_ts >= 30:
                _print_group_outcome_summary()
                _print_session_cashflow_summary()
                last_summary_ts = now_ts
                    
        except Exception as e:
            logger.error(f"Copy bot API error: {e}")
            
        time.sleep(poll_interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket Copy Bot")
    parser.add_argument("--target", required=True, help="Wallet address to copy")
    parser.add_argument("--stake", type=float, default=1.0, help="Stake per copied trade ($)")
    parser.add_argument("--durations", type=int, nargs="+", default=[5], help="Market durations to filter (e.g. 5 15)")
    parser.add_argument("--live", action="store_true", help="Execute real trades on CLOB")
    parser.add_argument(
        "--catch-up",
        type=int,
        default=300,
        help="Copy trades from the last N seconds at startup (default: 300)",
    )
    parser.add_argument("--asset", type=str, default=None, help="Asset to filter (e.g. ETH, BTC)")
    parser.add_argument("--side", type=str, default=None, help="Side to filter (e.g. UP, DOWN)")
    parser.add_argument("--min-original-size", type=float, default=0.0, help="Only copy trades where the original bot bet at least this much ($)")
    parser.add_argument("--max-original-size", type=float, default=0.0, help="Only copy trades where the original bot bet at most this much ($)")
    parser.add_argument("--stake-multiplier", type=float, default=0.0, help="Multiply original bet size by this (e.g. 0.1 for 10%% of their size). Overrides --stake.")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="Seconds between API polls (default 0.5)")
    args = parser.parse_args()
    
    run_copy_bot(
        target_wallet=args.target, 
        stake=args.stake, 
        duration_filter=args.durations[0], 
        is_live=args.live, 
        catch_up_seconds=args.catch_up,
        allowed_durations=tuple(args.durations),
        asset_filter=args.asset,
        side_filter=args.side,
        min_original_size=args.min_original_size,
        max_original_size=args.max_original_size,
        stake_multiplier=args.stake_multiplier,
        poll_interval=args.poll_interval
    )
