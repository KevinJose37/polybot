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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    # ── Original fleet (verified active) ──
    # {"address": "0x5d0f03cf1243a3e21262d6cf844795afd9fff0ad", "name": "EB99999",        "cat": "GEO",   "wr": 94.1},
    # {"address": "0xdb15fbbcc1a8d1cbe112f7a2d74f6f752f2314f1", "name": "memain",         "cat": "SPORT", "wr": 85.7},
    # {"address": "0xe7348e92f76c26e879a9d0c1ff37cdbc4a926a78", "name": "bobthetradoor",  "cat": "GEO",   "wr": 41.7},
    # {"address": "0xd7f85d0eb0fe0732ca38d9107ad0d4d01b1289e4", "name": "tdrhrhhd",       "cat": "POL",   "wr": 39.7},
    # {"address": "0x5490687ee61406afbb1fd887937fdbb7fe1cb051", "name": "snqwqkozmqoc",   "cat": "CRYP",  "wr": 84.2},
    # {"address": "0xed107a85a4585a381e48c7f7ca4144909e7dd2e5", "name": "bobe2",          "cat": "GEO",   "wr": 87.9},
    # ── Gravia top scorers (active <24h) ──
    # {"address": "0xae7f00473f325d2eda0813cee59006d48951d4fe", "name": "h00ch",          "cat": "CRYP",  "wr": 64.7},
    # {"address": "0x88c4919de76e526d55a32c1f8afb439dd1f1129a", "name": "QuietRisk",      "cat": "GEO",   "wr": 76.9},
    # {"address": "0xbef5ab169458cb47c5d233e067dff4447fad1c5a", "name": "StoneMarble",    "cat": "GEO",   "wr": 58.3},
    # {"address": "0xa80e3fe5e7a445fa047fe6de1e27f9a15217b94b", "name": "bin8888",         "cat": "FIN",   "wr": 70.0},
    # {"address": "0x2974bd0059e48f215c391882976e0f1b4c8c9c23", "name": "65765757",       "cat": "GEO",   "wr": 80.1},
    # ── Gravia recent (<7d) ──
    # {"address": "0xf6891d5f12873776e4dc7c38fe586219a09b9d83", "name": "hhhhhcgg",       "cat": "SPORT", "wr": 80.0},
    # {"address": "0x7f9e2d1df78614564a70becc7fa14aa9a6623a0e", "name": "nojnn",          "cat": "GEO",   "wr": 74.2},
    # ── Active CRYP wallets ──
    # {"address": "0x55e2436d747835c7e40b0c6cf92f632bf1215fc9", "name": "Rhabarber",      "cat": "CRYP",  "wr": 50.0},
    {"address": "0x89b5cdaaa4866c1e738406712012a630b4078beb", "name": "ohanism",        "cat": "CRYP",  "wr": 55.1},
    # {"address": "0x101888282092fb5be3764b1c615200b2f14a23fe", "name": "OhioOhio",       "cat": "CRYP",  "wr": 50.0},
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
#  REALISTIC FILL SIMULATION (VWAP book-walk)
# ══════════════════════════════════════════════════════════════════

_CLOB_BOOK_URL = "https://clob.polymarket.com/book"


def _simulate_fill(asks: list, order_usd: float) -> dict:
    """
    Walk the ask side of the book to calculate a realistic fill.
    Returns dict with vwap, filled_usd, filled_shares, levels_consumed, fully_filled.
    """
    sorted_asks = sorted(asks, key=lambda a: float(a.get("price", 0)))
    filled_usd = 0.0
    filled_shares = 0.0
    levels_consumed = 0

    for level in sorted_asks:
        px = float(level.get("price", 0))
        sz = float(level.get("size", 0))
        if px <= 0 or sz <= 0:
            continue
        available_usd = px * sz
        remaining = order_usd - filled_usd
        levels_consumed += 1

        if available_usd >= remaining:
            shares_here = remaining / px
            filled_shares += shares_here
            filled_usd += remaining
            break
        else:
            filled_shares += sz
            filled_usd += available_usd

    vwap = round(filled_usd / filled_shares, 6) if filled_shares > 0 else 0.0
    return {
        "vwap": vwap,
        "filled_usd": round(filled_usd, 4),
        "filled_shares": round(filled_shares, 4),
        "levels_consumed": levels_consumed,
        "fully_filled": filled_usd >= order_usd * 0.95,
    }


