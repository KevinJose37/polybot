"""
Tests for FillManager deduplication and inflight tracking.
"""
from bot.execution.fill_manager import FillManager
from bot.utils.clocks import set_clock, reset_clock, SimulatedClock


def test_dedup_within_window() -> None:
    """Same opportunity ID within window should be duplicate."""
    clock = SimulatedClock(start_ms=1000000)
    set_clock(clock)
    try:
        fm = FillManager(dedup_window_ms=60000)
        
        fm.mark_executed("opp1")
        assert fm.is_duplicate("opp1")
        
        # Different ID is not duplicate
        assert not fm.is_duplicate("opp2")
    finally:
        reset_clock()


def test_dedup_window_expiry() -> None:
    """After dedup window expires, same ID is no longer duplicate."""
    clock = SimulatedClock(start_ms=1000000)
    set_clock(clock)
    try:
        fm = FillManager(dedup_window_ms=60000)
        
        fm.mark_executed("opp1")
        assert fm.is_duplicate("opp1")
        
        # Advance past the window
        clock.advance(61000)
        assert not fm.is_duplicate("opp1")
    finally:
        reset_clock()


def test_inflight_tracking() -> None:
    """Test add/remove/check inflight orders."""
    fm = FillManager()
    
    fm.add_inflight_order("order1", {"market": "m1", "side": "BUY"})
    assert "order1" in fm.inflight_orders
    
    fm.remove_inflight_order("order1")
    assert "order1" not in fm.inflight_orders
    
    # Removing non-existent order doesn't crash
    fm.remove_inflight_order("order_nonexistent")


def test_cleanup() -> None:
    """Cleanup removes expired opportunities."""
    clock = SimulatedClock(start_ms=1000000)
    set_clock(clock)
    try:
        fm = FillManager(dedup_window_ms=60000)
        
        fm.mark_executed("opp1")
        fm.mark_executed("opp2")
        
        clock.advance(61000)
        fm.cleanup()
        
        assert len(fm.active_opportunities) == 0
    finally:
        reset_clock()


def test_expired_order_ttl() -> None:
    """Orders exceeding TTL should be returned by check_expired_orders."""
    clock = SimulatedClock(start_ms=1000000)
    set_clock(clock)
    try:
        fm = FillManager()
        
        fm.add_inflight_order("order1", {"market": "m1", "side": "BUY"})
        
        # Not expired yet
        assert fm.check_expired_orders(timeout_s=30.0) == []
        
        # Advance past timeout
        clock.advance(31000)
        expired = fm.check_expired_orders(timeout_s=30.0)
        assert "order1" in expired
        
        # Order added later should not be expired
        fm.add_inflight_order("order2", {"market": "m2", "side": "SELL"})
        expired2 = fm.check_expired_orders(timeout_s=30.0)
        assert "order1" in expired2
        assert "order2" not in expired2
    finally:
        reset_clock()

