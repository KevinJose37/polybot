"""Unit tests for adapters."""
import pytest
from adapters.mid_reconciler import MidReconciler, ReconcilerConfig
from adapters.polymarket_ws import PolymarketWSAdapter


def test_polymarket_orderbook_reconstruction() -> None:
    """Test that the WS adapter correctly builds and updates the orderbook."""
    adapter = PolymarketWSAdapter()
    adapter._subs.add("0x123")
    adapter._books["0x123"] = ({}, {})

    # 1. Snapshot
    adapter._process_message({"asset_id": "0x123", "bids": [{"price": "0.5", "size": "100"}], "asks": [{"price": "0.51", "size": "200"}]})

    book = adapter._get_orderbook("0x123")
    assert len(book.bids) == 1
    assert book.bids[0] == (0.5, 100.0)
    assert len(book.asks) == 1
    assert book.asks[0] == (0.51, 200.0)
    assert book.mid_price == 0.505

    # 2. Delta adding a better bid
    adapter._process_message({"asset_id": "0x123", "bids": [{"price": "0.505", "size": "50"}]})

    book = adapter._get_orderbook("0x123")
    assert len(book.bids) == 2
    assert book.bids[0] == (0.505, 50.0)  # Highest bid first
    assert book.mid_price == pytest.approx(0.5075)

    # 3. Delta removing an ask
    adapter._process_message({"asset_id": "0x123", "asks": [{"price": "0.51", "size": "0"}]})

    book = adapter._get_orderbook("0x123")
    assert len(book.asks) == 0
    assert book.mid_price == 0.505


def test_mid_reconciler_divergence() -> None:
    """Test that MidReconciler flags huge divergences correctly."""
    config = ReconcilerConfig(divergence_threshold=0.10)
    reconciler = MidReconciler(config)

    # Initial state
    flagged = reconciler.update_polymarket_mid("0x123", 0.50)
    assert not flagged

    # Small move (2%)
    flagged = reconciler.update_polymarket_mid("0x123", 0.51)
    assert not flagged

    # Large move (12% from baseline 0.50 -> 0.56)
    flagged = reconciler.update_polymarket_mid("0x123", 0.56)
    assert flagged
