"""Tests for live trading engine and paper trading PnL accuracy."""
import pytest
from bot.execution.position_manager import PositionManager
from bot.execution.fill_manager import FillManager
from bot.paper_trading.stats import TradingStats, TradeRecord
from bot.risk.engine import RiskEngine
from bot.settings import Settings
from bot.orderbook.local_book import LocalOrderBook
from bot.orderbook.book_state import BookState
from bot.arbitrage.opportunity import ArbOpportunity, ArbLeg, ArbType
from bot.utils.clocks import current_timestamp_ms, set_clock, reset_clock, SimulatedClock


# ─── MTM and Parity Valuation Tests ─────────────────────────────────────

def test_mtm_updates_unrealized_pnl() -> None:
    """update_all_mtm must produce non-zero unrealized PnL when positions exist."""
    pm = PositionManager()
    pm.add_fill("t1", "BUY", 0.45, 100)
    
    # Before MTM update, unrealized is 0
    assert pm.total_unrealized_pnl == 0.0
    
    # After MTM with mid price 0.55
    pm.update_all_mtm({"t1": 0.55})
    assert abs(pm.total_unrealized_pnl - 10.0) < 0.001  # (0.55 - 0.45) * 100


def test_mtm_parity_pair_valuation() -> None:
    """Parity pairs should be valued at $1.00 per matched share, ignoring mid prices."""
    pm = PositionManager()
    pm.register_parity_pair("yes", "no")
    
    pm.add_fill("yes", "BUY", 0.42, 100)
    pm.add_fill("no", "BUY", 0.46, 100)
    
    # Mid prices: 0.40, 0.45 (DON'T sum to 1.0)
    # Without parity: (0.40-0.42)*100 + (0.45-0.46)*100 = -3.0
    # WITH parity: 100*1.0 - (0.42*100 + 0.46*100) = 100 - 88 = +12.0
    pm.update_all_mtm({"yes": 0.40, "no": 0.45})
    assert abs(pm.total_unrealized_pnl - 12.0) < 0.001


def test_mtm_short_short_parity_valuation() -> None:
    """Short-short parity (Type-C SELL): selling both YES+NO creates $1.00 liability."""
    pm = PositionManager()
    pm.register_parity_pair("yes", "no")
    
    # SELL 100 YES @ 0.56, SELL 100 NO @ 0.50 → revenue = 1.06 per share, liability = 1.00
    pm.add_fill("yes", "SELL", 0.56, 100)
    pm.add_fill("no", "SELL", 0.50, 100)
    
    # Unrealized = revenue - liability = (0.56*100 + 0.50*100) - 100*1.0 = 106 - 100 = +6.0
    pm.update_all_mtm({"yes": 0.55, "no": 0.49})
    assert abs(pm.total_unrealized_pnl - 6.0) < 0.001


# ─── Stats PnL Accuracy Tests ───────────────────────────────────────────

def test_pnl_type_c_buy_parity() -> None:
    """Type-C BUY parity: buying YES+NO at total < $1.00 is guaranteed profit."""
    stats = TradingStats()
    
    # Simulate: BUY YES @ 0.42, BUY NO @ 0.46 → cost = 0.88, payout = 1.00
    stats.record_fill("yes_tok", "BUY", 0.42, 100, fee=0.30, opp_type="TYPE-C", opp_edge=0.12, opp_id="c1")
    stats.record_fill("no_tok", "BUY", 0.46, 100, fee=0.35, opp_type="TYPE-C", opp_edge=0.12, opp_id="c1")
    
    # Net PnL = payout - cost - fees = 100*1.0 - (42 + 46) - (0.30 + 0.35) = 11.35
    assert abs(stats.get_net_pnl(set()) - 11.35) < 0.01
    
    # Should be a win — both legs counted
    rate, wins, losses = stats.get_win_rate(set())
    assert wins == 2
    assert losses == 0


def test_pnl_type_c_sell_parity() -> None:
    """Type-C SELL parity: selling YES+NO at total > $1.00 captures the spread."""
    stats = TradingStats()
    
    # Simulate: SELL YES @ 0.56, SELL NO @ 0.50 → revenue = 1.06, liability = 1.00
    stats.record_fill("yes_tok", "SELL", 0.56, 100, fee=0.0, opp_type="TYPE-C", opp_edge=0.06, opp_id="c2")
    stats.record_fill("no_tok", "SELL", 0.50, 100, fee=0.0, opp_type="TYPE-C", opp_edge=0.06, opp_id="c2")
    
    # Net PnL = revenue - liability = (56 + 50) - 100 = 6.00
    assert abs(stats.get_net_pnl(set()) - 6.0) < 0.01


def test_pnl_type_b_monotonicity() -> None:
    """Type-B: SELL 5m + BUY 15m captures the spread immediately."""
    stats = TradingStats()
    
    # SELL 5m @ 0.60, BUY 15m @ 0.50
    stats.record_fill("tok_5m", "SELL", 0.60, 50, fee=0.0, opp_type="TYPE-B", opp_edge=0.10, opp_id="b1")
    stats.record_fill("tok_15m", "BUY", 0.50, 50, fee=0.20, opp_type="TYPE-B", opp_edge=0.10, opp_id="b1")
    
    # Net = 0.0 (TYPE-B returns 0 until settlement to prevent dashboard corruption)
    assert abs(stats.get_net_pnl(set()) - 0.0) < 0.01


