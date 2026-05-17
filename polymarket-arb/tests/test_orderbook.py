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
    """Monotonic sequences are accepted — Polymarket uses timestamps, not strict +1."""
    book = LocalOrderBook("t1", stale_threshold_ms=5000)
    snapshot = OrderBookSnapshot(
        market_id="t1", bids=[(0.40, 100)], asks=[(0.42, 50)]
    )
    await book.apply_snapshot(snapshot, sequence=1)
    
    # Sequence 3 > 1 is fine (monotonic ordering)
    await book.apply_delta(bids=[(0.41, 10)], asks=[], sequence=3)
    
    assert book.state == BookState.ACTIVE
    assert book.best_bid() == 0.41  # New best bid applied
    assert book.last_sequence == 3


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


@pytest.mark.asyncio
async def test_orderbook_mid_price() -> None:
    book = LocalOrderBook("t1", stale_threshold_ms=5000)
    
    # 1. Neither bid nor ask (empty or pending)
    assert book.mid_price() is None
    
    # 2. Both bid and ask
    snapshot = OrderBookSnapshot(
        market_id="t1", bids=[(0.40, 100)], asks=[(0.42, 50)]
    )
    await book.apply_snapshot(snapshot, sequence=1)
    assert book.mid_price() == pytest.approx(0.41)
    
    # 3. Only bid
    await book.apply_delta(bids=[], asks=[(0.42, 0.0)], sequence=2)
    assert book.best_ask() is None
    assert book.mid_price() == pytest.approx(0.40)
    
    # 4. Only ask
    await book.apply_delta(bids=[(0.40, 0.0)], asks=[(0.44, 50)], sequence=3)
    assert book.best_bid() is None
    assert book.mid_price() == pytest.approx(0.44)
