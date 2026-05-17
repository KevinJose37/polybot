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
    settings.paper_trading.mean_latency_ms = 0
    settings.paper_trading.std_latency_ms = 0
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


# ─── Position Manager Branch Tests (Audit 04 Fixes) ─────────────────────

def test_add_fill_partial_close_preserves_avg_price() -> None:
    """Partial close should NOT change avg_price of remaining shares."""
    pm = PositionManager()
    pm.add_fill("m1", "BUY", 0.50, 100)
    
    # Sell 30 @ 0.60 — partial close, 70 shares remain
    pm.add_fill("m1", "SELL", 0.60, 30)
    pos = pm.get_position("m1")
    assert pos.size == 70
    assert pos.avg_price == 0.50  # Must stay at original cost basis
    assert abs(pos.realized_pnl - 3.0) < 0.0001  # (0.60 - 0.50) * 30


def test_add_fill_flip_uses_fill_price() -> None:
    """Position flip should set avg_price to the fill price of the new direction."""
    pm = PositionManager()
    pm.add_fill("m1", "BUY", 0.50, 50)
    
    # Sell 80 @ 0.60 — closes 50 long, opens 30 short
    pm.add_fill("m1", "SELL", 0.60, 80)
    pos = pm.get_position("m1")
    assert pos.size == -30  # Short 30
    assert pos.avg_price == 0.60  # New short at fill price
    # Realized PnL from closing the 50 long: (0.60 - 0.50) * 50 = 5.0
    assert abs(pos.realized_pnl - 5.0) < 0.0001


def test_add_fill_increase_weighted_avg() -> None:
    """Increasing a position should compute weighted average price."""
    pm = PositionManager()
    pm.add_fill("m1", "BUY", 0.40, 60)
    pm.add_fill("m1", "BUY", 0.50, 40)
    
    pos = pm.get_position("m1")
    assert pos.size == 100
    # Weighted avg: (0.40*60 + 0.50*40) / 100 = (24 + 20) / 100 = 0.44
    assert abs(pos.avg_price - 0.44) < 0.0001


def test_resolved_positions_deque_maxlen() -> None:
    """resolved_positions should auto-evict oldest entries beyond 100."""
    pm = PositionManager()
    
    for i in range(110):
        mid = f"market_{i}"
        pm.add_fill(mid, "BUY", 0.50, 10)
        pm.settle_market(mid, settle_price=0.50)
    
    # deque(maxlen=100) should cap at 100
    assert len(pm.resolved_positions) == 100
    # Oldest should be market_10 (0-9 were evicted)
    assert pm.resolved_positions[0]["market_id"] == "market_10"


def test_retroactive_complement_settlement() -> None:
    """When parity legs resolve in different cycles, PnL should be retroactively adjusted."""
    pm = PositionManager()
    pm.register_parity_pair("yes_tok", "no_tok")
    
    # BUY YES @ 0.42 and NO @ 0.46 (total cost = 0.88)
    pm.add_fill("yes_tok", "BUY", 0.42, 100)
    pm.add_fill("no_tok", "BUY", 0.46, 100)
    
    # Cycle 1: YES resolves alone → settled at 0.5 (conservative default)
    pm.settle_market("yes_tok", settle_price=0.5)
    pnl_after_yes = pm.total_realized_pnl
    # YES PnL = (0.5 - 0.42) * 100 = 8.0
    assert abs(pnl_after_yes - 8.0) < 0.0001
    
    # Cycle 2: NO resolves → should retroactively adjust YES from 0.5 to (1.0 - 0.0) = 1.0
    pm.settle_market("no_tok", settle_price=0.0)
    pnl_after_no = pm.total_realized_pnl
    # Retroactive YES adjustment: (1.0 - 0.5) * 100 = +50.0
    # NO PnL = (0.0 - 0.46) * 100 = -46.0
    # Total: 8.0 + 50.0 + (-46.0) = 12.0
    # Which equals the true arb profit: (1.0 - 0.42 - 0.46) * 100 = 12.0
    assert abs(pnl_after_no - 12.0) < 0.01


def test_get_market_unrealized_pnl_parity() -> None:
    """get_market_unrealized_pnl should use parity valuation when complement exists."""
    pm = PositionManager()
    pm.register_parity_pair("yes", "no")
    
    pm.add_fill("yes", "BUY", 0.42, 100)
    pm.add_fill("no", "BUY", 0.46, 100)
    
    mid_prices = {"yes": 0.40, "no": 0.45}
    
    # Individual token PnL should be a portion of the pair PnL
    yes_pnl = pm.get_market_unrealized_pnl("yes", mid_prices)
    no_pnl = pm.get_market_unrealized_pnl("no", mid_prices)
    pair_pnl = pm.get_pair_unrealized_pnl("yes", "no", mid_prices)
    
    # Both should contribute to the total
    assert yes_pnl + no_pnl == pytest.approx(pair_pnl, abs=0.01)
    # And the pair PnL should be positive (parity arb)
    assert pair_pnl == pytest.approx(12.0, abs=0.01)

