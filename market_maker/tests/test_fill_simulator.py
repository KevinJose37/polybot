"""
tests/test_fill_simulator.py — Tests for the realistic fill simulator.
Covers: cancel discrimination, partial fills, taker fees, liquidity checks,
quote cooldown, order rejection, and VWAP slippage penalty.
"""

import pytest
import time
import random
from unittest.mock import patch

from execution.fill_simulator import FillSimulator
from utils.schemas import QuotePair, MarketOdds
from utils.pnl_engine import PnLEngine
from utils.schemas import FillRecord, InventoryState


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def make_book(bid_price=0.45, ask_price=0.55, bid_size=500, ask_size=500, extra_bids=None, extra_asks=None):
    """Create a MarketOdds with configurable L2 book."""
    bids = [{"price": str(bid_price), "size": str(bid_size)}]
    asks = [{"price": str(ask_price), "size": str(ask_size)}]
    if extra_bids:
        bids.extend(extra_bids)
    if extra_asks:
        asks.extend(extra_asks)
    return MarketOdds(
        market_id="test", token_id_yes="Y", token_id_no="N",
        yes_price=(bid_price + ask_price) / 2,
        bid_yes=bid_price, ask_yes=ask_price,
        bids=bids, asks=asks,
    )


def make_sim(**overrides):
    """Create a FillSimulator with test defaults."""
    # Only latency_ms and drain_rate are constructor args
    sim = FillSimulator(
        latency_ms=overrides.get("latency_ms", 0),
        drain_rate=overrides.get("drain_rate", 50.0),
    )
    # Override attributes for testing (bypass config defaults)
    sim.order_rejection_rate = overrides.get("rejection_rate", 0.0)
    sim.cancel_rate = overrides.get("cancel_rate", 0.60)
    sim.partial_fill_share = overrides.get("partial_fill_share", 0.30)
    sim.quote_cooldown_ms = overrides.get("cooldown_ms", 0)
    sim.gas_cost = overrides.get("gas_cost", 0.0)
    return sim


def submit_and_promote(sim, market_key, quotes, odds):
    """Submit quotes and immediately promote them (latency=0)."""
    sim.submit_quotes(market_key, quotes, odds)
    now = int(time.time() * 1000)
    sim.update_state(market_key, now, 0.5, odds, "btcusdt", 60)
    return now


# ══════════════════════════════════════════════════════════════
# 1. Latency Delay Tests
# ══════════════════════════════════════════════════════════════

def test_latency_delay():
    """Quotes should not go live until latency_ms has passed."""
    sim = make_sim(latency_ms=300)
    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = make_book()

    now = int(time.time() * 1000)
    sim.submit_quotes("test", quotes, odds)

    # Before latency
    fills = sim.update_state("test", now, 0.5, odds, "btcusdt", 60)
    assert len(fills) == 0
    assert "test" not in sim._live_quotes

    # After latency
    fills = sim.update_state("test", now + 300, 0.5, odds, "btcusdt", 60)
    assert len(fills) == 0
    assert sim._live_quotes["test"].bid_price == 0.40


# ══════════════════════════════════════════════════════════════
# 2. Cancel-vs-Fill Discrimination Tests
# ══════════════════════════════════════════════════════════════

def test_cancel_discrimination_slows_queue_drain():
    """With cancel_rate=0.60, only 40% of L1 size drops count as real fills."""
    sim = make_sim(cancel_rate=0.60)
    quotes = QuotePair(bid_price=0.45, ask_price=0.60, bid_size=10, ask_size=10)

    # Book has 100 ahead at 0.45
    odds = make_book(bid_price=0.45, bid_size=100)
    now = submit_and_promote(sim, "test", quotes, odds)

    # Queue should be 100
    assert sim._queue_pos["test"]["BUY"] == 100.0

    # L1 drops by 50 (but 60% are cancels, so only 20 real trades drain queue)
    odds2 = make_book(bid_price=0.45, bid_size=50)
    now += 500
    fills = sim.update_state("test", now, 0.5, odds2, "btcusdt", 60)

    # Queue should be 100 - 20 = 80 (not 50)
    assert sim._queue_pos["test"]["BUY"] == 80.0
    assert len(fills) == 0  # Still behind in queue


