"""
tests/test_polymarket_ws.py — Tests for the Polymarket WebSocket feed.
Tests the local book maintenance, price_change incremental updates,
and REST fallback logic.
"""

import asyncio
import pytest
import time

from data.feeds.polymarket_ws import PolymarketFeed
from utils.schemas import MarketOdds


# ══════════════════════════════════════════════════════════════
# 1. Local Book State Management
# ══════════════════════════════════════════════════════════════

def test_normalize_levels_sorts_bids_descending():
    """Bids should be sorted highest price first."""
    feed = PolymarketFeed("Y", "N", "test")
    levels = [
        {"price": "0.40", "size": "100"},
        {"price": "0.50", "size": "200"},
        {"price": "0.45", "size": "150"},
    ]
    result = feed._normalize_levels(levels, descending=True)
    prices = [float(l["price"]) for l in result]
    assert prices == [0.50, 0.45, 0.40]


def test_normalize_levels_sorts_asks_ascending():
    """Asks should be sorted lowest price first."""
    feed = PolymarketFeed("Y", "N", "test")
    levels = [
        {"price": "0.60", "size": "100"},
        {"price": "0.50", "size": "200"},
        {"price": "0.55", "size": "150"},
    ]
    result = feed._normalize_levels(levels, descending=False)
    prices = [float(l["price"]) for l in result]
    assert prices == [0.50, 0.55, 0.60]


def test_normalize_levels_handles_list_format():
    """Handle [price, size] tuple/list format."""
    feed = PolymarketFeed("Y", "N", "test")
    levels = [
        ["0.45", "100"],
        ["0.50", "200"],
    ]
    result = feed._normalize_levels(levels, descending=True)
    assert len(result) == 2
    assert result[0]["price"] == "0.50"
    assert result[0]["size"] == "200"


# ══════════════════════════════════════════════════════════════
# 2. Incremental Level Updates
# ══════════════════════════════════════════════════════════════

def test_apply_level_update_insert():
    """New price level should be inserted and book re-sorted."""
    feed = PolymarketFeed("Y", "N", "test")
    levels = [
        {"price": "0.50", "size": "200"},
        {"price": "0.45", "size": "100"},
    ]
    feed._apply_level_update(levels, "0.48", "150", descending=True)
    assert len(levels) == 3
    prices = [float(l["price"]) for l in levels]
    assert prices == [0.50, 0.48, 0.45]


def test_apply_level_update_modify():
    """Existing price level should be updated in place."""
    feed = PolymarketFeed("Y", "N", "test")
    levels = [
        {"price": "0.50", "size": "200"},
        {"price": "0.45", "size": "100"},
    ]
    feed._apply_level_update(levels, "0.45", "300", descending=True)
    assert len(levels) == 2
    assert levels[1]["size"] == "300"


def test_apply_level_update_remove():
    """Level with size 0 should be removed."""
    feed = PolymarketFeed("Y", "N", "test")
    levels = [
        {"price": "0.50", "size": "200"},
        {"price": "0.45", "size": "100"},
    ]
    feed._apply_level_update(levels, "0.45", "0", descending=True)
    assert len(levels) == 1
    assert float(levels[0]["price"]) == 0.50


def test_apply_level_update_ignore_zero_insert():
    """Inserting a level with size 0 should not add it."""
    feed = PolymarketFeed("Y", "N", "test")
    levels = [{"price": "0.50", "size": "200"}]
    feed._apply_level_update(levels, "0.40", "0", descending=True)
    assert len(levels) == 1


# ══════════════════════════════════════════════════════════════
# 3. Book Rebuild (L2 → MarketOdds)
# ══════════════════════════════════════════════════════════════

def test_rebuild_odds_from_book():
    """Rebuilding odds should produce correct best bid/ask and mid price."""
    feed = PolymarketFeed("Y", "N", "test_market")
    feed._bids = [
        {"price": "0.48", "size": "200"},
        {"price": "0.45", "size": "100"},
    ]
    feed._asks = [
        {"price": "0.52", "size": "150"},
        {"price": "0.55", "size": "300"},
    ]
    feed._rebuild_odds()

    odds = feed._last_odds
    assert odds is not None
    assert odds.bid_yes == 0.48
    assert odds.ask_yes == 0.52
    assert abs(odds.yes_price - 0.50) < 0.001
    assert odds.book_depth_bid == 2
    assert odds.book_depth_ask == 2
    assert len(odds.bids) == 2
    assert len(odds.asks) == 2


def test_rebuild_odds_empty_book():
    """Empty book should use defaults (bid=0, ask=1)."""
    feed = PolymarketFeed("Y", "N", "test_market")
    feed._bids = []
    feed._asks = []
    feed._rebuild_odds()

    odds = feed._last_odds
    assert odds.bid_yes == 0.0
    assert odds.ask_yes == 1.0


# ══════════════════════════════════════════════════════════════
# 4. WebSocket Message Processing
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_handle_book_snapshot():
    """Book snapshot event should replace the entire local book."""
    feed = PolymarketFeed("TOKEN_YES", "N", "test")
    feed._bids = [{"price": "0.99", "size": "999"}]  # Old data

    await feed._handle_book_snapshot({
        "event_type": "book",
        "asset_id": "TOKEN_YES",
        "bids": [
            {"price": "0.45", "size": "100"},
            {"price": "0.43", "size": "200"},
        ],
        "asks": []
    })
    
    await feed._handle_book_snapshot({
        "event_type": "book",
        "asset_id": "N",
        "bids": [
            {"price": "0.45", "size": "150"}, # 1 - 0.45 = 0.55 ask
        ],
        "asks": []
    })

    assert len(feed._bids) == 2
    assert len(feed._asks) == 1
    assert float(feed._bids[0]["price"]) == 0.45  # Sorted descending
    assert feed._last_odds.bid_yes == 0.45
    assert feed._last_odds.ask_yes == 0.55


