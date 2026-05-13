import pytest
import time
from execution.fill_simulator import FillSimulator
from utils.schemas import QuotePair, MarketOdds

def test_latency_delay():
    sim = FillSimulator(latency_ms=300, drain_rate=50.0)
    
    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    
    odds = MarketOdds(
        market_id="test", token_id_yes="Y", token_id_no="N",
        yes_price=0.5, bid_yes=0.45, ask_yes=0.55,
        bids=[{"price": "0.45", "size": "500"}],
        asks=[{"price": "0.55", "size": "500"}]
    )
    
    now = int(time.time() * 1000)
    
    # Submit quote
    sim.submit_quotes("test", quotes, odds)
    
    # Update immediately (should be no live quotes)
    fills = sim.update_state("test", now, 0.5, odds, "btcusdt", 60)
    assert len(fills) == 0
    assert not sim._live_quotes
    
    # Update after 300ms
    now += 300
    fills = sim.update_state("test", now, 0.5, odds, "btcusdt", 60)
    assert len(fills) == 0
    assert sim._live_quotes["test"].bid_price == 0.40

def test_adverse_selection():
    sim = FillSimulator(latency_ms=0, drain_rate=50.0)
    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = MarketOdds(
        market_id="test", token_id_yes="Y", token_id_no="N",
        yes_price=0.5, bid_yes=0.45, ask_yes=0.55,
        bids=[{"price": "0.45", "size": "500"}],
        asks=[{"price": "0.55", "size": "500"}]
    )
    
    now = int(time.time() * 1000)
    sim.submit_quotes("test", quotes, odds)
    sim.update_state("test", now, 0.5, odds, "btcusdt", 60)
    
    # Market dumps, best bid goes to 0.35 (below our bid of 0.40)
    odds.bid_yes = 0.35
    now += 100
    fills = sim.update_state("test", now, 0.35, odds, "btcusdt", 60)
    
    assert len(fills) == 1
    assert fills[0].side == "BUY"
    assert fills[0].price == 0.40
    assert fills[0].size == 100

def test_queue_drain():
    sim = FillSimulator(latency_ms=0, drain_rate=1000.0) # Fast drain
    quotes = QuotePair(bid_price=0.45, ask_price=0.60, bid_size=100, ask_size=100)
    
    # Book has $500 ahead of us at 0.45
    odds = MarketOdds(
        market_id="test", token_id_yes="Y", token_id_no="N",
        yes_price=0.5, bid_yes=0.45, ask_yes=0.55,
        bids=[{"price": "0.45", "size": "500"}],
        asks=[{"price": "0.55", "size": "500"}]
    )
    
    now = int(time.time() * 1000)
    sim.submit_quotes("test", quotes, odds)
    sim.update_state("test", now, 0.5, odds, "btcusdt", 60)
    
    assert sim._queue_pos["test"]["BUY"] == 500.0
    
    # Advance 1 second, drain rate is 1000, so queue should be 0
    now += 1000
    # Force random to always trigger for test
    import random
    original_random = random.random
    random.random = lambda: 0.0
    
    try:
        fills = sim.update_state("test", now, 0.5, odds, "btcusdt", 60)
        assert len(fills) == 1
        assert fills[0].side == "BUY"
        assert sim._queue_pos["test"]["BUY"] == 0
    finally:
        random.random = original_random