def _get_live_mid(token_id: str) -> float | None:
    """
    Fetch current mid price from REST book for TP/SL monitoring.
    Returns mid price or None if unavailable.
    """
    try:
        resp = requests.get(_CLOB_BOOK_URL, params={"token_id": token_id}, timeout=3)
        if resp.status_code != 200:
            return None
        data = resp.json()
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not bids or not asks:
            return None
        best_bid = max(float(b.get("price", 0)) for b in bids)
        best_ask = min(float(a.get("price", 0)) for a in asks)
        if best_bid <= 0 or best_ask <= 0:
            return None
        return round((best_bid + best_ask) / 2, 4)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  WALLET TRACKER STATE
# ══════════════════════════════════════════════════════════════════

class WalletTracker:
    """Tracks one wallet: its trades, capital, seen txs."""

    # Category-based poll intervals (seconds)
    _POLL_INTERVALS = {"CRYP": 10.0, "SPORT": 30.0, "GEO": 30.0, "POL": 30.0, "FIN": 30.0}

    def __init__(self, address: str, name: str, cat: str, wr: float,
                 capital: float, stake: float,
                 tp_pct: float, sl_pct: float, is_live: bool = False):
        self.address = address
        self.name = name
        self.cat = cat
        self.wr = wr
        self.capital = capital  # In live mode: max budget cap; in paper: simulated capital
        self.stake = stake
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.is_live = is_live
        self.seen = load_seen(address)
        self.start_ts = int(time.time())
        self.last_event = ""
        self.polls = 0
        self.skipped_no_liq = 0  # Tracks how many trades we skipped (realism)
        # Per-wallet session (avoids connection pool contention in threads)
        self._session = requests.Session()
        # Category-based poll interval
        self.poll_interval = self._POLL_INTERVALS.get(cat, 30.0)
        self.next_poll_at = 0.0  # Poll immediately on first cycle
        # Live balance cache (refreshed every 30s)
        self._live_balance: float | None = None
        self._cold_started = False  # Will pre-seed seen on first poll
        self._live_balance_ts: float = 0.0

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
        if self.is_live:
            # In live mode: use --capital as budget cap
            # Real USDC balance is enforced at order execution level
            return self.capital - self.exposure
        # Paper mode: simulated capital + earned P&L - exposure
        return self.capital + self.total_pnl - self.exposure

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

    def poll_and_copy(self, session: requests.Session | None = None) -> list[str]:
        """Poll wallet for new trades, copy any BUYs. Returns list of event strings."""
        events = []
        self.polls += 1
        s = session or self._session

        try:
            resp = s.get(
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

        # ── Cold start: on first poll, pre-seed seen set ──
        # This prevents copying the entire history when starting fresh
        if not self._cold_started:
            self._cold_started = True
            pre_count = len(self.seen)
            for t in api_trades:
                tx = t.get("transactionHash", "")
                if tx:
                    self.seen.add(tx)
            new_marked = len(self.seen) - pre_count
            if new_marked > 0:
                save_seen(self.address, self.seen)
                events.append(f"[{self.name}] Cold start: marked {new_marked} existing txs as seen")
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
            if self.available < self.stake:
                events.append(f"[{self.name}] SKIP no capital (${self.available:.0f}) {title[:30]}")
                continue

            # ── Duplicate check ──
            if any(tr.get("slug") == slug and tr.get("side") == outcome for tr in self.open_trades):
                continue

            # ── Signal delay tracking ──
            now_ts = int(time.time())
            signal_delay_s = now_ts - trade_ts if trade_ts > 0 else 0

            # ── REALISTIC: Verify orderbook + VWAP fill simulation ──
            entry_price = price  # Default to API price
            entry_source = "API"
            fill_meta = {}  # Execution quality metrics
            if token_id:
                try:
                    from scalper.live_client import _fetch_rest_book
                    t0_book = time.perf_counter()
                    book = _fetch_rest_book(token_id)
                    book_latency_ms = (time.perf_counter() - t0_book) * 1000

                    if book:
                        bids = book.get("bids", [])
                        asks = book.get("asks", [])
                        if bids and asks:
                            sorted_asks = sorted(asks, key=lambda a: float(a.get("price", 0)))
                            sorted_bids = sorted(bids, key=lambda b: float(b.get("price", 0)), reverse=True)
                            best_ask = float(sorted_asks[0]["price"])
                            best_bid = float(sorted_bids[0]["price"])
                            spread = round(best_ask - best_bid, 4)

                            # Total book depth in USD (top 10 ask levels)
                            total_depth_usd = sum(
                                float(a.get("price", 0)) * float(a.get("size", 0))
                                for a in sorted_asks[:10]
                            )

                            # ── Spread gate ──
                            if spread > 0.08:
                                self.skipped_no_liq += 1
                                events.append(f"[{self.name}] SKIP spread ${spread:.3f} {title[:30]}")
                                continue

                            # ── Depth gate: book must absorb 150% of order ──
                            if total_depth_usd < self.stake * 1.5:
                                self.skipped_no_liq += 1
                                events.append(f"[{self.name}] SKIP depth ${total_depth_usd:.0f}<${self.stake*1.5:.0f} {title[:25]}")
                                continue

                            # ── VWAP book-walk: simulate realistic fill ──
                            sim = _simulate_fill(asks, self.stake)
                            if not sim["fully_filled"]:
                                self.skipped_no_liq += 1
                                events.append(
                                    f"[{self.name}] SKIP partial fill "
                                    f"(${sim['filled_usd']:.1f}/${self.stake:.0f}) {title[:25]}"
                                )
                                continue

                            # Use VWAP as entry (accounts for depth/slippage)
                            entry_price = sim["vwap"]
                            slippage = round(sim["vwap"] - best_ask, 4)
                            entry_source = (
                                f"VWAP ${sim['vwap']:.4f} "
                                f"(ask=${best_ask:.3f} slip=${slippage:+.4f} "
                                f"lvl={sim['levels_consumed']} spr=${spread:.3f})"
                            )

                            # Execution quality metrics
                            fill_meta = {
                                "signal_delay_s": signal_delay_s,
                                "book_spread": spread,
                                "book_depth_usd": round(total_depth_usd, 2),
                                "best_ask": best_ask,
                                "best_bid": best_bid,
                                "vwap": sim["vwap"],
                                "slippage": slippage,
                                "levels_consumed": sim["levels_consumed"],
                                "book_latency_ms": round(book_latency_ms, 0),
                                "filled_shares": sim["filled_shares"],
                            }
                        else:
                            # One-sided book → use API price
                            entry_source = "API (one-sided book)"
                    else:
                        # Book unavailable (404 for resolved/sports)
                        entry_source = "API (no book)"
                        # Apply pessimistic adjustment for stale signal
                        if signal_delay_s > 120:
                            entry_price = round(price * 1.02, 4)
                            entry_source += f" +2% stale({signal_delay_s}s)"
                except Exception:
                    entry_source = "API (error)"

            # ── Sanity: don't buy at extremes ──
            if entry_price >= 0.95 or entry_price <= 0.05:
                events.append(f"[{self.name}] SKIP extreme price ${entry_price:.2f} {title[:30]}")
                continue

            # ── LIVE: Execute real order via CLOB ──
            # In paper mode, use VWAP shares from simulation
            actual_shares = fill_meta.get("filled_shares", self.stake / entry_price) if entry_price > 0 else 0
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
                        if live_result.get("already_held"):
                            events.append(f"[{self.name}] SKIP already holding {title[:30]}")
                            continue
                        actual_shares = live_result.get("shares", actual_shares)
                        if "actual_entry_price" in live_result:
                            entry_price = live_result["actual_entry_price"]
                        if "actual_cost" in live_result:
                            actual_stake = live_result["actual_cost"]
                            if actual_stake <= 0:
                                actual_stake = self.stake
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
                # Execution quality metrics
                "signal_delay_s": signal_delay_s,
                "fill_meta": fill_meta if fill_meta else None,
                # Exit fields
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

    def check_tp_sl(self) -> list[str]:
        """
        Monitor open positions for TP/SL exits using live book prices.
        TP/SL are based on the OUTCOME PRICE movement, not P&L percentage.

        For a BUY at entry_price:
          - TP triggers when mid_price >= entry_price + (1 - entry_price) * tp_pct
          - SL triggers when mid_price <= entry_price * (1 - sl_pct)

        Exit is simulated at best_bid (what you'd actually get selling).
        """
        events = []
        trades = self.trades
        changed = False

        for t in trades:
            if t.get("status") != "open":
                continue
            token_id = t.get("token_id", "")
            if not token_id:
                continue

            entry_px = t.get("entry_price", 0)
            if entry_px <= 0:
                continue

            # Calculate TP/SL thresholds
            upside = 1.0 - entry_px
            tp_target = entry_px + upside * self.tp_pct  # e.g. 0.64 + 0.36*0.5 = 0.82
            sl_target = entry_px * (1.0 - self.sl_pct)    # e.g. 0.64 * 0.75 = 0.48

            # Get live mid price from orderbook
            mid = _get_live_mid(token_id)
            if mid is None:
                continue

            exit_reason = None
            if mid >= tp_target:
                exit_reason = f"TP hit (mid=${mid:.3f} >= ${tp_target:.3f})"
            elif mid <= sl_target and self.cat != "CRYP":
                # SL disabled for CRYP — 5min binary markets have 30-50% swings
                # that resolve favorably. SL only active for slow markets (GEO/SPORT/etc)
                exit_reason = f"SL hit (mid=${mid:.3f} <= ${sl_target:.3f})"

            if not exit_reason:
                continue

            # Simulate exit at best_bid (realistic sell price)
            try:
                from scalper.live_client import _fetch_rest_book
                book = _fetch_rest_book(token_id)
                if book:
                    bids = book.get("bids", [])
                    if bids:
                        # Walk bid side for sell simulation
                        sorted_bids = sorted(bids, key=lambda b: float(b.get("price", 0)), reverse=True)
                        exit_price = float(sorted_bids[0]["price"])

                        # Simulate sell VWAP if we have enough shares
                        shares = t.get("shares", 0)
                        sell_usd = shares * exit_price
                        total_bid_depth = sum(
                            float(b.get("price", 0)) * float(b.get("size", 0))
                            for b in sorted_bids[:10]
                        )
                        # If book can't absorb our sell, use pessimistic exit
                        if total_bid_depth < sell_usd * 0.5:
                            exit_price *= 0.97  # 3% slippage penalty
                    else:
                        exit_price = mid * 0.98  # No bids, estimate
                else:
                    exit_price = mid * 0.98
            except Exception:
                exit_price = mid * 0.98

            # Calculate P&L
            pnl = (exit_price - entry_px) * t.get("shares", 0)

            # Execute live sell if in live mode
            if self.is_live and token_id:
                try:
                    from scalper.live_client import sell_outcome
                    sell_result = sell_outcome(
                        token_id=token_id,
                        price=exit_price,
                        size=t.get("shares", 0),
                        asset=t.get("slug", "")[:10],
                        side=t.get("side", ""),
                    )
                    if sell_result and isinstance(sell_result, dict):
                        actual_proceeds = sell_result.get("actual_proceeds", 0)
                        pnl = actual_proceeds - t.get("stake", 0)
                        exit_price = actual_proceeds / t.get("shares", 1) if t.get("shares", 0) > 0 else exit_price
                except Exception as e:
                    events.append(f"[{self.name}] LIVE SELL ERROR: {e}")
                    continue

            t["status"] = "sold"
            t["exit_price"] = round(exit_price, 4)
            t["exit_reason"] = exit_reason
            t["exit_time"] = datetime.now(timezone.utc).isoformat()
            t["pnl"] = round(pnl, 2)
            changed = True

            icon = "TP ✅" if "TP" in exit_reason else "SL ❌"
            msg = f"[{self.name}] {icon} P&L ${pnl:+.2f} exit@${exit_price:.3f} | {t['question'][:35]}"
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
    n_cryp = sum(1 for t in trackers if t.cat == "CRYP")
    n_other = len(trackers) - n_cryp
    print(f"\n  [Ctrl+C to stop]  |  CRYP: {n_cryp}×10s  |  Other: {n_other}×30s  |  Concurrent  |  {len(trackers)} strategies")


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
        from scalper.live_client import init_live_client, get_balance
        if not init_live_client(dry_run=False):
            print("  ** LIVE init failed. Check .env credentials. **")
            return
        real_bal = get_balance() or 0.0
        print(f"  ** LIVE MODE ACTIVE — Real orders will be placed **")
        print(f"  ** Wallet USDC: ${real_bal:.2f} | Budget cap: ${capital_per_wallet:.2f}/wallet | Stake: ${stake:.2f}/trade **")

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

    # ── Thread-safe event log ──
    event_log: list[str] = []
    _log_lock = threading.Lock()
    MAX_LOG = 100

    def _log_event(msg: str):
        """Thread-safe append to event log."""
        with _log_lock:
            event_log.append(msg)

    def _log_events(msgs: list[str]):
        """Thread-safe batch append."""
        if not msgs:
            return
        with _log_lock:
            event_log.extend(msgs)

    def _trim_log():
        """Thread-safe log trimming."""
        nonlocal event_log
        with _log_lock:
            if len(event_log) > MAX_LOG:
                event_log = event_log[-MAX_LOG:]

    def _snapshot_log() -> list[str]:
        """Thread-safe snapshot for rendering."""
        with _log_lock:
            return list(event_log)

    # ── Worker function for concurrent execution ──
    def _poll_worker(tr: WalletTracker, ts: str, cycle: int) -> list[str]:
        """Poll one wallet + TP/SL + resolutions. Returns events."""
        evts = []
        try:
            poll_evts = tr.poll_and_copy()  # Uses per-wallet session
            for e in poll_evts:
                evts.append(f"{ts} {e}")

            # Check TP/SL every 3 cycles
            if cycle % 3 == 0 and tr.open_trades:
                tpsl_evts = tr.check_tp_sl()
                for e in tpsl_evts:
                    evts.append(f"{ts} {e}")

            # Check resolutions every 5 cycles
            if cycle % 5 == 0:
                res_evts = tr.check_resolutions()
                for e in res_evts:
                    evts.append(f"{ts} {e}")

            # Schedule next poll
            tr.next_poll_at = time.time() + tr.poll_interval

        except Exception as exc:
            evts.append(f"{ts} [{tr.name}] Worker error: {exc}")
        return evts

    cycle = 0
    TICK_INTERVAL = 5.0  # Main loop tick (fast enough for 10s CRYP polls)
    MAX_WORKERS = 8  # Parallel poll threads

    # Initial render
    render_dashboard(trackers, event_log, cycle, poll_interval)

    while True:
        try:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")
            now = time.time()

            # ── Select wallets due for polling ──
            due = [tr for tr in trackers if now >= tr.next_poll_at]

            if due:
                # ── Concurrent polling with ThreadPoolExecutor ──
                t0_poll = time.perf_counter()
                with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(due))) as executor:
                    futures = {
                        executor.submit(_poll_worker, tr, ts, cycle): tr
                        for tr in due
                    }
                    try:
                        for future in as_completed(futures, timeout=45):
                            tr = futures[future]
                            try:
                                worker_evts = future.result()
                                _log_events(worker_evts)
                            except Exception as exc:
                                _log_event(f"{ts} [{tr.name}] Future error: {exc}")
                    except TimeoutError:
                        # Some futures timed out — collect what we can
                        timed_out = [tr.name for f, tr in futures.items() if not f.done()]
                        _log_event(f"{ts} [FLEET] Timeout: {', '.join(timed_out)}")

                poll_ms = (time.perf_counter() - t0_poll) * 1000
                if poll_ms > 15000:
                    _log_event(f"{ts} [FLEET] Slow poll: {len(due)} wallets in {poll_ms:.0f}ms")

            # Trim & render
            _trim_log()
            render_dashboard(trackers, _snapshot_log(), cycle, poll_interval)

            # ── Sleep until next tick ──
            time.sleep(TICK_INTERVAL)

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
            time.sleep(TICK_INTERVAL)


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
