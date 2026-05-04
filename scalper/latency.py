"""
scalper/latency.py — Latency profiler for HFT pipeline diagnostics.

Tracks millisecond-level delays across the entire trading pipeline:
  1. Orderbook WS (Polymarket → Bot)  — how stale our bid/ask data is
  2. Tick Data WS  (Binance → Bot)    — how stale our price signals are
  3. Order Execution (Bot → Polymarket CLOB) — roundtrip for FAK orders

Thread-safe — all sources write from their respective WS/API threads,
the runner reads from the main loop.
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("polybot.scalper.latency")

# Rolling window sizes
_WINDOW_SIZE = 50   # Keep last 50 samples per source


# ═══════════════════════════════════════════════════════════════
# Internal State
# ═══════════════════════════════════════════════════════════════

@dataclass
class _LatencyBuffer:
    """Thread-safe rolling buffer of latency samples (in ms)."""
    samples: deque = field(default_factory=lambda: deque(maxlen=_WINDOW_SIZE))
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_sample: float = 0.0   # most recent latency in ms
    sample_count: int = 0

    def record(self, latency_ms: float):
        """Record a latency sample (in milliseconds)."""
        with self.lock:
            self.samples.append(latency_ms)
            self.last_sample = latency_ms
            self.sample_count += 1

    def get_stats(self) -> dict | None:
        """Get min/avg/max/last stats. Returns None if no samples."""
        with self.lock:
            if not self.samples:
                return None
            samples = list(self.samples)

        return {
            "min_ms": round(min(samples), 1),
            "avg_ms": round(sum(samples) / len(samples), 1),
            "max_ms": round(max(samples), 1),
            "last_ms": round(samples[-1], 1),
            "count": len(samples),
            "total_count": self.sample_count,
        }


# Global latency buffers per source
_buffers: dict[str, _LatencyBuffer] = {
    "polymarket_ws": _LatencyBuffer(),   # Orderbook WS messages
    "binance_ws": _LatencyBuffer(),      # aggTrade WS messages
    "order_exec": _LatencyBuffer(),      # post_order roundtrip
}

# Track inter-message intervals for WS sources
_last_msg_time: dict[str, float] = {}
_msg_interval_buffers: dict[str, _LatencyBuffer] = {
    "polymarket_ws": _LatencyBuffer(),
    "binance_ws": _LatencyBuffer(),
}


# ═══════════════════════════════════════════════════════════════
# Public Recording API (called from WS parsers, live_client, etc.)
# ═══════════════════════════════════════════════════════════════


def record_polymarket_ws(server_timestamp_ms: int | None = None):
    """
    Record latency for a Polymarket WS message.

    Uses server timestamp if available, otherwise records inter-message
    interval to show WS freshness.

    Args:
        server_timestamp_ms: The 'timestamp' field from the WS message
                            (unix ms when Polymarket created the event).
                            Pass None if not available.
    """
    now = time.time()
    now_ms = now * 1000

    # Method 1: Server timestamp → true latency
    if server_timestamp_ms and server_timestamp_ms > 0:
        latency_ms = now_ms - server_timestamp_ms
        if -500 < latency_ms < 30_000:
            _buffers["polymarket_ws"].record(latency_ms)
            _last_msg_time["polymarket_ws"] = now
            return

    # Method 2: Inter-message interval (shows WS is alive + freshness)
    last = _last_msg_time.get("polymarket_ws")
    if last:
        interval_ms = (now - last) * 1000
        _msg_interval_buffers["polymarket_ws"].record(interval_ms)
    _last_msg_time["polymarket_ws"] = now


def record_binance_ws(trade_timestamp_ms: int):
    """
    Record latency for a Binance aggTrade WS message.

    Args:
        trade_timestamp_ms: The 'T' field from the aggTrade message
                           (unix ms when Binance executed the trade).
    """
    if trade_timestamp_ms <= 0:
        return
    now_ms = time.time() * 1000
    latency_ms = now_ms - trade_timestamp_ms
    if -500 < latency_ms < 30_000:
        _buffers["binance_ws"].record(latency_ms)


def record_order_exec(elapsed_ms: float):
    """
    Record roundtrip time for a CLOB order (post_order call).

    Args:
        elapsed_ms: time.perf_counter() delta in milliseconds.
    """
    if 0 < elapsed_ms < 60_000:
        _buffers["order_exec"].record(elapsed_ms)


# ═══════════════════════════════════════════════════════════════
# Public Read API (called from display/runner)
# ═══════════════════════════════════════════════════════════════


def get_all_stats() -> dict[str, dict | None]:
    """
    Get latency statistics for all tracked sources.

    Returns dict keyed by source name, values are stat dicts or None.
    """
    return {name: buf.get_stats() for name, buf in _buffers.items()}


def format_latency_display() -> str:
    """
    Format a compact latency diagnostics block for terminal display.

    Returns a multi-line string ready for printing.
    """
    stats = get_all_stats()
    SEP = "-" * 60
    DIV = " | "
    lines = []
    lines.append("  LATENCY DIAGNOSTICS")
    lines.append(f"  {SEP}")

    # Polymarket WS
    poly = stats.get("polymarket_ws")
    poly_interval = _msg_interval_buffers["polymarket_ws"].get_stats()
    if poly:
        tag = _latency_indicator(poly["avg_ms"], thresholds=(100, 500))
        lines.append(
            f"  {tag} Orderbook WS (Polymarket): "
            f"avg {poly['avg_ms']:.0f}ms{DIV}"
            f"last {poly['last_ms']:.0f}ms{DIV}"
            f"range [{poly['min_ms']:.0f}-{poly['max_ms']:.0f}ms]{DIV}"
            f"n={poly['count']}"
        )
    elif poly_interval:
        last_msg = _last_msg_time.get("polymarket_ws", 0)
        staleness = (time.time() - last_msg) * 1000 if last_msg else 0
        tag = _latency_indicator(poly_interval["avg_ms"], thresholds=(2000, 10000))
        lines.append(
            f"  {tag} Orderbook WS (Polymarket): "
            f"msg every {poly_interval['avg_ms']:.0f}ms{DIV}"
            f"stale {staleness:.0f}ms{DIV}"
            f"n={poly_interval['total_count']} msgs"
        )
    else:
        lines.append("  [ ] Orderbook WS (Polymarket): no data yet")

    # Binance WS
    binance = stats.get("binance_ws")
    if binance:
        tag = _latency_indicator(binance["avg_ms"], thresholds=(50, 200))
        lines.append(
            f"  {tag} Tick Data WS (Binance)   : "
            f"avg {binance['avg_ms']:.0f}ms{DIV}"
            f"last {binance['last_ms']:.0f}ms{DIV}"
            f"range [{binance['min_ms']:.0f}-{binance['max_ms']:.0f}ms]{DIV}"
            f"n={binance['count']}"
        )
    else:
        lines.append("  [ ] Tick Data WS (Binance)   : no data (V4 only)")

    # Order execution
    order = stats.get("order_exec")
    if order:
        tag = _latency_indicator(order["avg_ms"], thresholds=(200, 800))
        lines.append(
            f"  {tag} Order Execution (REST)   : "
            f"avg {order['avg_ms']:.0f}ms{DIV}"
            f"last {order['last_ms']:.0f}ms{DIV}"
            f"range [{order['min_ms']:.0f}-{order['max_ms']:.0f}ms]{DIV}"
            f"n={order['total_count']} total"
        )
    else:
        lines.append("  [ ] Order Execution (REST)   : no orders yet")

    # WS connection status
    try:
        from scalper.orderbook_ws import get_status
        ws_st = get_status()
        if ws_st["running"]:
            lines.append(
                f"  [*] WS Status               : "
                f"tracking {ws_st['subscribed']} tokens{DIV}"
                f"{ws_st['active_books']} books{DIV}"
                f"{ws_st['active_prices']} prices"
            )
    except ImportError:
        pass

    lines.append(f"  {SEP}")
    return "\n".join(lines)


def _latency_indicator(avg_ms: float, thresholds: tuple[float, float] = (100, 500)) -> str:
    """Return a text tag based on latency severity."""
    good, bad = thresholds
    if avg_ms <= good:
        return "[OK]"    # Good
    elif avg_ms <= bad:
        return "[!!]"    # Acceptable
    else:
        return "[XX]"    # Bad

