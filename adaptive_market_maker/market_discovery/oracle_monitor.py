"""Oracle Monitor to track Chainlink update schedules and spot deviation."""

import asyncio
import aiohttp
import time
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger(__name__)



@dataclass  
class OracleMonitor:
    underlying: str             # "ETH", "BTC", "SOL"
    feed_address: str
    deviation_threshold: float
    oracle_pause_seconds: float
    rpc_url: str
    pause_cleared_after: float = 30.0
    heartbeat_seconds: int = 3600
    
    def __post_init__(self):
        self.pause_event = asyncio.Event()
        self._pause_triggered_at: float | None = None
        self._last_binance_spot: float = 0.0
        self.last_chainlink_price: float = 0.0
        self.last_update_timestamp: int = 0
        self._running: bool = False
        self._tasks: set[asyncio.Task] = set()

    async def start_polling(self) -> None:
        if not self.feed_address: return
        if self._running:
            logger.warning("oracle_monitor_already_running", underlying=self.underlying)
            return
        self._running = True
        
        poll_task = asyncio.create_task(self._poll_loop(), name=f"oracle_poll_{self.underlying}")
        monitor_task = asyncio.create_task(self._monitor_loop(), name=f"oracle_monitor_{self.underlying}")
        
        self._tasks.add(poll_task)
        self._tasks.add(monitor_task)
        
        def _on_done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.error("oracle_task_failed", task=t.get_name(), error=str(t.exception()))
                
        poll_task.add_done_callback(_on_done)
        monitor_task.add_done_callback(_on_done)
        
    async def stop_polling(self) -> None:
        self._running = False
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

    async def _poll_loop(self) -> None:
        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    payload = {
                        "jsonrpc": "2.0",
                        "method": "eth_call",
                        "params": [{
                            "to": self.feed_address,
                            "data": "0xfeaf968c"
                        }, "latest"],
                        "id": 1
                    }
                    async with session.post(self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5.0)) as response:
                        result = await response.json()
                        if "result" in result and result["result"] != "0x":
                            data = result["result"][2:]
                            if len(data) >= 320:
                                answer_hex = data[64:128]
                                updated_at_hex = data[192:256]
                                
                                answer = int(answer_hex, 16) / 1e8
                                updated_at = int(updated_at_hex, 16)
                                
                                # F-09: Validate decoded data before accepting
                                now_unix = int(time.time())
                                if answer <= 0:
                                    logger.warning("oracle_invalid_answer", answer=answer, underlying=self.underlying)
                                elif updated_at > now_unix or (now_unix - updated_at) > 86400:
                                    logger.warning("oracle_stale_or_future_timestamp",
                                                   updated_at=updated_at, now=now_unix,
                                                   underlying=self.underlying)
                                else:
                                    self.last_chainlink_price = answer
                                    self.last_update_timestamp = updated_at
                except Exception as e:
                    logger.warning("oracle_poll_failed", error=str(e))
                    
                await asyncio.sleep(5.0)

    async def _monitor_loop(self) -> None:
        while self._running:
            now = time.time()
            deviation = 0.0
            if self.last_chainlink_price > 0:
                deviation = abs(self._last_binance_spot - self.last_chainlink_price) / self.last_chainlink_price
                
            heartbeat_imminent = self.seconds_until_heartbeat(now) < self.oracle_pause_seconds

            should_pause = (
                deviation > self.deviation_threshold * 0.7
                or heartbeat_imminent
            )

            if should_pause:
                if not self.pause_event.is_set():
                    logger.warning(
                        "oracle_pause_set",
                        underlying=self.underlying,
                        deviation=deviation,
                        heartbeat_imminent=heartbeat_imminent,
                    )
                self.pause_event.set()
                self._pause_triggered_at = now

            elif self.pause_event.is_set():
                if self._pause_triggered_at is None:
                    self._pause_triggered_at = now

                elapsed_since_trigger = now - self._pause_triggered_at
                if elapsed_since_trigger >= self.pause_cleared_after:
                    logger.info(
                        "oracle_pause_cleared",
                        underlying=self.underlying,
                        cooldown_elapsed_seconds=elapsed_since_trigger,
                    )
                    self.pause_event.clear()
                    self._pause_triggered_at = None

            await asyncio.sleep(1.0)

    def seconds_until_heartbeat(self, now: float) -> float:
        if self.last_update_timestamp == 0:
            return float('inf')
        return max(0.0, (self.last_update_timestamp + self.heartbeat_seconds) - now)
    
    def on_binance_tick(self, binance_spot: float) -> None:
        """Called whenever the Binance spot price updates."""
        self._last_binance_spot = binance_spot
        # [ADV-01] Fast-path hot-path deviation evaluation
        if self.last_chainlink_price > 0:
            deviation = abs(binance_spot - self.last_chainlink_price) / self.last_chainlink_price
            if deviation > self.deviation_threshold * 0.7:
                if not self.pause_event.is_set():
                    logger.warning(
                        "oracle_pause_set_fast_path",
                        underlying=self.underlying,
                        deviation=deviation,
                        chainlink=self.last_chainlink_price,
                        spot=binance_spot
                    )
                self.pause_event.set()
                self._pause_triggered_at = time.time()