@pytest.mark.asyncio
async def test_handle_book_snapshot_wrong_token():
    """Book snapshot for a different token should be ignored."""
    feed = PolymarketFeed("TOKEN_YES", "N", "test")
    feed._bids = [{"price": "0.50", "size": "100"}]

    await feed._handle_book_snapshot({
        "event_type": "book",
        "asset_id": "WRONG_TOKEN",
        "bids": [{"price": "0.99", "size": "999"}],
        "asks": [],
    })

    assert float(feed._bids[0]["price"]) == 0.50  # Unchanged


@pytest.mark.asyncio
async def test_handle_price_change_buy():
    """Price change on buy side should update bid levels."""
    feed = PolymarketFeed("TOKEN_YES", "N", "test")
    feed._bids = [{"price": "0.50", "size": "200"}]
    feed._asks = [{"price": "0.55", "size": "100"}]
    feed._rebuild_odds()

    await feed._handle_price_change({
        "event_type": "price_change",
        "price_changes": [{
            "asset_id": "TOKEN_YES",
            "price": "0.48",
            "size": "150",
            "side": "BUY",
        }],
    })

    assert len(feed._bids) == 2
    assert float(feed._bids[0]["price"]) == 0.50  # Still best
    assert float(feed._bids[1]["price"]) == 0.48  # New level
    assert feed._last_odds.bid_yes == 0.50


@pytest.mark.asyncio
async def test_handle_price_change_sell_remove():
    """Price change with size 0 should remove the ask level."""
    feed = PolymarketFeed("TOKEN_YES", "N", "test")
    feed._bids = [{"price": "0.50", "size": "200"}]
    feed._asks = [
        {"price": "0.55", "size": "100"},
        {"price": "0.60", "size": "200"},
    ]
    feed._rebuild_odds()

    await feed._handle_price_change({
        "event_type": "price_change",
        "price_changes": [{
            "asset_id": "N",
            "price": "0.45", # 1 - 0.45 = 0.55 ask
            "size": "0",
            "side": "BUY",
        }],
    })

    assert len(feed._asks) == 1
    assert float(feed._asks[0]["price"]) == 0.60
    assert feed._last_odds.ask_yes == 0.60


# ══════════════════════════════════════════════════════════════
# 5. Properties and Status
# ══════════════════════════════════════════════════════════════

def test_ws_connected_default_false():
    """WS should not be connected by default."""
    feed = PolymarketFeed("Y", "N", "test")
    assert feed.is_ws_connected is False


def test_book_age_ms_initial():
    """Book age should be very large when no updates received."""
    feed = PolymarketFeed("Y", "N", "test")
    assert feed.book_age_ms > 100000


def test_book_spread():
    """Book spread should be ask - bid."""
    feed = PolymarketFeed("Y", "N", "test")
    feed._bids = [{"price": "0.45", "size": "100"}]
    feed._asks = [{"price": "0.55", "size": "100"}]
    feed._rebuild_odds()

    spread = feed.get_book_spread()
    assert abs(spread - 0.10) < 0.001


def test_book_spread_none_before_data():
    """Book spread should be None before any data."""
    feed = PolymarketFeed("Y", "N", "test")
    assert feed.get_book_spread() is None


# ══════════════════════════════════════════════════════════════
# 6. Integration: Full Event Sequence
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_full_event_sequence():
    """Simulate a full sequence: snapshot → price_changes → verify book."""
    feed = PolymarketFeed("TOKEN_YES", "N", "test_market")

    # 1. Initial snapshot
    await feed._handle_book_snapshot({
        "event_type": "book",
        "asset_id": "TOKEN_YES",
        "bids": [
            {"price": "0.50", "size": "200"},
            {"price": "0.48", "size": "100"},
        ],
    })
    
    await feed._handle_book_snapshot({
        "event_type": "book",
        "asset_id": "N",
        "bids": [
            {"price": "0.48", "size": "150"}, # 0.52
            {"price": "0.45", "size": "300"}, # 0.55
        ],
    })
    assert feed._last_odds.bid_yes == 0.50
    assert feed._last_odds.ask_yes == 0.52

    # 2. New bid arrives at better price
    await feed._handle_price_change({
        "event_type": "price_change",
        "price_changes": [{
            "asset_id": "TOKEN_YES",
            "price": "0.51",
            "size": "50",
            "side": "BUY",
        }],
    })
    assert feed._last_odds.bid_yes == 0.51  # New best bid

    # 3. Best ask gets removed (filled/cancelled)
    await feed._handle_price_change({
        "event_type": "price_change",
        "price_changes": [{
            "asset_id": "N",
            "price": "0.48", # 1 - 0.48 = 0.52
            "size": "0",
            "side": "BUY",
        }],
    })
    assert feed._last_odds.ask_yes == 0.55  # New best ask

    # 4. Verify final state
    assert len(feed._bids) == 3  # 0.51, 0.50, 0.48
    assert len(feed._asks) == 1  # 0.55
    assert feed._update_count == 4
    assert abs(feed._last_odds.yes_price - (0.51 + 0.55) / 2) < 0.001
