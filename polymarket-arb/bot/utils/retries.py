"""
Async retry utilities.
"""
import asyncio
import functools
import structlog
from typing import Callable, Any, TypeVar, cast, Awaitable

logger = structlog.get_logger(__name__)

T = TypeVar("T")

def async_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator for retrying async functions with exponential backoff.
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = base_delay
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries:
                        logger.error(
                            "max_retries_exceeded", 
                            func=func.__name__, 
                            error=str(e), 
                            attempt=attempt
                        )
                        raise
                    
                    logger.warning(
                        "retry_attempt", 
                        func=func.__name__, 
                        error=str(e), 
                        attempt=attempt, 
                        next_delay=delay
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, max_delay)
            # Should not reach here
            raise RuntimeError("Unreachable code in retry logic")
        return cast(Callable[..., Awaitable[T]], wrapper)
    return decorator
