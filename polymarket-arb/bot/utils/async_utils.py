"""
Async coroutine utilities.
"""
import asyncio
from typing import Awaitable, Any


async def gather_with_concurrency(n: int, *tasks: Awaitable[Any]) -> list[Any]:
    """
    Gather tasks with a limit on concurrency.
    """
    semaphore = asyncio.Semaphore(n)

    async def sem_task(task: Awaitable[Any]) -> Any:
        async with semaphore:
            return await task

    return await asyncio.gather(*(sem_task(t) for t in tasks))