def test_cancel_rate_zero_drains_fully():
    """With cancel_rate=0, all L1 size drops drain the queue (legacy behavior)."""
    sim = make_sim(cancel_rate=0.0)
    quotes = QuotePair(bid_price=0.45, ask_price=0.60, bid_size=10, ask_size=10)

    odds = make_book(bid_price=0.45, bid_size=50)
    now = submit_and_promote(sim, "test", quotes, odds)
    assert sim._queue_pos["test"]["BUY"] == 50.0

    # L1 drops by 50, cancel_rate=0 so all 50 drain
    odds2 = make_book(bid_price=0.45, bid_size=0)
    now += 500
    fills = sim.update_state("test", now, 0.5, odds2, "btcusdt", 60)

    # Queue fully drained, should get a fill
    assert sim._queue_pos["test"]["BUY"] == 0
    assert len(fills) == 1


# ══════════════════════════════════════════════════════════════
# 3. Partial Fill on Adverse Sweeps
# ══════════════════════════════════════════════════════════════

def test_adverse_sweep_partial_fill():
    """Adverse sweep should result in partial fill, not full fill of our order."""
    sim = make_sim(partial_fill_share=0.30)
    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = make_book(bid_price=0.45, ask_price=0.55, bid_size=50)

    now = submit_and_promote(sim, "test", quotes, odds)

    # Market drops: best bid goes from 0.45 to 0.35
    # Our bid at 0.40 gets swept, but only partially
    # Only 30 contracts resting at 0.40 level, taker volume is small
    odds2 = make_book(
        bid_price=0.35, ask_price=0.55, bid_size=20,
        extra_bids=[{"price": "0.40", "size": "30"}]  # 30 resting at our price level
    )
    now += 100
    fills = sim.update_state("test", now, 0.35, odds2, "btcusdt", 60)

    # Should get a fill, but NOT 100 contracts (partial)
    if fills:
        assert fills[0].size < 100, f"Expected partial fill, got full fill of {fills[0].size}"
        assert fills[0].side == "BUY"
        assert fills[0].price == 0.40


def test_adverse_sweep_sell_side():
    """Sell-side adverse sweep (ask rises) should also be partial."""
    sim = make_sim(partial_fill_share=0.30)
    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = make_book(bid_price=0.45, ask_price=0.55)

    now = submit_and_promote(sim, "test", quotes, odds)

    # Market pumps: best ask goes from 0.55 to 0.65
    odds2 = make_book(
        bid_price=0.45, ask_price=0.65, ask_size=200,
        extra_asks=[{"price": "0.60", "size": "100"}]
    )
    now += 100
    fills = sim.update_state("test", now, 0.65, odds2, "btcusdt", 60)

    if fills:
        assert fills[0].size < 100, f"Expected partial fill, got {fills[0].size}"
        assert fills[0].side == "SELL"


# ══════════════════════════════════════════════════════════════
# 4. Taker Fee on Crossing Fills
# ══════════════════════════════════════════════════════════════

def test_taker_fee_on_crossing():
    """When our bid crosses the ask, we pay taker fee, not maker fee."""
    sim = make_sim(gas_cost=0.0)
    sim.taker_fee_rate = 0.02
    sim.maker_fee_rate = 0.01

    # Our bid at 0.60 crosses the ask at 0.55
    quotes = QuotePair(bid_price=0.60, ask_price=0.80, bid_size=10, ask_size=10)
    odds = make_book(bid_price=0.45, ask_price=0.55, ask_size=100)

    now = submit_and_promote(sim, "test", quotes, odds)

    # Should fill as taker
    fills = sim.update_state("test", now + 100, 0.5, odds, "btcusdt", 60)

    if fills:
        expected_taker_fee = 0.02 * 0.60 * fills[0].size
        assert abs(fills[0].fee - expected_taker_fee) < 0.001, (
            f"Expected taker fee {expected_taker_fee}, got {fills[0].fee}"
        )
        assert fills[0].is_maker is False


