"""
Tests for general utilities.
"""
import pytest
from bot.utils.retries import async_retry


class MockError(Exception):
    pass


@pytest.mark.asyncio
async def test_async_retry_success_first_try() -> None:
    """Test that retry decorator succeeds on first try."""
    attempts = 0
    
    @async_retry(max_retries=3, base_delay=0.01)
    async def successful_func() -> str:
        nonlocal attempts
        attempts += 1
        return "success"
        
    result = await successful_func()
    assert result == "success"
    assert attempts == 1


@pytest.mark.asyncio
async def test_async_retry_success_after_failure() -> None:
    """Test that retry decorator succeeds after failures."""
    attempts = 0
    
    @async_retry(max_retries=3, base_delay=0.01, exceptions=(MockError,))
    async def failing_func() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise MockError("Failed")
        return "success"
        
    result = await failing_func()
    assert result == "success"
    assert attempts == 3


@pytest.mark.asyncio
async def test_async_retry_max_retries_exceeded() -> None:
    """Test that retry decorator raises after max retries."""
    attempts = 0
    
    @async_retry(max_retries=2, base_delay=0.01, exceptions=(MockError,))
    async def always_failing_func() -> str:
        nonlocal attempts
        attempts += 1
        raise MockError("Failed")
        
    with pytest.raises(MockError):
        await always_failing_func()
        
    assert attempts == 3  # 1 initial + 2 retries
