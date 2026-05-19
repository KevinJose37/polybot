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

    async def get_market(self, condition_id: str):
        class MockMarket:
            def __init__(self):
                self.question = "Will ETH be above $3000?"
                self.end_date_iso = "2024-12-31T23:59:59Z"
        return MockMarket()
    
    async def get_clob_market_info(self, condition_id: str):
        class MockToken:
            def __init__(self, t_val):
                self.t = t_val
        class MockInfo:
            def __init__(self):
                self.mts = "0.001"
                self.mos = "5.0"
                self.t = [MockToken("0xyes"), MockToken("0xno")]
        return MockInfo()


@pytest.fixture
def mock_time():
    with patch('time.time') as mock:
        # Start at 1000.0, increment by 1.0 each call
        mock.side_effect = [float(x) for x in range(1000, 2000)]
        yield mock

@pytest.mark.asyncio
async def test_bot_end_to_end_state_machine(mock_time) -> None:
    """Test the full tick-to-order pipeline."""
    # 1. Setup minimal configuration
    settings = Config(
        markets=["ETH-updown-15m-1234567890"],
        min_spread=0.006,
        vol_mult=2.0,
        vol_lambda=0.94,
        skew_factor=0.5,
        requote_threshold=0.003,
        max_open_orders=2,
        max_position_usdc=50.0,
        order_size_usdc=10.0,
        max_capital_deployed_pct=1.0,
        warm_up_seconds=0,
        warm_up_min_observations=2
    )

    client = MockPolymarketClient()
    pm_ws = PolymarketWSAdapter()
    binance_ws = BinanceWSAdapter()

    bot = AdaptiveMarketMakerBot(settings, client, pm_ws, binance_ws)

    # 2. Simulate Binance Spot Price to establish sanity
    await bot.on_binance_mid("ETH", 3000.0)
    
    bot.reconciler.update_polymarket_mid = MagicMock(return_value=False)

    # Tick 1: cold start (time 1001)
    ob1 = OrderBook("ETH-updown-15m-1234567890", bids=[(0.50, 100.0)], asks=[(0.52, 100.0)])
    await bot.on_pm_book(ob1)
    
    # Assert no orders placed yet (warm-up incomplete)
    assert len(client.placed_orders) == 0

    # Tick 2: warm up completes (time 1002)
    ob2 = OrderBook("ETH-updown-15m-1234567890", bids=[(0.50, 100.0)], asks=[(0.54, 100.0)])  # Mid is now 0.52
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
    ob3 = OrderBook("ETH-updown-15m-1234567890", bids=[(0.38, 100.0)], asks=[(0.42, 100.0)])  # Mid = 0.40
    
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
        markets=["ETH-updown-15m-1234567890"],
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
    await bot._safe_cancel(CancelOrder(order_id="123", market_id="ETH-updown-15m-1234567890"))
    
    # Test _safe_place error handling
    await bot._safe_place(PlaceOrder(market_id="ETH-updown-15m-1234567890", side="BID", price=0.50, size=10.0))

@pytest.mark.asyncio
async def test_bot_reconnect_sync() -> None:
    settings = Config(
        markets=["ETH-updown-15m-1234567890"], min_spread=0.006, vol_mult=2.0, vol_lambda=0.94,
        skew_factor=0.5, requote_threshold=0.003, max_open_orders=2,
        max_inventory=5.0, order_size_usdc=10.0
    )
    client = MockPolymarketClient()
    bot = AdaptiveMarketMakerBot(settings, client, PolymarketWSAdapter(), BinanceWSAdapter())
    
    client.fetch_inventory = MagicMock()
    await bot.on_reconnect("ETH-updown-15m-1234567890")
    client.fetch_inventory.assert_called_once_with("ETH-updown-15m-1234567890")
    
    # Test failure
    client.fetch_inventory.side_effect = Exception("Network error")
    await bot.on_reconnect("ETH-updown-15m-1234567890")  # Should log error but not raise


@pytest.mark.asyncio
async def test_bot_binance_staleness_resets_warmup() -> None:
    """[H-1] Verify that Binance spot staleness correctly triggers a warmup reset
    so the bot doesn't resume quoting with a stale volatility estimate."""
    settings = Config(
        markets=["ETH-updown-15m-1234567890"], min_spread=0.006, vol_mult=2.0,
        vol_lambda=0.94, skew_factor=0.5, requote_threshold=0.003, max_open_orders=2,
        max_inventory=5.0, order_size_usdc=10.0,
        warm_up_seconds=10.0, warm_up_min_observations=2
    )
    client = MockPolymarketClient()
    bot = AdaptiveMarketMakerBot(settings, client, PolymarketWSAdapter(), BinanceWSAdapter())

    with patch('time.time') as mock_time_func:
        # 1. Provide fresh spot and let the bot warm up
        mock_time_func.return_value = 1000.0
        await bot.on_binance_mid("ETH", 3000.0)  # Time 1000.0
        
        mock_time_func.return_value = 1001.0
        ob1 = OrderBook("ETH-updown-15m-1234567890", bids=[(0.50, 100.0)], asks=[(0.52, 100.0)])
        await bot.on_pm_book(ob1)  # Time 1001.0
        
        # 2. Complete warmup (time + 10s elapsed, observations >= 2)
        mock_time_func.return_value = 1011.0
        await bot.on_binance_mid("ETH", 3000.0)  # Keep spot fresh!
        
        mock_time_func.return_value = 1012.0
        ob2 = OrderBook("ETH-updown-15m-1234567890", bids=[(0.50, 100.0)], asks=[(0.54, 100.0)])
        await bot.on_pm_book(ob2)
        
        # Assert warmup is complete
        assert bot.signal_engines["ETH-updown-15m-1234567890"].is_market_ready("ETH-updown-15m-1234567890", 1012.0)

        # 3. Simulate Binance staleness (spot age > 10.0)
        mock_time_func.return_value = 1030.0
        # spot timestamp is still 1000.0, so age is 30.0s
        ob3 = OrderBook("ETH-updown-15m-1234567890", bids=[(0.50, 100.0)], asks=[(0.54, 100.0)])
        await bot.on_pm_book(ob3)
        
        # Warmup should now be reset!
        assert not bot.signal_engines["ETH-updown-15m-1234567890"].is_market_ready("ETH-updown-15m-1234567890", 1030.0)

