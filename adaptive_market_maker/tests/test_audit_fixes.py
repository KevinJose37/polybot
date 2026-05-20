"""Unit and regression tests for Polymarket AMM audit fixes."""

import asyncio
import pytest
from core.paper_client import PaperPolymarketClient, PaperLiveOrder
from config.settings import LatencyConfig
from adapters.base import OrderBook, TradeEvent


def test_symmetrical_ask_fill_truncation_fix() -> None:
    """[C-2] Verify that an ASK fill larger than YES inventory correctly buys NO tokens."""
    client = PaperPolymarketClient(
        latency_config=LatencyConfig(
            place_mean_ms=0, place_std_ms=0, cancel_mean_ms=0, cancel_std_ms=0,
            market_data_mean_ms=0, market_data_std_ms=0, p_fat_tail=0
        ),
        initial_capital=1000.0
    )
    
    # 1. Test YES Token ASK path
    client.market_to_tokens["yes_token"] = ("yes_token", "no_token")
    client.market_to_tokens["no_token"] = ("yes_token", "no_token")
    
    client.inventory_yes["yes_token"] = 10.0
    client.cost_basis_yes["yes_token"] = 5.0  # entry = 0.50
    client.inventory_no["yes_token"] = 0.0
    client.cost_basis_no["yes_token"] = 0.0
    
    order = PaperLiveOrder(
        id="order_1",
        market_id="yes_token",
        side="ASK",
        price=0.60,
        size=15.0,
        remaining_size=15.0,
        queue_ahead=0.0,
        created_at=0.0
    )
    client.live_orders[order.id] = order
    
    client.process_fill(order, 15.0, 12345.67)
    
    # First 10 YES sold: realized P&L = 10 * (0.60 - 0.50) = +1.0 USDC
    # Excess 5 YES sold: converted to BID on NO for 5 shares at 1.0 - 0.60 = 0.40
    assert client.inventory_yes["yes_token"] == 0.0
    assert client.cost_basis_yes["yes_token"] == 0.0
    assert client.inventory_no["yes_token"] == 5.0
    assert client.cost_basis_no["yes_token"] == pytest.approx(2.0)
    assert client.realized_pnl["yes_token"] == pytest.approx(1.0)
    assert client.get_inventory("yes_token") == -5.0

    # 2. Test NO Token ASK path
    client.inventory_no["yes_token"] = 10.0
    client.cost_basis_no["yes_token"] = 4.0  # entry = 0.40
    client.inventory_yes["yes_token"] = 0.0
    client.cost_basis_yes["yes_token"] = 0.0
    client.realized_pnl["yes_token"] = 0.0
    
    order2 = PaperLiveOrder(
        id="order_2",
        market_id="no_token",
        side="ASK",
        price=0.70,
        size=15.0,
        remaining_size=15.0,
        queue_ahead=0.0,
        created_at=0.0
    )
    client.live_orders[order2.id] = order2
    
    client.process_fill(order2, 15.0, 12345.67)
    
    # First 10 NO sold: realized P&L = 10 * (0.70 - 0.40) = +3.0 USDC
    # Excess 5 NO sold: converted to BID on YES for 5 shares at 1.0 - 0.70 = 0.30
    assert client.inventory_no["yes_token"] == 0.0
    assert client.cost_basis_no["yes_token"] == 0.0
    assert client.inventory_yes["yes_token"] == 5.0
    assert client.cost_basis_yes["yes_token"] == pytest.approx(1.5)
    assert client.realized_pnl["yes_token"] == pytest.approx(3.0)


