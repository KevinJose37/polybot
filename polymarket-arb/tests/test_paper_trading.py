"""
Tests for paper trading logic.
"""
import pytest
from bot.paper_trading.fills import simulate_fill
from bot.paper_trading.slippage import apply_slippage
from bot.paper_trading.pnl import PnLTracker
from bot.execution.position_manager import PositionManager


from bot.orderbook.local_book import LocalOrderBook

from bot.orderbook.book_state import BookState
from bot.utils.clocks import current_timestamp_ms

def test_simulate_fill() -> None:
    book = LocalOrderBook("m1", stale_threshold_ms=99999999) # Make sure it's not stale
    book.state = BookState.ACTIVE
    book.last_updated_ts = current_timestamp_ms()
    
    # order_size = 10, depth = 100
    book.asks = {0.51: 100.0}
    is_filled, filled_size, vwap = simulate_fill(10.0, book, "BUY")
    assert is_filled
    assert filled_size == 10.0
    # Single level fill: VWAP = best ask (no slippage applied at fill time)
    assert abs(vwap - 0.51) < 0.001
    
    # order_size = 100, depth = 10
    book.asks = {0.51: 10.0}
    is_filled, filled_size, vwap = simulate_fill(100.0, book, "BUY")
    assert is_filled
    assert filled_size == 10.0
    assert abs(vwap - 0.51) < 0.001
    
    # depth = 0
    book.asks = {}
    is_filled, filled_size, vwap = simulate_fill(10.0, book, "BUY")
    assert not is_filled
    assert filled_size == 0.0


def test_simulate_fill_multilevel() -> None:
    """VWAP across multiple price levels."""
    book = LocalOrderBook("m1", stale_threshold_ms=99999999)
    book.state = BookState.ACTIVE
    book.last_updated_ts = current_timestamp_ms()
    
    # Two levels: 50 @ 0.50, 50 @ 0.55
    book.asks = {0.50: 50.0, 0.55: 50.0}
    is_filled, filled_size, vwap = simulate_fill(80.0, book, "BUY")
    assert is_filled
    assert filled_size == 80.0
    # VWAP = (50*0.50 + 30*0.55) / 80 = (25 + 16.5) / 80 = 0.51875
    assert abs(vwap - 0.51875) < 0.0001


def test_simulate_fill_sell() -> None:
    """SELL fills walk the bid side."""
    book = LocalOrderBook("m1", stale_threshold_ms=99999999)
    book.state = BookState.ACTIVE
    book.last_updated_ts = current_timestamp_ms()
    
    book.bids = {0.60: 100.0}
    is_filled, filled_size, vwap = simulate_fill(10.0, book, "SELL")
    assert is_filled
    assert filled_size == 10.0
    # Single level fill: VWAP = best bid (no slippage at fill time)
    assert abs(vwap - 0.60) < 0.001


def test_apply_slippage() -> None:
    # Buy 100 with 1% slippage
    buy_price = apply_slippage(0.50, "BUY", 0.01)
    assert abs(buy_price - 0.505) < 0.0001
    
    # Sell 100 with 1% slippage
    sell_price = apply_slippage(0.50, "SELL", 0.01)
    assert abs(sell_price - 0.495) < 0.0001


def test_position_manager_pnl() -> None:
    pm = PositionManager()
    
    # Buy 100 @ 0.50
    pm.add_fill("m1", "BUY", 0.50, 100)
    pos = pm.get_position("m1")
    assert pos.size == 100
    assert pos.avg_price == 0.50
    
    # Mark to market @ 0.60
    unrealized = pm.mark_to_market("m1", 0.60)
    assert abs(unrealized - 10.0) < 0.0001
    
    # Sell 50 @ 0.60
    pm.add_fill("m1", "SELL", 0.60, 50)
    pos = pm.get_position("m1")
    assert pos.size == 50
    assert pos.avg_price == 0.50
    assert abs(pos.realized_pnl - 5.0) < 0.0001
    
    # Sell 100 @ 0.40 (Flips to short 50)
    pm.add_fill("m1", "SELL", 0.40, 100)
    pos = pm.get_position("m1")
    assert pos.size == -50
    assert pos.avg_price == 0.40
    # First 50 close the long @ 0.40 -> PnL = (0.40 - 0.50)*50 = -5.0
    # Total realized = 5.0 - 5.0 = 0.0
    assert abs(pos.realized_pnl - 0.0) < 0.0001
    
    # Mark to market short 50 @ 0.30
    unrealized_short = pm.mark_to_market("m1", 0.30)
    # (0.40 - 0.30) * 50 = 5.0
    assert abs(unrealized_short - 5.0) < 0.0001


def test_position_manager_with_fees() -> None:
    """Fees should be deducted from realized PnL."""
    pm = PositionManager()
    
    # Buy 100 @ 0.50 with $1.00 fee
    pm.add_fill("m1", "BUY", 0.50, 100, fee=1.0)
    assert abs(pm.total_realized_pnl - (-1.0)) < 0.0001
    
    # Sell 100 @ 0.60 with $0.00 fee (sells are fee-free)
    pm.add_fill("m1", "SELL", 0.60, 100, fee=0.0)
    # trade PnL = +10, net = +10 - 0.00 - 1.00 = +9.00
    assert abs(pm.total_realized_pnl - 9.0) < 0.0001


