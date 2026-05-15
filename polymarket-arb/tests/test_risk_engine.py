"""
Tests for Risk Engine.
"""
import pytest
import os
from pathlib import Path
from bot.risk.engine import RiskEngine, RiskKillSwitchTriggered
from bot.execution.position_manager import PositionManager
from bot.settings import Settings


@pytest.fixture(autouse=True)
def clean_kill_switch():
    """Ensure kill switch file is cleaned up between tests."""
    path = Path(".kill_switch")
    if path.exists():
        path.unlink()
    yield
    if path.exists():
        path.unlink()


def test_risk_exposure_limit() -> None:
    settings = Settings()
    settings.risk.max_exposure_per_asset = 100.0  # Max $100
    pm = PositionManager()
    
    # Add a position of 100 size at $1 (Notional = $100)
    pm.add_fill("m1", "BUY", 1.0, 100.0)
    
    risk = RiskEngine(settings, pm)
    
    # Trying to buy 10 more should fail
    assert not risk.validate_order("m1", 10.0)
    
    # Trying to buy for another market should succeed
    assert risk.validate_order("m2", 50.0)


def test_risk_drawdown_killswitch() -> None:
    settings = Settings()
    settings.risk.max_daily_drawdown = 50.0  # Max $50 drawdown
    pm = PositionManager()
    
    # Realize a loss of $60
    pm.add_fill("m1", "BUY", 1.0, 100.0)
    pm.add_fill("m1", "SELL", 0.40, 100.0)  # Loss = (0.4 - 1.0) * 100 = -60
    
    assert pm.total_realized_pnl == -60.0
    
    risk = RiskEngine(settings, pm)
    
    # Trying to place any order should trigger kill switch
    with pytest.raises(RiskKillSwitchTriggered):
        risk.validate_order("m1", 10.0)
        
    assert risk.kill_switch_active


def test_risk_portfolio_cap() -> None:
    """Portfolio-level exposure cap blocks when total exceeds limit."""
    settings = Settings()
    settings.risk.max_portfolio_exposure = 200.0
    settings.risk.max_exposure_per_asset = 500.0  # High per-asset, low portfolio
    pm = PositionManager()
    
    # Fill 3 markets at $80 each = $240 total exposure
    pm.add_fill("m1", "BUY", 0.80, 100.0)  # 100 * 0.80 = $80
    pm.add_fill("m2", "BUY", 0.80, 100.0)  # $80
    pm.add_fill("m3", "BUY", 0.80, 100.0)  # $80 → total = $240
    
    risk = RiskEngine(settings, pm)
    
    # New order should be rejected (total would exceed $200)
    assert not risk.validate_order("m4", 50.0)


def test_risk_stale_feed_rejection() -> None:
    """Stale orderbook feed blocks order."""
    import asyncio
    from bot.orderbook.local_book import LocalOrderBook
    from bot.orderbook.book_state import BookState
    from bot.api.schemas import OrderBookSnapshot
    
    settings = Settings()
    pm = PositionManager()
    risk = RiskEngine(settings, pm)
    
    book = LocalOrderBook("t1", stale_threshold_ms=10)  # 10ms threshold
    book.state = BookState.ACTIVE
    # Set last_updated_ts to something old
    book.last_updated_ts = 0  # epoch = definitely stale
    
    orderbooks = {"t1": book}
    
    assert not risk.validate_order("t1", 10.0, orderbooks=orderbooks)


def test_risk_kill_switch_persists() -> None:
    """Kill switch survives restart (new RiskEngine instance)."""
    settings = Settings()
    pm = PositionManager()
    
    # Activate kill switch
    risk1 = RiskEngine(settings, pm)
    risk1.activate_kill_switch("test_reason")
    assert risk1.kill_switch_active
    
    # Create new instance — should restore from disk
    risk2 = RiskEngine(settings, pm)
    assert risk2.kill_switch_active
    
    # Clear and verify
    risk2.clear_kill_switch()
    risk3 = RiskEngine(settings, pm)
    assert not risk3.kill_switch_active