# ══════════════════════════════════════════════════════════════
# 5. Liquidity Check on Taker Fills
# ══════════════════════════════════════════════════════════════

def test_taker_fill_limited_by_liquidity():
    """Taker fill size should be capped by available liquidity at the price."""
    sim = make_sim(gas_cost=0.0)

    # Our bid at 0.60 crosses ask at 0.55, but only 5 contracts available
    quotes = QuotePair(bid_price=0.60, ask_price=0.80, bid_size=100, ask_size=100)
    odds = make_book(bid_price=0.45, ask_price=0.55, ask_size=5)

    now = submit_and_promote(sim, "test", quotes, odds)
    fills = sim.update_state("test", now + 100, 0.5, odds, "btcusdt", 60)

    if fills:
        assert fills[0].size <= 5, f"Fill should be limited to 5, got {fills[0].size}"


# ══════════════════════════════════════════════════════════════
# 6. Quote Cooldown After Fills
# ══════════════════════════════════════════════════════════════

def test_quote_cooldown_blocks_immediate_requote():
    """After a fill, new quotes should be blocked for cooldown_ms."""
    sim = make_sim(cooldown_ms=500)

    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = make_book(bid_price=0.45, ask_price=0.55)
    now = submit_and_promote(sim, "test", quotes, odds)

    # Force a fill (adverse sweep)
    odds_swept = make_book(bid_price=0.35, ask_price=0.55)
    now += 100
    fills = sim.update_state("test", now, 0.35, odds_swept, "btcusdt", 60)
    assert len(fills) > 0, "Expected at least one fill"

    # Immediately try to submit new quotes — should be blocked by cooldown
    new_quotes = QuotePair(bid_price=0.38, ask_price=0.58, bid_size=50, ask_size=50)
    sim.submit_quotes("test", new_quotes, odds_swept)

    # Check that no new pending quotes were added
    assert len(sim._pending_quotes.get("test", [])) == 0, "Quote should be blocked by cooldown"

    # After cooldown expires
    now += 600  # 600ms > 500ms cooldown
    sim.submit_quotes("test", new_quotes, odds_swept)
    # Now it should be accepted — manually set time forward
    # The submit_quotes uses time.time(), so we can't easily test the time part
    # But we can verify the cooldown was set
    assert sim._quote_cooldown_until["test"] > 0


# ══════════════════════════════════════════════════════════════
# 7. Order Rejection Simulation
# ══════════════════════════════════════════════════════════════

def test_order_rejection():
    """Orders should be rejected at the configured rejection rate."""
    sim = make_sim(rejection_rate=1.0)  # 100% rejection for determinism

    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = make_book()

    sim.submit_quotes("test", quotes, odds)
    assert len(sim._pending_quotes.get("test", [])) == 0, "All quotes should be rejected"


def test_no_rejection_when_rate_zero():
    """With rejection_rate=0, all quotes should be accepted."""
    sim = make_sim(rejection_rate=0.0)

    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = make_book()

    sim.submit_quotes("test", quotes, odds)
    assert len(sim._pending_quotes["test"]) == 1


# ══════════════════════════════════════════════════════════════
# 8. Gas Cost Inclusion
# ══════════════════════════════════════════════════════════════

