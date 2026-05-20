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
    """Test that MidReconciler flags probability divergences correctly."""
    config = ReconcilerConfig(divergence_threshold=0.10)
    reconciler = MidReconciler(config)
    
    # Set spot price
    reconciler.update_spot_mid("ETH", 3000.0)
    
    # Layer 1 test: spot below strike, but poly_mid is bullish (0.7)
    # Strike 3200, spot 3000
    res1 = reconciler.update_polymarket_mid("0x123", 0.70, "ETH", 3200.0, 0.5, 0.1)
    assert res1

    # Layer 2 test: theoretical probability calculation divergence
    # Strike 3000, spot 3000 -> ATM -> theoretical prob approx 0.5
    # Let poly_mid be 0.65 -> divergence 0.15 > 0.10
    res2 = reconciler.update_polymarket_mid("0x124", 0.65, "ETH", 3000.0, 0.5, 0.1)
    assert res2
    
    # Not diverged
    res3 = reconciler.update_polymarket_mid("0x125", 0.52, "ETH", 3000.0, 0.5, 0.1)
    assert not res3


# ── [C-1] Tests for REST client get_market / get_clob_market_info ──

MOCK_GAMMA_MARKET = {
    "question": "Will ETH be above $3,200 at 10:00 AM UTC?",
    "endDate": "2026-06-01T10:00:00Z",
    "conditionId": "0xcondition_abc",
    "clobTokenIds": '["0xtoken_yes", "0xtoken_no"]',
    "outcomes": '["Yes", "No"]',
    "minimumTickSize": "0.01",
    "minimumOrderSize": "5.0",
    "slug": "eth-updown-5m-12345",
    "active": True,
    "closed": False,
}


@pytest.fixture
def rest_client_with_mock():
    """Create a PolymarketRESTClient with _fetch_market_data mocked."""
    from unittest.mock import AsyncMock
    from adapters.polymarket_rest import PolymarketRESTClient

    client = PolymarketRESTClient()
    client._fetch_market_data = AsyncMock(return_value=MOCK_GAMMA_MARKET)
    return client


@pytest.mark.asyncio
async def test_get_market_returns_question_and_date(rest_client_with_mock):
    """[C-1] get_market() returns MarketInfo with question and end_date_iso."""
    from adapters.polymarket_rest import MarketInfo

    result = await rest_client_with_mock.get_market("0xtoken_yes")
    assert isinstance(result, MarketInfo)
    assert result.question == "Will ETH be above $3,200 at 10:00 AM UTC?"
    assert result.end_date_iso == "2026-06-01T10:00:00Z"


@pytest.mark.asyncio
async def test_get_clob_market_info_returns_tick_and_tokens(rest_client_with_mock):
    """[C-1] get_clob_market_info() returns ClobMarketInfo with correct fields."""
    from adapters.polymarket_rest import ClobMarketInfo

    result = await rest_client_with_mock.get_clob_market_info("0xtoken_yes")
    assert isinstance(result, ClobMarketInfo)
    assert result.mts == "0.01"
    assert result.mos == "5.0"
    assert len(result.t) == 2
    assert result.t[0].t == "0xtoken_yes"
    assert result.t[1].t == "0xtoken_no"


@pytest.mark.asyncio
async def test_get_market_raises_on_missing_question():
    """[C-1] get_market() raises ValueError when question is missing."""
    from unittest.mock import AsyncMock
    from adapters.polymarket_rest import PolymarketRESTClient

    client = PolymarketRESTClient()
    client._fetch_market_data = AsyncMock(return_value={"endDate": "2026-01-01T00:00:00Z"})

    with pytest.raises(ValueError, match="missing 'question'"):
        await client.get_market("0xbad")


