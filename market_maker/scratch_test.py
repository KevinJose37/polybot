import time
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from execution.fill_simulator import FillSimulator
from utils.schemas import QuotePair, MarketOdds

def run_tests():
    sim = FillSimulator(latency_ms=0, drain_rate=50.0)
    quotes = QuotePair(bid_price=0.40, ask_price=0.60, bid_size=100, ask_size=100)
    odds = MarketOdds(
        market_id='test', token_id_yes='Y', token_id_no='N',
        yes_price=0.5, bid_yes=0.45, ask_yes=0.55,
        bids=[{'price': '0.45', 'size': '500'}],
        asks=[{'price': '0.55', 'size': '500'}]
    )

    sim.submit_quotes('test', quotes, odds)
    time.sleep(0.01)
    now = int(time.time() * 1000)
    sim.update_state('test', now, 0.5, odds, 'btcusdt', 60)

    print(f"Live quotes size before: {sim._live_quotes['test'].bid_size}")
    odds.bid_yes = 0.35
    now += 100
    fills = sim.update_state('test', now, 0.35, odds, 'btcusdt', 60)
    print(f"Fills: {fills}")

run_tests()
