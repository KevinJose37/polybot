"""
Latency simulation with pluggable RNG and sleep for deterministic testing.
"""
import random as _random
import asyncio
from typing import Callable, Awaitable


async def inject_latency(
    mean_ms: float = 120.0,
    std_ms: float = 30.0,
    rng: _random.Random | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """
    Injects a realistic gaussian latency before simulated execution.

    Args:
        mean_ms: Mean latency in milliseconds.
        std_ms: Standard deviation of latency in milliseconds.
        rng: Optional seeded Random instance for deterministic testing.
        sleep_fn: Optional async sleep function. Defaults to asyncio.sleep.
                  Inject a no-op coroutine in tests for instant execution.
    """
    r = rng or _random.Random()
    delay_ms = r.gauss(mean_ms, std_ms)
    delay_s = max(0.001, delay_ms / 1000.0)
    _sleep = sleep_fn or asyncio.sleep
    await _sleep(delay_s)