@pytest.mark.asyncio
async def test_get_clob_market_info_raises_on_missing_tokens():
    """[C-1] get_clob_market_info() raises if fewer than 2 tokens."""
    from unittest.mock import AsyncMock
    from adapters.polymarket_rest import PolymarketRESTClient

    client = PolymarketRESTClient()
    client._fetch_market_data = AsyncMock(return_value={
        "minimumTickSize": "0.01",
        "minimumOrderSize": "5.0",
        "clobTokenIds": '["only_one"]',
    })

    with pytest.raises(ValueError, match="expected ≥2 tokens"):
        await client.get_clob_market_info("0xbad")


@pytest.mark.asyncio
async def test_fetch_market_data_not_found():
    """[C-1] _fetch_market_data() raises ValueError when API returns no results."""
    from unittest.mock import AsyncMock, MagicMock
    from adapters.polymarket_rest import PolymarketRESTClient

    client = PolymarketRESTClient()

    # Mock session that returns empty arrays for both strategies
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=[])
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.closed = False
    client._session = mock_session

    with pytest.raises(ValueError, match="Market not found"):
        await client._fetch_market_data("0xnonexistent")


@pytest.mark.asyncio
async def test_fetch_market_data_caches_result():
    """[C-1] _fetch_market_data() caches results to avoid double-fetch."""
    from unittest.mock import AsyncMock
    from adapters.polymarket_rest import PolymarketRESTClient

    client = PolymarketRESTClient()
    client._market_cache["0xcached"] = MOCK_GAMMA_MARKET

    # Should return cached data without hitting the network
    result = await client._fetch_market_data("0xcached")
    assert result["question"] == "Will ETH be above $3,200 at 10:00 AM UTC?"


@pytest.mark.asyncio
async def test_oracle_monitor_task_cleanup() -> None:
    """Test that OracleMonitor cleans up background tasks on stop or completion."""
    import asyncio
    from market_discovery.oracle_monitor import OracleMonitor
    
    monitor = OracleMonitor(
        underlying="ETH",
        feed_address="0x123",
        deviation_threshold=0.01,
        oracle_pause_seconds=60,
        rpc_url="http://dummy"
    )
    
    # Mock loop methods so start_polling doesn't run blocking infinite loops
    async def dummy_loop():
        await asyncio.sleep(0.01)
        
    monitor._poll_loop = dummy_loop
    monitor._monitor_loop = dummy_loop
    
    assert len(monitor._tasks) == 0
    
    await monitor.start_polling()
    # Wait briefly for tasks to complete (since we mocked them to sleep 0.01s)
    await asyncio.sleep(0.05)
    
    # Tasks should have finished and been discarded via done_callback
    assert len(monitor._tasks) == 0
    
    # Reset loop methods to infinite wait
    async def infinite_loop():
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
            
    monitor._poll_loop = infinite_loop
    monitor._monitor_loop = infinite_loop
    monitor._running = False # Reset flag since mocked loops completed and left it True
    
    await monitor.start_polling()
    assert len(monitor._tasks) == 2
    
    await monitor.stop_polling()
    assert len(monitor._tasks) == 0
    assert not monitor._running


def test_oracle_monitor_fast_path_pause() -> None:
    """Test that OracleMonitor pause is triggered immediately via fast-path tick evaluation."""
    from market_discovery.oracle_monitor import OracleMonitor
    
    monitor = OracleMonitor(
        underlying="ETH",
        feed_address="0x123",
        deviation_threshold=0.01, # 1%
        oracle_pause_seconds=60,
        rpc_url="http://dummy"
    )
    
    monitor.last_chainlink_price = 3000.0
    
    # 0.5% deviation -> no pause (threshold is 0.7 * 1% = 0.7%)
    monitor.on_binance_tick(3015.0)
    assert not monitor.pause_event.is_set()
    
    # 0.8% deviation -> triggers fast path pause (> 0.7%)
    monitor.on_binance_tick(3025.0)
    assert monitor.pause_event.is_set()
    assert monitor._pause_triggered_at is not None