def test_parity_pair_valuation() -> None:
    """Parity pairs (YES+NO) should be valued at $1.00 per matched share."""
    pm = PositionManager()
    pm.register_parity_pair("yes_token", "no_token")
    
    # Buy 100 YES @ 0.42 and 100 NO @ 0.46
    pm.add_fill("yes_token", "BUY", 0.42, 100)
    pm.add_fill("no_token", "BUY", 0.46, 100)
    
    # Mid prices: YES=0.40, NO=0.45 — these DON'T sum to 1.0
    # Without parity awareness: unrealized = (0.40-0.42)*100 + (0.45-0.46)*100 = -2 + -1 = -3
    # WITH parity awareness: matched 100 shares at $1.00 payout
    # unrealized = 100 * 1.0 - (0.42*100 + 0.46*100) = 100 - 88 = +12
    pm.update_all_mtm({"yes_token": 0.40, "no_token": 0.45})
    
    assert abs(pm.total_unrealized_pnl - 12.0) < 0.0001
    
    # With unmatched sizes: 100 YES + 60 NO
    pm2 = PositionManager()
    pm2.register_parity_pair("yes_token", "no_token")
    pm2.add_fill("yes_token", "BUY", 0.42, 100)
    pm2.add_fill("no_token", "BUY", 0.46, 60)
    
    pm2.update_all_mtm({"yes_token": 0.40, "no_token": 0.45})
    # 60 matched: pnl = 60*1.0 - (0.42*60 + 0.46*60) = 60 - 52.8 = 7.2
    # 40 excess YES at mid: (0.40 - 0.42) * 40 = -0.8
    # Total: 7.2 - 0.8 = 6.4
    assert abs(pm2.total_unrealized_pnl - 6.4) < 0.0001


def test_pnl_tracker_sharpe() -> None:
    tracker = PnLTracker()
    tracker.record_pnl(1.0)
    tracker.record_pnl(-0.5)
    tracker.record_pnl(2.0)
    tracker.record_pnl(1.5)
    
    sharpe = tracker.calculate_sharpe()
    assert sharpe > 0


def test_simulate_fill_exceeds_total_depth() -> None:
    """Order exceeding total book depth should fill only what's available."""
    book = LocalOrderBook("m1", stale_threshold_ms=99999999)
    book.state = BookState.ACTIVE
    book.last_updated_ts = current_timestamp_ms()
    
    # Total depth = 30 + 20 = 50
    book.asks = {0.50: 30.0, 0.55: 20.0}
    is_filled, filled_size, vwap = simulate_fill(200.0, book, "BUY")
    
    assert is_filled
    assert filled_size == 50.0  # Partial fill at total depth
    # VWAP = (30*0.50 + 20*0.55) / 50 = (15 + 11) / 50 = 0.52
    assert abs(vwap - 0.52) < 0.001


def test_simulate_fill_vwap_reflects_depth() -> None:
    """Multi-level fills should have VWAP reflecting actual depth walked."""
    book = LocalOrderBook("m1", stale_threshold_ms=99999999)
    book.state = BookState.ACTIVE
    book.last_updated_ts = current_timestamp_ms()
    
    # Two levels with different sizes
    book.asks = {0.50: 10.0, 0.60: 1000.0}
    is_filled, filled_size, vwap = simulate_fill(100.0, book, "BUY")
    
    assert is_filled
    assert filled_size == 100.0
    # VWAP = (10*0.50 + 90*0.60) / 100 = (5 + 54) / 100 = 0.59
    assert abs(vwap - 0.59) < 0.001
    assert vwap > 0.50  # VWAP is worse than best price


@pytest.mark.asyncio
async def test_paper_executor_dedup() -> None:
    """Same opportunity should be deduplicated on second submission."""
    from bot.execution.fill_manager import FillManager
    from bot.risk.engine import RiskEngine
    from bot.paper_trading.engine import PaperExecutor
    from bot.paper_trading.stats import TradingStats
    from bot.arbitrage.opportunity import ArbOpportunity, ArbLeg, ArbType
    from bot.settings import Settings
    from bot.utils.clocks import current_timestamp_ms as _ts
    
    settings = Settings()
    pm = PositionManager()
    fm = FillManager()
    risk = RiskEngine(settings, pm)
    stats = TradingStats()
    
    book = LocalOrderBook("m1", stale_threshold_ms=99999999)
    book.state = BookState.ACTIVE
    book.last_updated_ts = current_timestamp_ms()
    book.asks = {0.40: 100.0}
    book.bids = {0.50: 100.0}
    orderbooks = {"m1": book, "m2": book}
    
    executor = PaperExecutor(settings, risk, pm, fm, orderbooks, stats)
    
    opp = ArbOpportunity(
        opportunity_id="test_opp_1",
        type=ArbType.PARITY,
        edge=0.05,
        size=10.0,
        timestamp_ms=_ts(),
        legs=[
            ArbLeg(market_id="m1", side="BUY", price=0.40, size=10.0),
            ArbLeg(market_id="m2", side="BUY", price=0.50, size=10.0),
        ]
    )
    
    # First execution should succeed
    acks1 = await executor.execute_opportunity(opp)
    assert len(acks1) == 2
    assert stats.opportunities_executed == 1
    
    # Second execution of same opp should be deduped
    acks2 = await executor.execute_opportunity(opp)
    assert len(acks2) == 0
    assert stats.opportunities_rejected_dedup == 1