def test_update_inventory_cache_reconnect_cost_basis_scale() -> None:
    """[C-3] Verify cost basis scales appropriately during inventory updates."""
    client = PaperPolymarketClient(
        latency_config=LatencyConfig(
            place_mean_ms=0, place_std_ms=0, cancel_mean_ms=0, cancel_std_ms=0,
            market_data_mean_ms=0, market_data_std_ms=0, p_fat_tail=0
        ),
        initial_capital=1000.0
    )
    client.market_to_tokens["yes_token"] = ("yes_token", "no_token")
    
    # 1. Scale YES position down
    client.inventory_yes["yes_token"] = 10.0
    client.cost_basis_yes["yes_token"] = 6.0  # entry = 0.60
    
    client.update_inventory_cache("yes_token", 5.0)
    assert client.inventory_yes["yes_token"] == 5.0
    assert client.cost_basis_yes["yes_token"] == pytest.approx(3.0)
    
    # 2. Scale NO position up
    client.inventory_yes["yes_token"] = 0.0
    client.cost_basis_yes["yes_token"] = 0.0
    client.inventory_no["yes_token"] = 10.0
    client.cost_basis_no["yes_token"] = 4.0  # entry = 0.40
    
    client.update_inventory_cache("yes_token", -15.0)
    assert client.inventory_no["yes_token"] == 15.0
    assert client.cost_basis_no["yes_token"] == pytest.approx(6.0)


class MockAPIClient:
    def __init__(self) -> None:
        self.calls = 0
        
    async def get_clob_market_info(self, condition_id: str):
        self.calls += 1
        await asyncio.sleep(0.05)  # yield control
        class MockToken:
            t = "yes_token"
        class MockInfo:
            mts = "0.001"
            mos = "10.0"
            t = [MockToken(), MockToken()]
        return MockInfo()

    async def get_market(self, condition_id: str):
        class MockMarket:
            question = "Will ETH be above 3000?"
            end_date_iso = "2026-12-31T23:59:59Z"
        return MockMarket()
        
    def update_book(self, book: OrderBook) -> None:
        pass


@pytest.mark.asyncio
async def test_market_context_concurrent_initialization_guard() -> None:
    """[C-4] Verify re-entry protection blocks duplicate context setups."""
    from core.bot import AdaptiveMarketMakerBot
    from config.settings import Config
    
    settings = Config.model_validate({
        "markets": ["ETH-5m"],
        "total_capital": 1000.0,
        "max_capital_deployed_pct": 0.5,
        "active_token_ids": ["yes_token"],
    })
    
    api_client = MockAPIClient()
    
    class MockWS:
        def subscribe(self, markets) -> None:
            pass
            
    bot = AdaptiveMarketMakerBot(settings, api_client, MockWS(), MockWS())
    bot.yes_tokens.add("yes_token")
    
    book = OrderBook(market_id="yes_token", bids=[(0.50, 100.0)], asks=[(0.51, 100.0)])
    
    # Ingest concurrently
    await asyncio.gather(
        bot.on_pm_book(book),
        bot.on_pm_book(book)
    )
    
    assert api_client.calls == 1


@pytest.mark.asyncio
async def test_l2_queue_cancellation_heuristic() -> None:
    """[H-1] Verify L2 depth cancellation heuristic clamps order queue_ahead."""
    client = PaperPolymarketClient(
        latency_config=LatencyConfig(
            place_mean_ms=0, place_std_ms=0, cancel_mean_ms=0, cancel_std_ms=0,
            market_data_mean_ms=0, market_data_std_ms=0, p_fat_tail=0
        ),
        initial_capital=1000.0
    )
    
    order = PaperLiveOrder(
        id="order_1",
        market_id="yes_token",
        side="BID",
        price=0.50,
        size=10.0,
        remaining_size=10.0,
        queue_ahead=100.0,
        created_at=0.0
    )
    client.live_orders[order.id] = order
    
    # 1. Depth drops to 40.0 shares
    book = OrderBook(market_id="yes_token", bids=[(0.50, 40.0)], asks=[])
    client.update_book(book)
    
    trade = TradeEvent(market_id="yes_token", price=0.52, size=10.0, timestamp=123.45)
    await client.on_trade(trade)
    assert order.queue_ahead == 40.0
    
    # 2. Level completely gone (depth becomes 0)
    book2 = OrderBook(market_id="yes_token", bids=[(0.49, 10.0)], asks=[])
    client.update_book(book2)
    
    await client.on_trade(trade)
    assert order.queue_ahead == 0.0
