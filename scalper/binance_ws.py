"""
scalper/binance_ws.py — Real-time Binance WebSocket tick manager.

Maintains persistent WebSocket connections to Binance aggTrade streams
for all configured assets. Provides sub-second price data to replace
1-minute kline lag.

Usage:
    manager = BinanceTickManager()
    manager.start()  # starts background threads
    ...
    ticks = manager.get_ticks("BTC", count=50)
    price = manager.get_current_price("BTC")
    ...
    manager.stop()
"""

import json
import logging
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import websocket  # websocket-client library

logger = logging.getLogger("polybot.scalper.binance_ws")

# ═══════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════

WS_BASE_URL = "wss://stream.binance.com:9443/ws"

# Map asset keys to Binance lowercase symbols
ASSET_WS_SYMBOLS = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
}

TICK_BUFFER_SIZE = 500   # Keep last 500 ticks per asset (~5-10 min)
RECONNECT_DELAY = 3      # Seconds before reconnect on disconnect
WARMUP_TICKS = 20        # Minimum ticks needed before signals are reliable


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class Tick:
    """Single aggregated trade from Binance."""
    timestamp: float      # Unix timestamp (seconds)
    price: float          # Trade price
    quantity: float       # Trade quantity
    is_buyer_maker: bool  # True = sell aggressor, False = buy aggressor


@dataclass
class AssetTickBuffer:
    """Thread-safe tick buffer for a single asset."""
    asset: str
    symbol: str
    ticks: deque = field(default_factory=lambda: deque(maxlen=TICK_BUFFER_SIZE))
    last_price: float = 0.0
    tick_count: int = 0
    connected: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_tick(self, tick: Tick):
        with self._lock:
            self.ticks.append(tick)
            self.last_price = tick.price
            self.tick_count += 1

    def get_ticks(self, count: int = 50) -> list[Tick]:
        with self._lock:
            return list(self.ticks)[-count:]

    def get_current_price(self) -> float:
        with self._lock:
            return self.last_price

    def is_warm(self) -> bool:
        """Check if buffer has enough ticks for reliable signals."""
        with self._lock:
            return len(self.ticks) >= WARMUP_TICKS


# ═══════════════════════════════════════════════════════════════
# WebSocket Manager — uses manual recv loop for Windows compat
# ═══════════════════════════════════════════════════════════════


class BinanceTickManager:
    """
    Manages persistent WebSocket connections to Binance aggTrade streams.

    Uses manual recv loop instead of WebSocketApp.run_forever() for
    reliable operation on Windows. Each asset runs in its own daemon
    thread with auto-reconnect.
    """

    def __init__(self, assets: dict | None = None):
        self._assets = assets or ASSET_WS_SYMBOLS
        self._buffers: dict[str, AssetTickBuffer] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._running = False

        for asset, symbol in self._assets.items():
            self._buffers[asset] = AssetTickBuffer(asset=asset, symbol=symbol)

    def start(self):
        """Start WebSocket connections for all assets."""
        if self._running:
            return

        self._running = True
        logger.info("Starting Binance WebSocket tick manager for %s",
                     list(self._assets.keys()))

        for asset, symbol in self._assets.items():
            thread = threading.Thread(
                target=self._run_ws,
                args=(asset, symbol),
                daemon=True,
                name=f"ws-{asset}",
            )
            self._threads[asset] = thread
            thread.start()

        print(f"  [WS] Connecting to {len(self._assets)} Binance streams...")

    def stop(self):
        """Stop all WebSocket connections."""
        self._running = False
        logger.info("Binance WebSocket manager stopped")

    def get_ticks(self, asset: str, count: int = 50) -> list[Tick]:
        """Get recent ticks for an asset. Thread-safe."""
        buf = self._buffers.get(asset)
        if not buf:
            return []
        return buf.get_ticks(count)

    def get_current_price(self, asset: str) -> float:
        """Get latest price for an asset. Thread-safe."""
        buf = self._buffers.get(asset)
        if not buf:
            return 0.0
        return buf.get_current_price()

    def is_warm(self, asset: str) -> bool:
        """Check if an asset has enough ticks for reliable signals."""
        buf = self._buffers.get(asset)
        if not buf:
            return False
        return buf.is_warm()

    def is_all_warm(self) -> bool:
        """Check if all assets have enough ticks."""
        return all(buf.is_warm() for buf in self._buffers.values())

    def get_warmup_status(self) -> dict[str, dict]:
        """Get warmup status for all assets."""
        status = {}
        for asset, buf in self._buffers.items():
            with buf._lock:
                tick_count = len(buf.ticks)
                status[asset] = {
                    "ticks": tick_count,
                    "needed": WARMUP_TICKS,
                    "warm": tick_count >= WARMUP_TICKS,
                    "connected": buf.connected,
                    "price": buf.last_price,
                }
        return status

    def _run_ws(self, asset: str, symbol: str):
        """
        Run WebSocket connection with manual recv loop and auto-reconnect.

        Uses websocket.create_connection() + manual recv instead of
        WebSocketApp.run_forever() for Windows compatibility.
        """
        url = f"{WS_BASE_URL}/{symbol}@aggTrade"
        buf = self._buffers[asset]

        while self._running:
            ws = None
            try:
                ws = websocket.create_connection(
                    url,
                    timeout=15,
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                )
                buf.connected = True
                logger.info("WS %s connected", asset)

                # Manual recv loop
                while self._running:
                    try:
                        ws.settimeout(5)  # 5s timeout for recv
                        raw = ws.recv()
                        data = json.loads(raw)

                        tick = Tick(
                            timestamp=data["T"] / 1000.0,
                            price=float(data["p"]),
                            quantity=float(data["q"]),
                            is_buyer_maker=data["m"],
                        )
                        buf.add_tick(tick)

                        # Record Binance WS latency
                        try:
                            from scalper.latency import record_binance_ws
                            record_binance_ws(data["T"])
                        except ImportError:
                            pass

                    except websocket.WebSocketTimeoutException:
                        continue  # No data in 5s, just retry
                    except (KeyError, ValueError, TypeError) as exc:
                        logger.debug("WS %s bad message: %s", asset, exc)

            except websocket.WebSocketException as exc:
                logger.warning("WS %s connection error: %s", asset, exc)
            except Exception as exc:
                logger.warning("WS %s error: %s", asset, exc)
            finally:
                buf.connected = False
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass

            if self._running:
                logger.info("WS %s reconnecting in %ds...", asset, RECONNECT_DELAY)
                time.sleep(RECONNECT_DELAY)
