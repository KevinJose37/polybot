"""End-to-End tests for the Adaptive Market Maker Bot."""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, patch

from config.settings import Config
from adapters.polymarket_ws import PolymarketWSAdapter, OrderBook
from adapters.binance_ws import BinanceWSAdapter
from core.bot import AdaptiveMarketMakerBot
from core.interfaces import PolymarketClientProtocol
from engine.execution_manager import CancelOrder, PlaceOrder


class MockPolymarketClient(PolymarketClientProtocol):
    def __init__(self):
        self.inventory_cache: dict[str, float] = {}
        self.placed_orders: list[dict] = []
        self.cancelled_orders: list[str] = []
        self._order_id_counter = 0

    async def fetch_inventory(self, market_id: str) -> float:
        # Mock fetching from network
        inv = self.inventory_cache.get(market_id, 0.0)
        return inv

    def get_inventory(self, market_id: str) -> float:
        return self.inventory_cache.get(market_id, 0.0)

    async def place_order(self, market_id: str, side: str, price: float, size: float) -> str:
        self._order_id_counter += 1
        order_id = f"mock_order_{self._order_id_counter}"
        self.placed_orders.append({
            "id": order_id,
            "market_id": market_id,
            "side": side,
            "price": price,
            "size": size
        })
        return order_id

    async def cancel_order(self, order_id: str, market_id: str) -> bool:
        self.cancelled_orders.append(order_id)
        return True


@pytest.fixture
def mock_time():
    with patch('time.time') as mock:
        # Start at 1000.0, increment by 1.0 each call
        mock.side_effect = range(1000, 2000)
        yield mock

@pytest.mark.asyncio
async def test_bot_end_to_end_state_machine(mock_time) -> None:
    """Test the full tick-to-order pipeline."""
    # 1. Setup minimal configuration
    settings = Config(
        markets=["ETH-24H"],
        min_spread=0.006,
        vol_mult=2.0,
        vol_lambda=0.94,
        skew_factor=0.5,
        requote_threshold=0.003,
        max_open_orders=2,
        max_inventory=5.0,
        order_size_usdc=10.0
    )

    client = MockPolymarketClient()
    pm_ws = PolymarketWSAdapter()
    binance_ws = BinanceWSAdapter()

    bot = AdaptiveMarketMakerBot(settings, client, pm_ws, binance_ws)
    
    # Overwrite warm-up to be extremely fast for testing
    bot.signal_engine.warm_up_seconds = 0.0
    bot.signal_engine.warm_up_min_obs = 2

    # 2. Simulate Binance Spot Price to establish sanity
    await bot.on_binance_mid("ETH", 3000.0)
    
    bot.reconciler.update_polymarket_mid = MagicMock(return_value=False)

    # Tick 1: cold start (time 1001)
    ob1 = OrderBook("ETH-24H", bids=[(0.50, 100.0)], asks=[(0.52, 100.0)])
    await bot.on_pm_book(ob1)
    
    # Assert no orders placed yet (warm-up incomplete)
    assert len(client.placed_orders) == 0

    # Tick 2: warm up completes (time 1002)
    ob2 = OrderBook("ETH-24H", bids=[(0.50, 100.0)], asks=[(0.54, 100.0)])  # Mid is now 0.52
    await bot.on_pm_book(ob2)

    # Now the bot should have calculated quotes and placed orders!
    assert len(client.placed_orders) == 2
    sides = {o["side"] for o in client.placed_orders}
    assert sides == {"BID", "ASK"}
    
    # Get the ID of the placed bid
    bid_order = next(o for o in client.placed_orders if o["side"] == "BID")
    ask_order = next(o for o in client.placed_orders if o["side"] == "ASK")
    
    assert bid_order["price"] < 0.52
    assert ask_order["price"] > 0.52
    
    # Tick 3: Price moves massively, triggering asymmetric cancel/replace (time 1003)
    ob3 = OrderBook("ETH-24H", bids=[(0.38, 100.0)], asks=[(0.42, 100.0)])  # Mid = 0.40
    
    # Dwell time prevents replacement unless emergency OR adverse selection.
    # Target < live (worse quote), so it instantly cancels without dwell check?
    # Wait, dwell blocks EVERYTHING! We must fast-forward created_at internally.
    for orders in bot.execution_manager.live_orders.values():
        for o in orders:
            o.created_at -= 10.0  # Push 10 seconds into the past

    await bot.on_pm_book(ob3)
    
    # It should have cancelled the old orders and placed new ones
    assert len(client.cancelled_orders) == 2
    assert len(client.placed_orders) == 4 # 2 old + 2 new


@pytest.mark.asyncio
async def test_bot_safe_execution_failures() -> None:
    settings = Config(
        markets=["ETH-24H"],
        min_spread=0.006,
        vol_mult=2.0,
        vol_lambda=0.94,
        skew_factor=0.5,
        requote_threshold=0.003,
        max_open_orders=2,
        max_inventory=5.0,
        order_size_usdc=10.0
    )
    client = MockPolymarketClient()
    bot = AdaptiveMarketMakerBot(settings, client, PolymarketWSAdapter(), BinanceWSAdapter())
    
    # Force client to raise exceptions
    client.place_order = MagicMock(side_effect=Exception("Place failed"))
    client.cancel_order = MagicMock(side_effect=Exception("Cancel failed"))
    
    # Test _safe_cancel error handling
    await bot._safe_cancel(CancelOrder(order_id="123", market_id="ETH-24H"))
    
    # Test _safe_place error handling
    await bot._safe_place(PlaceOrder(market_id="ETH-24H", side="BID", price=0.50, size=10.0))

@pytest.mark.asyncio
async def test_bot_reconnect_sync() -> None:
    settings = Config(
        markets=["ETH-24H"], min_spread=0.006, vol_mult=2.0, vol_lambda=0.94,
        skew_factor=0.5, requote_threshold=0.003, max_open_orders=2,
        max_inventory=5.0, order_size_usdc=10.0
    )
    client = MockPolymarketClient()
    bot = AdaptiveMarketMakerBot(settings, client, PolymarketWSAdapter(), BinanceWSAdapter())
    
    client.fetch_inventory = MagicMock()
    await bot.on_reconnect("ETH-24H")
    client.fetch_inventory.assert_called_once_with("ETH-24H")
    
    # Test failure
    client.fetch_inventory.side_effect = Exception("Network error")
    await bot.on_reconnect("ETH-24H")  # Should log error but not raise

@pytest.mark.asyncio
async def test_bot_run_method() -> None:
    settings = Config(
        markets=["ETH-24H"], min_spread=0.006, vol_mult=2.0, vol_lambda=0.94,
        skew_factor=0.5, requote_threshold=0.003, max_open_orders=2,
        max_inventory=5.0, order_size_usdc=10.0
    )
    client = MockPolymarketClient()
    pm_ws = PolymarketWSAdapter()
    binance_ws = BinanceWSAdapter()
    bot = AdaptiveMarketMakerBot(settings, client, pm_ws, binance_ws)
    
    from unittest.mock import AsyncMock
    # Mock the run to just cancel immediately
    pm_ws.connect_and_run = AsyncMock(side_effect=asyncio.CancelledError())
    binance_ws.connect_and_run = AsyncMock()
    pm_ws.close = AsyncMock()
    binance_ws.close = AsyncMock()
    
    await bot.run()
    
    assert bot._running is False
    pm_ws.close.assert_called_once()
    binance_ws.close.assert_called_once()
