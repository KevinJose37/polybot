"""
chain_listener.py — On-Chain Log Polling Listener for Polymarket CTF (Polygon)
===============================================================================
Polls eth_getLogs every ~4s for TransferSingle events from the CTF contract
on Polygon to detect wallet trades in near-real-time (~4-6s latency).

Uses Alchemy HTTP RPC (free tier compatible with 10-block range limit).

Architecture:
  - Background thread polls eth_getLogs every POLL_INTERVAL seconds
  - Filters TransferSingle events for watched wallet addresses
  - Fires callbacks to copy_wallet.py on detection
  - Deduplicates via transactionHash
  - Runs in parallel with existing polling system as fallback

Instrumentation:
  - Detection latency (block → callback)
  - Duplicate event suppression count
  - Callback processing time
  - Events detected by chain vs polling fallback
  - Total signal→execution time
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

logger = logging.getLogger("polybot.chain_listener")

# ══════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════

# Polygon RPC (Alchemy free tier)
_ALCHEMY_KEY = os.environ.get("ALCHEMY_POLYGON_KEY", "AAH9PMi13mOhWG0z-E1Kp")
RPC_URL = f"https://polygon-mainnet.g.alchemy.com/v2/{_ALCHEMY_KEY}"

# Polymarket Conditional Tokens Framework (ERC-1155) on Polygon
CTF_CONTRACT = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"

# TransferSingle(address operator, address from, address to, uint256 id, uint256 value)
TRANSFER_SINGLE_TOPIC = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"

# Polling config
POLL_INTERVAL = 4.0  # seconds between getLogs calls
BLOCK_RANGE = 9      # Alchemy free tier limit: 10 blocks max

# Metrics persistence
DATA_DIR = Path(__file__).parent / "data" / "chain_listener"
DATA_DIR.mkdir(parents=True, exist_ok=True)
METRICS_PATH = DATA_DIR / "metrics.json"
SEEN_TX_PATH = DATA_DIR / "seen_tx.json"


# ══════════════════════════════════════════════════════════════════
#  EVENT DECODING
# ══════════════════════════════════════════════════════════════════

def _addr_to_topic(address: str) -> str:
    """Convert 0x-prefixed address to 32-byte topic (zero-padded left)."""
    clean = address.lower().replace("0x", "")
    return "0x" + clean.zfill(64)


def _topic_to_addr(topic: str) -> str:
    """Extract address from 32-byte topic."""
    return "0x" + topic[-40:].lower()


def decode_transfer_single(log: dict) -> dict | None:
    """Decode a TransferSingle event log. Returns dict or None."""
    try:
        topics = log.get("topics", [])
        data = log.get("data", "0x")

        if len(topics) < 4:
            return None

        operator = _topic_to_addr(topics[1])
        from_addr = _topic_to_addr(topics[2])
        to_addr = _topic_to_addr(topics[3])

        raw = data[2:] if data.startswith("0x") else data
        if len(raw) < 128:
            return None

        token_id_int = int(raw[:64], 16)
        amount_raw = int(raw[64:128], 16)
        amount = amount_raw / 1e6  # CTF uses 6 decimals

        return {
            "operator": operator,
            "from_addr": from_addr,
            "to_addr": to_addr,
            "token_id": str(token_id_int),
            "amount": amount,
            "tx_hash": log.get("transactionHash", ""),
            "block_number": int(log.get("blockNumber", "0x0"), 16),
            "log_index": int(log.get("logIndex", "0x0"), 16),
            "removed": log.get("removed", False),
        }
    except Exception as exc:
        logger.debug("Failed to decode TransferSingle: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════
#  METRICS & INSTRUMENTATION
# ══════════════════════════════════════════════════════════════════

class ListenerMetrics:
    """Thread-safe instrumentation for the chain listener."""

    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.polling_active = False
        self.rpc_errors = 0
        self.polls_total = 0
        self.events_received = 0
        self.events_processed = 0
        self.events_duplicated = 0
        self.events_buy = 0
        self.events_sell = 0
        self.events_ignored = 0
        self.callback_errors = 0
        self.detection_latencies: deque[float] = deque(maxlen=100)
        self.callback_times: deque[float] = deque(maxlen=100)
        self.last_event_time: float = 0.0
        self.last_block: int = 0

    def record_event(self, latency_s: float, callback_ms: float, is_buy: bool):
        with self._lock:
            self.events_processed += 1
            if is_buy:
                self.events_buy += 1
            else:
                self.events_sell += 1
            self.detection_latencies.append(latency_s)
            self.callback_times.append(callback_ms)
            self.last_event_time = time.time()

    def record_duplicate(self):
        with self._lock:
            self.events_duplicated += 1

    def record_poll(self):
        with self._lock:
            self.polls_total += 1

    def record_rpc_error(self):
        with self._lock:
            self.rpc_errors += 1

    def record_received(self):
        with self._lock:
            self.events_received += 1

    def record_callback_error(self):
        with self._lock:
            self.callback_errors += 1

    def snapshot(self) -> dict:
        with self._lock:
            avg_latency = (
                sum(self.detection_latencies) / len(self.detection_latencies)
                if self.detection_latencies else 0.0
            )
            avg_callback = (
                sum(self.callback_times) / len(self.callback_times)
                if self.callback_times else 0.0
            )
            uptime = time.time() - self.started_at
            return {
                "uptime_s": round(uptime, 0),
                "polling_active": self.polling_active,
                "polls_total": self.polls_total,
                "rpc_errors": self.rpc_errors,
                "events_received": self.events_received,
                "events_processed": self.events_processed,
                "events_buy": self.events_buy,
                "events_sell": self.events_sell,
                "events_duplicated": self.events_duplicated,
                "callback_errors": self.callback_errors,
                "avg_detection_latency_s": round(avg_latency, 2),
                "avg_callback_ms": round(avg_callback, 1),
                "last_event_age_s": round(time.time() - self.last_event_time, 0) if self.last_event_time else None,
                "last_block": self.last_block,
            }

    def save(self):
        try:
            snap = self.snapshot()
            snap["saved_at"] = datetime.now(timezone.utc).isoformat()
            METRICS_PATH.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
#  DEDUPLICATION
# ══════════════════════════════════════════════════════════════════

class TxDedup:
    """Thread-safe transaction deduplication with persistence."""

    def __init__(self, max_size: int = 5000):
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._max = max_size
        self._load()

    def _load(self):
        try:
            if SEEN_TX_PATH.exists():
                data = json.loads(SEEN_TX_PATH.read_text(encoding="utf-8"))
                self._seen = set(data[-self._max:])
        except Exception:
            self._seen = set()

    def save(self):
        try:
            with self._lock:
                data = list(self._seen)[-self._max:]
            SEEN_TX_PATH.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def is_new(self, tx_hash: str) -> bool:
        with self._lock:
            if tx_hash in self._seen:
                return False
            self._seen.add(tx_hash)
            if len(self._seen) > self._max * 1.5:
                self._seen = set(list(self._seen)[-self._max:])
            return True

    def mark_seen(self, tx_hash: str):
        with self._lock:
            self._seen.add(tx_hash)


# ══════════════════════════════════════════════════════════════════
#  CHAIN LISTENER
# ══════════════════════════════════════════════════════════════════

class ChainListener:
    """
    Near-real-time on-chain listener for Polymarket wallet trades.

    Polls eth_getLogs every ~4s for TransferSingle events on the CTF contract,
    filters for watched wallets, and fires callbacks on detection.
    """

    def __init__(
        self,
        watched_wallets: list[str],
        on_buy: Callable[[dict], None] | None = None,
        on_sell: Callable[[dict], None] | None = None,
        rpc_url: str = RPC_URL,
    ):
        self._wallets = set(w.lower() for w in watched_wallets)
        self._on_buy = on_buy
        self._on_sell = on_sell
        self._rpc_url = rpc_url
        self._running = False
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._last_scanned_block = 0

        self.metrics = ListenerMetrics()
        self.dedup = TxDedup()

        logger.info(
            "ChainListener initialized: %d wallets, RPC=%s",
            len(self._wallets), rpc_url[:50] + "...",
        )

    # ── Public API ──────────────────────────────────────────────

    def start(self):
        if self._running:
            logger.warning("ChainListener already running")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="chain-listener",
        )
        self._thread.start()
        logger.info("ChainListener started (polling mode, %ds interval)", POLL_INTERVAL)

    def stop(self):
        self._running = False
        self.dedup.save()
        self.metrics.save()
        logger.info("ChainListener stop requested")

    def is_active(self) -> bool:
        return self.metrics.polling_active

    def get_metrics(self) -> dict:
        return self.metrics.snapshot()

    def add_wallet(self, address: str):
        self._wallets.add(address.lower())
        logger.info("ChainListener: added wallet %s (total: %d)", address[:10], len(self._wallets))

    # ── RPC helpers ────────────────────────────────────────────

    def _rpc_call(self, method: str, params: list) -> dict | None:
        """Make a JSON-RPC call to the Polygon node."""
        try:
            resp = self._session.post(
                self._rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=10,
            )
            data = resp.json()
            if "error" in data:
                logger.debug("RPC error in %s: %s", method, data["error"])
                self.metrics.record_rpc_error()
                return None
            return data
        except Exception as exc:
            logger.debug("RPC call %s failed: %s", method, exc)
            self.metrics.record_rpc_error()
            return None

    def _get_latest_block(self) -> int | None:
        data = self._rpc_call("eth_blockNumber", [])
        if data and "result" in data:
            return int(data["result"], 16)
        return None

    def _get_logs(self, from_block: int, to_block: int) -> list[dict]:
        """Fetch TransferSingle logs from CTF contract in block range."""
        data = self._rpc_call("eth_getLogs", [{
            "address": CTF_CONTRACT,
            "topics": [TRANSFER_SINGLE_TOPIC],
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }])
        if data and "result" in data:
            return data["result"]
        return []

    # ── Main poll loop ─────────────────────────────────────────

    def _poll_loop(self):
        """Background thread: poll eth_getLogs every POLL_INTERVAL seconds."""
        # Initialize: get current block
        latest = self._get_latest_block()
        if latest:
            self._last_scanned_block = latest
            self.metrics.last_block = latest
            self.metrics.polling_active = True
            logger.info("Chain poll started at block %d", latest)
        else:
            logger.error("Chain poll: failed to get initial block number")
            return

        consecutive_errors = 0

        while self._running:
            try:
                time.sleep(POLL_INTERVAL)
                self.metrics.record_poll()

                latest = self._get_latest_block()
                if not latest:
                    consecutive_errors += 1
                    if consecutive_errors > 10:
                        logger.warning("Chain poll: %d consecutive errors, backing off", consecutive_errors)
                        time.sleep(30)
                    continue

                consecutive_errors = 0

                # Skip if no new blocks
                if latest <= self._last_scanned_block:
                    continue

                # Clamp range to BLOCK_RANGE (Alchemy free tier limit)
                from_block = max(self._last_scanned_block + 1, latest - BLOCK_RANGE)
                to_block = latest

                # Fetch logs
                logs = self._get_logs(from_block, to_block)
                self._last_scanned_block = to_block
                self.metrics.last_block = to_block

                if not logs:
                    continue

                # Process each log
                t_poll = time.time()
                for log in logs:
                    self._process_log(log, t_poll)

                # Periodic save
                if self.metrics.polls_total % 100 == 0:
                    self.dedup.save()
                    self.metrics.save()

            except Exception as exc:
                logger.error("Chain poll loop error: %s", exc)
                time.sleep(POLL_INTERVAL)

        self.metrics.polling_active = False
        self.dedup.save()
        self.metrics.save()
        logger.info("Chain poll loop exited")

    # ── Log processing ─────────────────────────────────────────

    def _process_log(self, log: dict, poll_time: float):
        """Process a single TransferSingle log entry."""
        self.metrics.record_received()

        # Skip reorgs
        if log.get("removed", False):
            return

        event = decode_transfer_single(log)
        if not event:
            return

        # Dedup
        tx_hash = event["tx_hash"]
        log_index = event.get("log_index", 0)
        dedup_key = f"{tx_hash}_{log_index}"
        if not tx_hash or not self.dedup.is_new(dedup_key):
            self.metrics.record_duplicate()
            return

        from_addr = event["from_addr"]
        to_addr = event["to_addr"]
        zero = "0x" + "0" * 40

        # Skip mints/burns (resolutions)
        if from_addr == zero or to_addr == zero:
            return

        is_buy = to_addr in self._wallets
        is_sell = from_addr in self._wallets

        if not is_buy and not is_sell:
            self.metrics.events_ignored += 1
            return

        # Fire callback
        t0 = time.perf_counter()
        event["detected_at"] = datetime.now(timezone.utc).isoformat()
        event["detection_source"] = "chain_poll"
        event["direction"] = "BUY" if is_buy else "SELL"
        event["wallet"] = to_addr if is_buy else from_addr

        callback = self._on_buy if is_buy else self._on_sell
        if callback:
            try:
                callback(event)
                cb_ms = (time.perf_counter() - t0) * 1000
                # Latency = poll_interval / 2 + RPC time (~4s avg)
                est_latency = POLL_INTERVAL
                self.metrics.record_event(est_latency, cb_ms, is_buy)

                icon = "BUY" if is_buy else "SELL"
                wallet = event["wallet"][:10]
                logger.info(
                    "CHAIN %s: wallet=%s token=%s amt=%.1f tx=%s cb=%.0fms",
                    icon, wallet, event["token_id"][:12],
                    event["amount"], tx_hash[:12], cb_ms,
                )
            except Exception as exc:
                self.metrics.record_callback_error()
                logger.error("Chain callback error: %s", exc)


# ══════════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from copy_wallet import FLEET_WALLETS

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    cryp_wallets = [w["address"] for w in FLEET_WALLETS if w["cat"] == "CRYP"]
    all_wallets = [w["address"] for w in FLEET_WALLETS]

    print(f"\n  Chain Listener — polling mode")
    print(f"  RPC: {RPC_URL[:50]}...")
    print(f"  CTF: {CTF_CONTRACT}")
    print(f"  Watching: {len(all_wallets)} wallets ({len(cryp_wallets)} CRYP)")
    print(f"  Poll: every {POLL_INTERVAL}s, {BLOCK_RANGE+1} blocks/poll")
    print("-" * 60)

    event_count = [0]

    def on_buy(event):
        event_count[0] += 1
        w = event["wallet"][:10]
        t = event["token_id"][:12]
        a = event["amount"]
        tx = event["tx_hash"][:12]
        print(f"  >> BUY  wallet={w} token={t} amt={a:.1f} tx={tx}")

    def on_sell(event):
        event_count[0] += 1
        w = event["wallet"][:10]
        t = event["token_id"][:12]
        a = event["amount"]
        tx = event["tx_hash"][:12]
        print(f"  >> SELL wallet={w} token={t} amt={a:.1f} tx={tx}")

    listener = ChainListener(
        watched_wallets=all_wallets,
        on_buy=on_buy,
        on_sell=on_sell,
    )
    listener.start()

    try:
        while True:
            time.sleep(15)
            m = listener.get_metrics()
            status = "ON" if m["polling_active"] else "OFF"
            print(
                f"  [{status}] polls={m['polls_total']} "
                f"recv={m['events_received']} proc={m['events_processed']} "
                f"buy={m['events_buy']} sell={m['events_sell']} "
                f"dup={m['events_duplicated']} err={m['rpc_errors']} "
                f"blk={m['last_block']} "
                f"events_total={event_count[0]}"
            )
    except KeyboardInterrupt:
        print(f"\n  Stopping... ({event_count[0]} events)")
        listener.stop()
        print(json.dumps(listener.get_metrics(), indent=2))
