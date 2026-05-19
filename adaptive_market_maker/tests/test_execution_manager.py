"""Unit tests for the Execution Manager."""

import pytest
from engine.quoting_engine import QuoteResult
from engine.execution_manager import (
    ExecutionManager,
    LiveOrder,
    PlaceOrder,
    CancelOrder,
    is_adverse_requote,
    exceeds_threshold
)


def test_is_adverse_requote() -> None:
    # BID side
    # Target < Live -> worse quote (overbidding), urgent cancel
    assert is_adverse_requote(live=0.510, target=0.505, side="BID") is True
    # Target >= Live -> better quote, not adverse
    assert is_adverse_requote(live=0.510, target=0.515, side="BID") is False
    
    # ASK side
    # Target > Live -> worse quote (underasking), urgent cancel
    assert is_adverse_requote(live=0.510, target=0.515, side="ASK") is True
    # Target <= Live -> better quote, not adverse
    assert is_adverse_requote(live=0.510, target=0.505, side="ASK") is False

def test_exceeds_threshold() -> None:
    # BID side
    assert exceeds_threshold(live=0.510, target=0.515, side="BID", threshold=0.010) is False
    assert exceeds_threshold(live=0.510, target=0.525, side="BID", threshold=0.010) is True
    
    # ASK side
    assert exceeds_threshold(live=0.510, target=0.505, side="ASK", threshold=0.010) is False
    assert exceeds_threshold(live=0.510, target=0.495, side="ASK", threshold=0.010) is True


def test_execution_manager_empty_book() -> None:
    em = ExecutionManager(
        requote_threshold=0.010,
        dwell_min_seconds=3.0,
        max_open_orders=4,
        order_size_usdc=10.0
    )
    
    quotes = QuoteResult(bid=0.50, ask=0.52, half_spread=0.01, skew=0.0)
    actions = em.process_quotes("m1", quotes, 100.0)
    
    # Expect 2 PlaceOrders
    assert len(actions) == 2
    assert all(isinstance(a, PlaceOrder) for a in actions)
    places = sorted(actions, key=lambda a: a.side)  # ASK, BID
    
    assert places[0].side == "ASK"
    assert places[0].price == 0.52
    assert places[0].size == 10.0 / 0.52
    
    assert places[1].side == "BID"
    assert places[1].price == 0.50


def test_execution_manager_dwell_block() -> None:
    em = ExecutionManager(
        requote_threshold=0.010,
        dwell_min_seconds=3.0,
        max_open_orders=4,
        order_size_usdc=10.0
    )
    
    em.add_live_order(LiveOrder("o1", "m1", "BID", 0.50, 20.0, 100.0, "live"))
    
    # Time 101.0 (1s elapsed, dwell block active)
    # Target=0.60 is a better quote (target > live), so is_adverse_requote is False.
    # Therefore it hits the standard path, which blocks it due to dwell.
    quotes = QuoteResult(bid=0.60, ask=None, half_spread=0.01, skew=0.0)
    actions = em.process_quotes("m1", quotes, 101.0)
    assert len(actions) == 0


def test_execution_manager_emergency_unwind() -> None:
    em = ExecutionManager(
        requote_threshold=0.010,
        dwell_min_seconds=3.0,
        max_open_orders=4,
        order_size_usdc=10.0
    )
    
    em.add_live_order(LiveOrder("o1", "m1", "BID", 0.50, 20.0, 100.0, "live"))
    
    # Emergency halt (target is None), ignores dwell block
    quotes = QuoteResult(bid=None, ask=None, half_spread=0.01, skew=0.0)
    actions = em.process_quotes("m1", quotes, 101.0)
    
    assert len(actions) == 1
    assert isinstance(actions[0], CancelOrder)
    assert actions[0].order_id == "o1"
    
    # State changed to pending_cancel
    assert em.live_orders["m1"][0].status == "pending_cancel"


def test_execution_manager_max_orders() -> None:
    em = ExecutionManager(
        requote_threshold=0.010,
        dwell_min_seconds=3.0,
        max_open_orders=2, # Max 2!
        order_size_usdc=10.0
    )
    
    em.add_live_order(LiveOrder("o1", "m1", "BID", 0.50, 20.0, 100.0, "live"))
    em.add_live_order(LiveOrder("o2", "m2", "BID", 0.50, 20.0, 100.0, "live"))
    
    # Requesting to place on m3, but we are at max orders (2)
    quotes = QuoteResult(bid=0.50, ask=None, half_spread=0.01, skew=0.0)
    actions = em.process_quotes("m3", quotes, 105.0)
    assert len(actions) == 0
    
    # What if we are replacing an existing order? It should generate Cancel but NOT Place,
    # wait... if we cancel, the state goes to "pending_cancel", so live count drops!
    em.add_live_order(LiveOrder("o3", "m3", "BID", 0.60, 20.0, 100.0, "live"))
    # Currently 3 live orders (over max).
    # Requote o3 due to adverse selection (0.50 < 0.60)
    actions = em.process_quotes("m3", quotes, 105.0)
    
    # We should get a Cancel. 
    # Will we get a Place? 
    # Before placing, it checks get_live_count() < max_open_orders. 
    # After canceling o3, it becomes pending_cancel, so live count goes from 3 to 2.
    # 2 < 2 is False! So it will NOT place!
    assert len(actions) == 1
    assert isinstance(actions[0], CancelOrder)

    # Now let's test a successful replacement under max limit
    em2 = ExecutionManager(0.010, 3.0, 4, 10.0)
    em2.add_live_order(LiveOrder("o1", "m1", "BID", 0.60, 20.0, 100.0, "live"))
    quotes_replace = QuoteResult(bid=0.50, ask=None, half_spread=0.01, skew=0.0)
    actions_replace = em2.process_quotes("m1", quotes_replace, 105.0)
    
    assert len(actions_replace) == 2
    assert isinstance(actions_replace[0], CancelOrder)
    assert isinstance(actions_replace[1], PlaceOrder)


def test_update_order_status() -> None:
    em = ExecutionManager(0.01, 3.0, 4, 10.0)
    em.add_live_order(LiveOrder("o1", "m1", "BID", 0.50, 20.0, 100.0, "live"))
    
    em.update_order_status("o1", "m1", "pending_cancel")
    assert em.live_orders["m1"][0].status == "pending_cancel"
    
    em.update_order_status("o1", "m1", "cancelled")
    # Cleaned up
    assert len(em.live_orders["m1"]) == 0

def test_action_base() -> None:
    from engine.execution_manager import Action
    assert Action() is not None