@pytest.mark.asyncio
async def test_bot_run_method() -> None:
    settings = Config(
        markets=["ETH-updown-15m-1234567890"], min_spread=0.006, vol_mult=2.0, vol_lambda=0.94,
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


@pytest.mark.asyncio
async def test_fill_callback_syncs_execution_manager() -> None:
    """[C-2] Verify that when the paper client fills an order, the
    ExecutionManager is notified via the fill callback and removes the
    phantom live order. Without this fix, filled orders stay in
    ExecutionManager.live_orders forever, blocking new placements."""
    from core.paper_client import PaperPolymarketClient
    from adapters.base import TradeEvent, OrderBook
    from config.settings import LatencyConfig
    from engine.execution_manager import ExecutionManager, LiveOrder

    em = ExecutionManager(
        requote_threshold=0.01, dwell_min_seconds=3.0,
        max_open_orders=4, order_size_usdc=10.0
    )
    client = PaperPolymarketClient(
        LatencyConfig(place_mean_ms=0, place_std_ms=0,
                      cancel_mean_ms=0, cancel_std_ms=0)
    )

    # [C-2] Wire the fill callback (same pattern as papertrade.py)
    def _on_fill(order_id, market_id, remaining_size):
        if remaining_size <= 1e-6:
            em.update_order_status(order_id, market_id, "filled")

    client.set_fill_callback(_on_fill)

    # Provide an empty book so queue_ahead = 0 (instant fills on cross)
    client.update_book(OrderBook("m1", [], []))

    # Place a BID order through the paper client
    order_id = await client.place_order("m1", "BID", 0.50, 20.0)

    # Also track it in ExecutionManager (as bot._safe_place would)
    em.add_live_order(LiveOrder(
        id=order_id, market_id="m1", side="BID",
        price=0.50, size=20.0, created_at=100.0, status="live"
    ))
    assert em.get_live_count() == 1

    # Simulate a trade that crosses through our bid → fills the order
    trade = TradeEvent(market_id="m1", price=0.49, size=100.0, timestamp=200.0)
    await client.on_trade(trade)

    # Paper client should have updated inventory
    assert client.synthetic_inventory.get("m1", 0.0) == 20.0

    # ExecutionManager should have been notified — order removed
    assert em.get_live_count() == 0, (
        "ExecutionManager still has phantom live order after fill!"
    )


@pytest.mark.asyncio
async def test_paper_client_adverse_selection_penalty() -> None:
    """[C-3] Verify that adverse selection penalty is applied to fills."""
    from core.paper_client import PaperPolymarketClient
    from adapters.base import TradeEvent, OrderBook
    from config.settings import LatencyConfig

    client = PaperPolymarketClient(LatencyConfig(place_mean_ms=0, place_std_ms=0,
                                                 cancel_mean_ms=0, cancel_std_ms=0))
    client.adverse_selection_bps = 10.0  # 10 bps = 0.001 penalty

    # Empty book
    client.update_book(OrderBook("m1", [], []))

    # 1. Trade crosses through bid (worst adverse selection = 2x penalty)
    await client.place_order("m1", "BID", 0.50, 10.0)
    await client.on_trade(TradeEvent("m1", 0.45, 100.0, 100.0))

    # Expected price: 0.50 + 0.002 = 0.502
    assert client.cost_basis["m1"] == pytest.approx(10.0 * 0.502)

    # 2. Trade crosses through ask (worst adverse selection = 2x penalty)
    # Reset state
    client.synthetic_inventory["m1"] = 10.0
    client.cost_basis["m1"] = 5.0
    client.realized_pnl["m1"] = 0.0

    await client.place_order("m1", "ASK", 0.52, 10.0)
    await client.on_trade(TradeEvent("m1", 0.55, 100.0, 200.0))

    # Expected price: 0.52 - 0.002 = 0.518
    # Realized P&L: 10.0 * (0.518 - 0.50) = 0.18
    assert client.realized_pnl["m1"] == pytest.approx(0.18)