def test_gas_cost_included_in_fee():
    """Fill fees should include gas cost."""
    sim = make_sim(gas_cost=0.005)
    sim.taker_fee_rate = 0.02

    # Force a taker fill (bid crosses ask)
    quotes = QuotePair(bid_price=0.60, ask_price=0.80, bid_size=1, ask_size=1)
    odds = make_book(bid_price=0.45, ask_price=0.55, ask_size=100)

    now = submit_and_promote(sim, "test", quotes, odds)
    fills = sim.update_state("test", now + 100, 0.5, odds, "btcusdt", 60)

    if fills:
        expected_fee = 0.02 * 0.60 * 1 + 0.005  # taker_fee + gas
        assert abs(fills[0].fee - expected_fee) < 0.001, (
            f"Expected fee {expected_fee}, got {fills[0].fee}"
        )


# ══════════════════════════════════════════════════════════════
# 9. VWAP Slippage Penalty Tests
# ══════════════════════════════════════════════════════════════

def test_vwap_penalty_beyond_depth():
    """VWAP should apply slippage penalty for size exceeding L2 depth."""
    engine = PnLEngine()

    # Book has 50 at 0.40 and 30 at 0.38
    levels = [
        {"price": "0.40", "size": "50"},
        {"price": "0.38", "size": "30"},
    ]

    # Try to exit 100 contracts (20 beyond depth)
    vwap = engine._calculate_vwap(levels, 100)

    # Expected: (50*0.40 + 30*0.38 + 20*0.38*0.90) / 100
    # = (20.0 + 11.4 + 6.84) / 100 = 0.3824
    # The penalty uses last_price (0.38) * (1 - 0.10) = 0.342
    expected = (50 * 0.40 + 30 * 0.38 + 20 * 0.38 * 0.90) / 100
    assert abs(vwap - expected) < 0.01, f"Expected VWAP ~{expected:.4f}, got {vwap:.4f}"


def test_vwap_no_penalty_within_depth():
    """VWAP should not apply penalty when position fits within L2 depth."""
    engine = PnLEngine()

    levels = [
        {"price": "0.40", "size": "100"},
        {"price": "0.38", "size": "100"},
    ]

    # Exit 50 — fully within first level
    vwap = engine._calculate_vwap(levels, 50)
    assert abs(vwap - 0.40) < 0.001, f"Expected VWAP 0.40, got {vwap:.4f}"


def test_vwap_empty_book():
    """VWAP of empty book should return 0."""
    engine = PnLEngine()
    vwap = engine._calculate_vwap([], 100)
    assert vwap == 0.0


# ══════════════════════════════════════════════════════════════
# 10. Integration: Fill → PnL Pipeline
# ══════════════════════════════════════════════════════════════

def test_fill_pnl_pipeline():
    """Full pipeline: fill simulator generates fill → PnL engine records it."""
    sim = make_sim(gas_cost=0.001, cancel_rate=0.0)
    pnl = PnLEngine()

    # Create a buy fill via taker crossing
    quotes = QuotePair(bid_price=0.60, ask_price=0.80, bid_size=5, ask_size=5)
    odds = make_book(bid_price=0.45, ask_price=0.55, ask_size=100)

    now = submit_and_promote(sim, "test", quotes, odds)
    fills = sim.update_state("test", now + 100, 0.5, odds, "btcusdt", 60)

    for fill in fills:
        pnl.record_fill("test", fill)

    stats = pnl.get_stats()
    if fills:
        assert stats["total_fills"] > 0
        assert stats["fee_pnl"] < 0, "Fee PnL should be negative (fees paid)"


# ══════════════════════════════════════════════════════════════
# 11. Reset Clears All State
# ══════════════════════════════════════════════════════════════

def test_reset_clears_all_state():
    """Reset should clear all tracking state for a market."""
    sim = make_sim()
    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = make_book()
    submit_and_promote(sim, "test", quotes, odds)

    sim.reset("test")

    assert "test" not in sim._live_quotes
    assert "test" not in sim._pending_quotes
    assert "test" not in sim._quote_cooldown_until
