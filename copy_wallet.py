"""
copy_wallet.py  -  Multi-Wallet Copy Bot for Polymarket
========================================================
Copies ALL buy/sell transactions from multiple target wallets.
Runs in ONE process, ONE console, with a clean dashboard display.

Each wallet gets:
  - Its OWN independent capital (e.g. $40 each)
  - Isolated trade log:  data/trades/copy_{wallet_short}.json
  - Isolated seen file:  data/trades/copy_{wallet_short}_seen.json
  - Independent P&L, WR, positions — for strategy comparison

Positions are NOT artificially capped. Each wallet can open as many
positions as its capital allows (capital / stake = max trades).

Usage (fleet - all 5 wallets, $40 each):
  python copy_wallet.py --fleet
  python copy_wallet.py --fleet --capital 40 --stake 4

Usage (single wallet):
  python copy_wallet.py --target 0x5d0f03cf... --capital 40
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Project imports ──────────────────────────────────────────────
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import setup_logging
from scalper.config import GAMMA_API_BASE

# ── Constants ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data" / "trades"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_TRADES_URL = "https://data-api.polymarket.com/activity"
GAMMA_EVENTS_URL = f"{GAMMA_API_BASE}/events"

# ── Default fleet wallets ────────────────────────────────────────
FLEET_WALLETS = [
    {"address": "0x5d0f03cf1243a3e21262d6cf844795afd9fff0ad", "name": "EB99999",       "cat": "GEO",   "wr": 94.1},
    {"address": "0xdb15fbbcc1a8d1cbe112f7a2d74f6f752f2314f1", "name": "memain",        "cat": "SPORT", "wr": 85.7},
    {"address": "0xe7348e92f76c26e879a9d0c1ff37cdbc4a926a78", "name": "bobthetradoor", "cat": "GEO",   "wr": 41.7},
    {"address": "0xd7f85d0eb0fe0732ca38d9107ad0d4d01b1289e4", "name": "tdrhrhhd",      "cat": "POL",   "wr": 39.7},
    {"address": "0xf989bd9c62b1eae2c388515fcc766527a8b147cc", "name": "vovatoxic",     "cat": "GEO",   "wr": 61.4},
    {"address": "0x5490687ee61406afbb1fd887937fdbb7fe1cb051", "name": "crypto",        "cat": "CRYP", "wr": 84.2},
    {"address": "0xed107a85a4585a381e48c7f7ca4144909e7dd2e5", "name": "bobe2",         "cat": "GEO",  "wr": 87.9}
]


# ══════════════════════════════════════════════════════════════════
#  FILE I/O per wallet
# ══════════════════════════════════════════════════════════════════

def _ws(addr: str) -> str:
    """Short wallet id for file naming."""
    return addr[2:10].lower()


def _trades_path(addr: str) -> Path:
    return DATA_DIR / f"copy_{_ws(addr)}.json"


def _seen_path(addr: str) -> Path:
    return DATA_DIR / f"copy_{_ws(addr)}_seen.json"


def load_trades(addr: str) -> list[dict]:
    p = _trades_path(addr)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_trades(addr: str, trades: list[dict]):
    _trades_path(addr).write_text(json.dumps(trades, indent=2, default=str), encoding="utf-8")


def load_seen(addr: str) -> set:
    p = _seen_path(addr)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(addr: str, seen: set):
    _seen_path(addr).write_text(json.dumps(list(seen)), encoding="utf-8")


def _safe_ts(raw) -> int:
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return v // 1000 if v > 1_000_000_000_000 else v


def _next_id(trades: list[dict], addr: str) -> str:
    prefix = _ws(addr)
    mx = 0
    for t in trades:
        try:
            mx = max(mx, int(t.get("id", "").split("_")[-1]))
        except (ValueError, IndexError):
            pass
    return f"cp_{prefix}_{mx + 1:03d}"


# ══════════════════════════════════════════════════════════════════
#  RESOLUTION CHECK
# ══════════════════════════════════════════════════════════════════

def _check_resolution(slug: str) -> dict | None:
    """Check if a market resolved via Gamma API."""
    try:
        resp = requests.get(GAMMA_EVENTS_URL, params={"slug": slug}, timeout=8)
        resp.raise_for_status()
        events = resp.json()
        if not events:
            return None
        mkt = events[0].get("markets", [{}])[0]
        if not bool(mkt.get("closed", False)):
            return None
        op = mkt.get("outcomePrices", '["0.5","0.5"]')
        if isinstance(op, str):
            op = json.loads(op)
        return {"up": float(op[0]), "down": float(op[1])}
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  WALLET TRACKER STATE
# ══════════════════════════════════════════════════════════════════

class WalletTracker:
    """Tracks one wallet: its trades, capital, seen txs."""

    def __init__(self, address: str, name: str, cat: str, wr: float,
                 capital: float, stake: float,
                 tp_pct: float, sl_pct: float, is_live: bool = False):
        self.address = address
        self.name = name
        self.cat = cat
        self.wr = wr
        self.capital = capital
        self.stake = stake
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.is_live = is_live
        self.seen = load_seen(address)
        self.start_ts = int(time.time())
        self.last_event = ""
        self.polls = 0
        self.skipped_no_liq = 0  # Tracks how many trades we skipped (realism)

    @property
    def ws(self) -> str:
        return _ws(self.address)

    @property
    def trades(self) -> list[dict]:
        return load_trades(self.address)

    @property
    def open_trades(self) -> list[dict]:
        return [t for t in self.trades if t.get("status") == "open"]

    @property
    def resolved_trades(self) -> list[dict]:
        return [t for t in self.trades if t.get("status") in ("won", "lost", "sold")]

    @property
    def exposure(self) -> float:
        return sum(t.get("stake", 0) for t in self.open_trades)

    @property
    def available(self) -> float:
        return self.capital - self.exposure

    @property
    def total_pnl(self) -> float:
        return sum(t.get("pnl", 0) or 0 for t in self.resolved_trades)

    @property
    def win_rate(self) -> float:
        res = self.resolved_trades
        if not res:
            return 0.0
        wins = sum(1 for t in res if (t.get("pnl", 0) or 0) > 0)
        return wins / len(res) * 100

    def poll_and_copy(self, session: requests.Session) -> list[str]:
        """Poll wallet for new trades, copy any BUYs. Returns list of event strings."""
        events = []
        self.polls += 1

        try:
            resp = session.get(
                DATA_TRADES_URL,
                params={"user": self.address, "limit": 20},
                timeout=15,
            )
            if resp.status_code != 200:
                return events
            api_trades = resp.json()
            if not isinstance(api_trades, list):
                return events
            # Filter to TRADE type only (activity also has REDEEM, etc)
            api_trades = [t for t in api_trades if t.get("type") == "TRADE"]
        except Exception as e:
            events.append(f"[{self.name}] API error: {e}")
            return events

        for t in reversed(api_trades):
            tx = t.get("transactionHash", "")
            if not tx or tx in self.seen:
                continue

            self.seen.add(tx)
            save_seen(self.address, self.seen)

            trade_ts = _safe_ts(t.get("timestamp", 0))
            if trade_ts < self.start_ts - 10:
                continue

            action = str(t.get("side", "")).upper()
            outcome = str(t.get("outcome", "")).strip()
            slug = t.get("slug", "")
            title = t.get("title", slug)
            token_id = str(t.get("asset", ""))
            price = float(t.get("price", 0.5) or 0.5)
            size = float(t.get("size", 0) or 0)

            if not outcome or not slug:
                continue

            if action == "SELL":
                msg = f"[{self.name}] SELL {outcome} @ ${price:.2f} | {title[:40]}"
                events.append(msg)
                self.last_event = msg
                continue

            if action != "BUY":
                continue

            # ── Capital check ──
            if self.exposure + self.stake > self.capital:
                avail = self.capital - self.exposure
                events.append(f"[{self.name}] SKIP no capital (${avail:.0f}) {title[:30]}")
                continue

            # ── Duplicate check ──
            if any(tr.get("slug") == slug and tr.get("side") == outcome for tr in self.open_trades):
                continue

            # ── REALISTIC: Verify orderbook (spread + liquidity only, NO R/R filter) ──
            # We trust the target wallet's conviction — only check the book is tradeable
            entry_price = price  # Default to API price
            entry_source = "API"
            if token_id:
                try:
                    from scalper.live_client import _fetch_rest_book
                    book = _fetch_rest_book(token_id)
                    if book:
                        bids = book.get("bids", [])
                        asks = book.get("asks", [])
                        if bids and asks:
                            sorted_asks = sorted(asks, key=lambda a: float(a.get("price", 0)))
                            sorted_bids = sorted(bids, key=lambda b: float(b.get("price", 0)), reverse=True)
                            best_ask = float(sorted_asks[0]["price"])
                            best_bid = float(sorted_bids[0]["price"])
                            ask_size = float(sorted_asks[0].get("size", 0))
                            spread = round(best_ask - best_bid, 4)

                            if spread > 0.08:
                                # Wide spread = risky execution
                                self.skipped_no_liq += 1
                                events.append(f"[{self.name}] SKIP spread ${spread:.3f} {title[:30]}")
                                continue
                            if ask_size < 1.0:
                                self.skipped_no_liq += 1
                                events.append(f"[{self.name}] SKIP thin ask ({ask_size:.0f}) {title[:30]}")
                                continue

                            # Use REAL best_ask as entry (what we'd actually pay)
                            entry_price = best_ask
                            entry_source = f"BOOK ask=${best_ask:.3f} bid=${best_bid:.3f} spr=${spread:.4f}"
                        else:
                            # One-sided book → use API price
                            entry_source = "API (one-sided book)"
                    else:
                        # Book fetch failed (404 for resolved/sports) → use API price
                        entry_source = "API (no book)"
                except Exception:
                    # Network error → use API price as fallback
                    entry_source = "API (error)"

            # ── Sanity: don't buy at extremes ──
            if entry_price >= 0.95 or entry_price <= 0.05:
                events.append(f"[{self.name}] SKIP extreme price ${entry_price:.2f} {title[:30]}")
                continue

            # ── LIVE: Execute real order via CLOB ──
            actual_shares = self.stake / entry_price if entry_price > 0 else 0
            actual_stake = self.stake
            live_result = None
            if self.is_live and token_id:
                try:
                    from scalper.live_client import buy_outcome
                    live_result = buy_outcome(
                        token_id=token_id,
                        price=entry_price,
                        size=self.stake,
                        asset=slug[:10],
                        side=outcome,
                    )
                    if not live_result:
                        events.append(f"[{self.name}] LIVE BUY FAILED {title[:35]}")
                        continue
                    # Use on-chain verified data
                    if isinstance(live_result, dict):
                        actual_shares = live_result.get("shares", actual_shares)
                        if "actual_entry_price" in live_result:
                            entry_price = live_result["actual_entry_price"]
                        if "actual_cost" in live_result:
                            actual_stake = live_result["actual_cost"]
                except Exception as e:
                    events.append(f"[{self.name}] LIVE BUY ERROR: {e}")
                    continue

            # ── Record trade ──
            trades = self.trades
            entry = {
                "id": _next_id(trades, self.address),
                "wallet": self.address[:14],
                "wallet_name": self.name,
                "slug": slug,
                "question": title[:100],
                "side": outcome,
                "token_id": token_id,
                "entry_price": round(entry_price, 4),
                "entry_source": entry_source,
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "stake": round(actual_stake, 2),
                "shares": round(actual_shares, 4),
                "original_size": round(size, 2),
                "original_price": round(price, 4),
                "status": "open",
                "mode": "LIVE" if (self.is_live and live_result) else "PAPER",
                "exit_price": None,
                "exit_time": None,
                "exit_reason": None,
                "pnl": None,
            }
            trades.append(entry)
            save_trades(self.address, trades)

            mode_icon = "LIVE" if self.is_live else "PAPER"
            msg = (f"[{self.name}] ** {mode_icon} BUY ** {outcome} @ ${entry_price:.3f} "
                   f"({entry_source}) | ${actual_stake:.2f} | {title[:35]}")
            events.append(msg)
            self.last_event = msg

        return events

    def check_resolutions(self) -> list[str]:
        """Check if any open positions have resolved."""
        events = []
        trades = self.trades
        changed = False

        for t in trades:
            if t.get("status") != "open":
                continue
            slug = t.get("slug", "")
            if not slug:
                continue

            res = _check_resolution(slug)
            if not res:
                continue

            side = t.get("side", "")
            won = (res["up"] > 0.9) if side in ("Yes", "UP") else (res["down"] > 0.9)
            pnl = ((1.0 - t["entry_price"]) * t.get("shares", 0)) if won else -t["stake"]
            t["status"] = "won" if won else "lost"
            t["exit_price"] = 1.0 if won else 0.0
            t["exit_reason"] = "resolution"
            t["exit_time"] = datetime.now(timezone.utc).isoformat()
            t["pnl"] = round(pnl, 2)
            changed = True

            icon = "WIN" if won else "LOSS"
            msg = f"[{self.name}] {icon} P&L ${pnl:+.2f} | {t['question'][:40]}"
            events.append(msg)
            self.last_event = msg

        if changed:
            save_trades(self.address, trades)
        return events


# ══════════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════════

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def render_dashboard(trackers: list[WalletTracker], event_log: list[str], cycle: int, poll_interval: float):
    """Render the full dashboard to console."""
    clear_screen()
    now = datetime.now().strftime("%H:%M:%S")

    # ── Header ──
    total_pnl = sum(t.total_pnl for t in trackers)
    total_open = sum(len(t.open_trades) for t in trackers)
    total_resolved = sum(len(t.resolved_trades) for t in trackers)
    cap_each = trackers[0].capital if trackers else 0
    stake_each = trackers[0].stake if trackers else 0

    print(f"  POLYMARKET COPY FLEET              {now}  |  Cycle #{cycle}")
    print(f"  {len(trackers)} wallets  |  ${cap_each:.0f}/wallet  |  ${stake_each:.0f}/trade  |  "
          f"Open: {total_open}  |  Resolved: {total_resolved}  |  "
          f"Fleet P&L: ${total_pnl:+.2f}")
    print("=" * 95)

    # ── Leaderboard (sorted by P&L) ──
    sorted_trackers = sorted(trackers, key=lambda t: t.total_pnl, reverse=True)
    header = (f"  {'#':>2} {'WALLET':<16} {'CAT':<6} {'REF WR':>6} "
              f"{'OPEN':>4} {'RES':>4} {'OUR WR':>6} {'P&L':>9} "
              f"{'EXP':>6} {'AVAIL':>6} {'ROI':>6}")
    print(header)
    print("-" * 95)

    for i, tr in enumerate(sorted_trackers, 1):
        pnl = tr.total_pnl
        pnl_str = f"${pnl:+.2f}" if tr.resolved_trades else "--"
        our_wr = f"{tr.win_rate:.0f}%" if tr.resolved_trades else "--"
        ref_wr = f"{tr.wr:.0f}%"
        roi = (pnl / tr.capital * 100) if tr.capital > 0 and tr.resolved_trades else 0
        roi_str = f"{roi:+.1f}%" if tr.resolved_trades else "--"
        medal = ["  ", "  ", "  ", "  ", "  "]
        if i == 1 and pnl > 0:
            medal[0] = " *"

        print(
            f"  {i:>2} {tr.name:<16} {tr.cat:<6} {ref_wr:>6} "
            f"{len(tr.open_trades):>4} {len(tr.resolved_trades):>4} "
            f"{our_wr:>6} {pnl_str:>9} "
            f"${tr.exposure:>5.0f} ${tr.available:>5.0f} {roi_str:>6}{medal[0] if i <= 1 else ''}"
        )

    # ── Open positions detail ──
    all_open = []
    for tr in trackers:
        for t in tr.open_trades:
            all_open.append((tr.name, t))

    if all_open:
        print(f"\n  OPEN POSITIONS ({len(all_open)})")
        print("-" * 95)
        for name, t in all_open[:20]:  # Show max 20 to avoid flooding
            q = t.get("question", "?")[:42]
            side = t.get("side", "?")
            entry = t.get("entry_price", 0)
            sk = t.get("stake", 0)
            age = ""
            try:
                et = datetime.fromisoformat(t["entry_time"].replace("Z", "+00:00"))
                mins = (datetime.now(timezone.utc) - et).total_seconds() / 60
                if mins < 60:
                    age = f"{mins:.0f}m"
                elif mins < 1440:
                    age = f"{mins/60:.1f}h"
                else:
                    age = f"{mins/1440:.0f}d"
            except Exception:
                pass
            print(f"    {name:<14} {side:<5} @ ${entry:.2f}  ${sk:.0f}  {age:>5}  {q}")
        if len(all_open) > 20:
            print(f"    ... and {len(all_open) - 20} more")

    # ── Event log (last 12 events) ──
    print(f"\n  ACTIVITY LOG")
    print("-" * 95)
    if event_log:
        for ev in event_log[-12:]:
            print(f"    {ev}")
    else:
        print("    Waiting for new transactions from tracked wallets...")

    # ── Footer ──
    print(f"\n  [Ctrl+C to stop]  |  Poll: {poll_interval:.0f}s  |  {len(trackers)} independent strategies")


# ══════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════

def run_fleet(
    wallets: list[dict],
    capital_per_wallet: float,
    stake: float,
    tp_pct: float,
    sl_pct: float,
    poll_interval: float,
    is_live: bool = False,
):
    setup_logging()
    logger = logging.getLogger("polybot.copy_fleet")

    # Initialize live client if requested
    if is_live:
        from scalper.live_client import init_live_client
        if not init_live_client(dry_run=False):
            print("  ** LIVE init failed. Check .env credentials. **")
            return
        print("  ** LIVE MODE ACTIVE - Real orders will be placed **")

    # Create trackers — each wallet gets FULL capital independently
    trackers = []
    for w in wallets:
        tr = WalletTracker(
            address=w["address"],
            name=w["name"],
            cat=w.get("cat", "?"),
            wr=w.get("wr", 0),
            capital=capital_per_wallet,
            stake=stake,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            is_live=is_live,
        )
        trackers.append(tr)

    session = requests.Session()
    event_log: list[str] = []
    cycle = 0

    # Keep last 100 events max
    MAX_LOG = 100

    # Initial render
    render_dashboard(trackers, event_log, cycle, poll_interval)

    while True:
        try:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")

            # ── Poll each wallet ──
            for tr in trackers:
                evts = tr.poll_and_copy(session)
                for e in evts:
                    event_log.append(f"{ts} {e}")

                # Check resolutions every 5 cycles
                if cycle % 5 == 0:
                    res_evts = tr.check_resolutions()
                    for e in res_evts:
                        event_log.append(f"{ts} {e}")

                time.sleep(0.5)

            # Trim log
            if len(event_log) > MAX_LOG:
                event_log = event_log[-MAX_LOG:]

            # ── Re-render dashboard ──
            render_dashboard(trackers, event_log, cycle, poll_interval)

            # ── Sleep ──
            time.sleep(poll_interval)

        except KeyboardInterrupt:
            clear_screen()
            print("\n  COPY FLEET — FINAL LEADERBOARD")
            print("=" * 75)
            sorted_t = sorted(trackers, key=lambda t: t.total_pnl, reverse=True)
            for i, tr in enumerate(sorted_t, 1):
                wr = f"{tr.win_rate:.0f}%" if tr.resolved_trades else "--"
                roi = (tr.total_pnl / tr.capital * 100) if tr.resolved_trades else 0
                roi_s = f"{roi:+.1f}%" if tr.resolved_trades else "--"
                print(
                    f"  #{i} {tr.name:<16} | "
                    f"Open: {len(tr.open_trades):>3} | "
                    f"Resolved: {len(tr.resolved_trades):>3} | "
                    f"WR: {wr:>4} | "
                    f"P&L: ${tr.total_pnl:+.2f} | "
                    f"ROI: {roi_s}"
                )
            total = sum(t.total_pnl for t in trackers)
            print(f"\n  FLEET TOTAL P&L: ${total:+.2f}")
            print(f"  Files saved in: data/trades/copy_*.json")
            return
        except Exception as e:
            logger.error("Fleet loop error: %s", e)
            time.sleep(poll_interval)


def run_single(target: str, capital: float, stake: float,
               tp_pct: float, sl_pct: float, poll_interval: float, is_live: bool = False):
    """Run copy bot for a single wallet."""
    wallet = {"address": target, "name": target[:10], "cat": "?", "wr": 0}
    for fw in FLEET_WALLETS:
        if fw["address"].lower() == target.lower():
            wallet = fw
            break
    run_fleet([wallet], capital, stake, tp_pct, sl_pct, poll_interval, is_live=is_live)


# ══════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Polymarket Copy Bot - Track and copy wallet trades",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python copy_wallet.py --fleet                     # 5 wallets, $40 EACH
  python copy_wallet.py --fleet --capital 100       # 5 wallets, $100 EACH
  python copy_wallet.py --target 0x5d0f03cf...      # Single wallet, $40
        """,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fleet", action="store_true", help="Run all 5 tracked wallets")
    mode.add_argument("--target", type=str, help="Single wallet address to copy")

    parser.add_argument("--capital", type=float, default=40.0,
                        help="Capital PER WALLET (each gets this amount independently)")
    parser.add_argument("--stake", type=float, default=0,
                        help="Stake per trade (default: capital/10)")
    parser.add_argument("--tp", type=float, default=0.50, help="Take profit (0.50 = 50%%)")
    parser.add_argument("--sl", type=float, default=0.25, help="Stop loss (0.25 = 25%%)")
    parser.add_argument("--poll", type=float, default=30, help="Poll interval in seconds")
    parser.add_argument("--live", action="store_true",
                        help="Execute REAL trades on CLOB (requires .env credentials)")

    args = parser.parse_args()

    # Auto-stake: capital/10 allows ~10 concurrent positions
    stake = args.stake if args.stake > 0 else max(1.0, round(args.capital / 10, 1))

    if args.live:
        print("\n  !! LIVE MODE !! Real money will be used. Ctrl+C within 5s to cancel.")
        time.sleep(5)

    if args.fleet:
        run_fleet(
            wallets=FLEET_WALLETS,
            capital_per_wallet=args.capital,
            stake=stake,
            tp_pct=args.tp,
            sl_pct=args.sl,
            poll_interval=args.poll,
            is_live=args.live,
        )
    else:
        run_single(
            target=args.target,
            capital=args.capital,
            stake=stake,
            tp_pct=args.tp,
            sl_pct=args.sl,
            poll_interval=args.poll,
            is_live=args.live,
        )