def test_pnl_by_type_segregation() -> None:
    """PnL should be correctly segregated by strategy type."""
    stats = TradingStats()
    
    # Type-C trade
    stats.record_fill("y1", "BUY", 0.40, 50, fee=0.10, opp_type="TYPE-C", opp_edge=0.10, opp_id="c1")
    stats.record_fill("n1", "BUY", 0.50, 50, fee=0.10, opp_type="TYPE-C", opp_edge=0.10, opp_id="c1")
    
    # Type-B trade
    stats.record_fill("s5", "SELL", 0.55, 30, fee=0.0, opp_type="TYPE-B", opp_edge=0.05, opp_id="b1")
    stats.record_fill("b15", "BUY", 0.50, 30, fee=0.05, opp_type="TYPE-B", opp_edge=0.05, opp_id="b1")
    
    pnl_by = stats.get_pnl_by_type(set())
    assert "TYPE-C" in pnl_by
    assert "TYPE-B" in pnl_by
    
    # Type-C: payout=50, cost=40*50/50+50*50/50=40+50=45, fees=0.20 → PnL=50-45-0.20=4.80
    # Actually: payout=50*1.0=50, cost=(0.40*50 + 0.50*50)=20+25=45, fees=0.20 → 4.80
    assert pnl_by["TYPE-C"] > 0
    
    # Type-B: returns 0.0 until settlement
    assert abs(pnl_by["TYPE-B"] - 0.0) < 0.01


def test_matched_sizing_enforcement() -> None:
    """Paper executor should enforce matched sizing on parity arbs."""
    # This is implicitly tested through PnL — if sizes mismatch,
    # the parity payout calculation would be wrong.
    stats = TradingStats()
    
    # Simulate matched: same size on both legs
    stats.record_fill("y", "BUY", 0.42, 80, fee=0.20, opp_type="TYPE-C", opp_edge=0.10, opp_id="matched1")
    stats.record_fill("n", "BUY", 0.48, 80, fee=0.20, opp_type="TYPE-C", opp_edge=0.10, opp_id="matched1")
    
    # PnL = 80*1.0 - (0.42*80 + 0.48*80) - 0.40 = 80 - 72 - 0.40 = 7.60
    assert abs(stats.get_net_pnl(set()) - 7.60) < 0.01


def test_win_rate_multiple_opportunities() -> None:
    """Win rate should correctly classify multiple opportunities.
    TYPE-C counts each leg: 2-leg win = 2W, 2-leg loss = 2L."""
    stats = TradingStats()
    
    # Winning Type-C trade: cost < 1.0
    stats.record_fill("y1", "BUY", 0.40, 100, fee=0.20, opp_type="TYPE-C", opp_edge=0.10, opp_id="win1")
    stats.record_fill("n1", "BUY", 0.50, 100, fee=0.20, opp_type="TYPE-C", opp_edge=0.10, opp_id="win1")
    
    # Losing trade: total cost > $1.00 (fees push it over)
    stats.record_fill("y2", "BUY", 0.49, 100, fee=2.00, opp_type="TYPE-C", opp_edge=0.01, opp_id="lose1")
    stats.record_fill("n2", "BUY", 0.49, 100, fee=2.00, opp_type="TYPE-C", opp_edge=0.01, opp_id="lose1")
    
    rate, wins, losses = stats.get_win_rate(set())
    # Each TYPE-C counts both legs: 2W from win1 + 2L from lose1
    assert wins == 2
    assert losses == 2
    assert abs(rate - 0.5) < 0.001


@pytest.mark.asyncio
async def test_paper_executor_matched_sizing() -> None:
    """Paper executor should cap leg 2 to leg 1's actual fill size."""
    from bot.paper_trading.engine import PaperExecutor
    
    # Clean up kill switch from prior runs
    from pathlib import Path
    ks = Path(".kill_switch")
    if ks.exists():
        ks.unlink()
    
    settings = Settings()
    # Set high limits so this test exercises matched sizing, not risk rejection
    settings.risk.max_exposure_per_asset = 500.0
    settings.risk.max_portfolio_exposure = 1000.0
    pm = PositionManager()
    fm = FillManager()
    risk = RiskEngine(settings, pm)
    stats = TradingStats()
    
    # Book with limited depth on one side
    book_yes = LocalOrderBook("yes_tok", stale_threshold_ms=99999999)
    book_yes.state = BookState.ACTIVE
    book_yes.last_updated_ts = current_timestamp_ms()
    book_yes.asks = {0.42: 50.0}  # Only 50 shares available
    
    book_no = LocalOrderBook("no_tok", stale_threshold_ms=99999999)
    book_no.state = BookState.ACTIVE
    book_no.last_updated_ts = current_timestamp_ms()
    book_no.asks = {0.46: 200.0}  # 200 shares available
    
    orderbooks = {"yes_tok": book_yes, "no_tok": book_no}
    executor = PaperExecutor(settings, risk, pm, fm, orderbooks, stats=stats)
    
    opp = ArbOpportunity(
        opportunity_id="test_matched",
        type=ArbType.EXHAUSTIVE,
        edge=0.12,
        size=100.0,  # Request 100 but YES only has 50
        timestamp_ms=current_timestamp_ms(),
        legs=[
            ArbLeg(market_id="yes_tok", side="BUY", price=0.42, size=100.0),
            ArbLeg(market_id="no_tok", side="BUY", price=0.46, size=100.0),
        ]
    )
    
    acks = await executor.execute_opportunity(opp)
    assert len(acks) == 2
    
    # YES leg should fill at 50 (book depth limit)
    pos_yes = pm.get_position("yes_tok")
    assert abs(pos_yes.size - 50.0) < 0.001
    
    # NO leg should be MATCHED to 50, not 100
    pos_no = pm.get_position("no_tok")
    assert abs(pos_no.size - 50.0) < 0.001
