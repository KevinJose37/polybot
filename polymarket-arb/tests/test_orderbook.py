"""
Tests for local orderbook.
"""
import pytest
import asyncio

from bot.api.schemas import OrderBookSnapshot
from bot.orderbook.local_book import LocalOrderBook
from bot.orderbook.book_state import BookState
from bot.orderbook.reconciliation import SequenceGapError
from bot.utils.clocks import SimulatedClock, set_clock, reset_clock



@pytest.mark.asyncio
async def test_orderbook_snapshot() -> None:
    book = LocalOrderBook("t1", stale_threshold_ms=5000)
    assert book.state == BookState.PENDING
    
    snapshot = OrderBookSnapshot(
        market_id="t1",
        bids=[(0.40, 100), (0.39, 200)],
        asks=[(0.42, 50), (0.43, 10)]
    )
    
    await book.apply_snapshot(snapshot, sequence=1)
    
    assert book.state == BookState.ACTIVE
    assert book.best_bid() == 0.40
    assert book.best_ask() == 0.42
    assert book.last_sequence == 1


@pytest.mark.asyncio
async def test_orderbook_delta_update() -> None:
    book = LocalOrderBook("t1", stale_threshold_ms=5000)
    snapshot = OrderBookSnapshot(
        market_id="t1",
        bids=[(0.40, 100)],
        asks=[(0.42, 50)]
    )
    await book.apply_snapshot(snapshot, sequence=1)
    
    # sequence=2, updates existing bid, deletes ask, adds new ask
    await book.apply_delta(
        bids=[(0.40, 150)],
        asks=[(0.42, 0.0), (0.44, 20)],
        sequence=2
    )
    
    assert book.state == BookState.ACTIVE
    assert book.bids[0.40] == 150.0
    assert 0.42 not in book.asks
    assert book.asks[0.44] == 20.0
    assert book.best_ask() == 0.44


@pytest.mark.asyncio
async def test_orderbook_sequence_gap() -> None:
    book = LocalOrderBook("t1", stale_threshold_ms=5000)
    snapshot = OrderBookSnapshot(
        market_id="t1", bids=[(0.40, 100)], asks=[(0.42, 50)]
    )
    await book.apply_snapshot(snapshot, sequence=1)
    
    # Gap! sequence=3 instead of 2
    await book.apply_delta(bids=[(0.41, 10)], asks=[], sequence=3)
    
    assert book.state == BookState.DISCONNECTED
    assert book.best_bid() is None
    assert book.last_sequence is None


@pytest.mark.asyncio
async def test_orderbook_stale() -> None:
    clock = SimulatedClock(start_ms=1000000)
    set_clock(clock)
    try:
        book = LocalOrderBook("t1", stale_threshold_ms=10)
        snapshot = OrderBookSnapshot(
            market_id="t1", bids=[(0.40, 100)], asks=[(0.42, 50)]
        )
        await book.apply_snapshot(snapshot, sequence=1)
        
        assert book.is_stale() is False
        
        # Advance clock past stale threshold
        clock.advance(20)
        assert book.is_stale() is True
        assert book.best_bid() is None
    finally:
        reset_clock()

