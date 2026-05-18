"""Core Bot Orchestrator."""

import asyncio
import math
import time
from typing import Any

import structlog

from adapters.polymarket_ws import PolymarketWSAdapter, OrderBook, TradeEvent
from adapters.binance_ws import BinanceWSAdapter
from adapters.mid_reconciler import MidReconciler, ReconcilerConfig
from engine.signal_engine import SignalEngine
from engine.quoting_engine import QuotingEngine
from engine.execution_manager import ExecutionManager, LiveOrder, PlaceOrder, CancelOrder
from config.settings import Config
from core.interfaces import PolymarketClientProtocol

logger = structlog.get_logger(__name__)


class AdaptiveMarketMakerBot:
    """Core orchestrator wiring together adapters and internal engines."""
    
    def __init__(
        self,
        settings: Config,
        api_client: PolymarketClientProtocol,
        pm_ws: PolymarketWSAdapter,
        binance_ws: BinanceWSAdapter
    ):
        self.settings = settings
        self.api_client = api_client
        self.pm_ws = pm_ws
        self.binance_ws = binance_ws
        
        # Initialize Reconciler (1% tolerance)
        self.reconciler = MidReconciler(ReconcilerConfig(divergence_threshold=0.01))
        
        # Initialize Engines
        tau = -1.0 / math.log(settings.vol_lambda) if settings.vol_lambda < 1.0 else 16.15
        
        # In testing we will modify warm_up parameters
        self.signal_engine = SignalEngine(
            tau=tau,
            min_spread=settings.min_spread,
            warm_up_seconds=300.0,
            warm_up_min_obs=60
        )
        
        self.quoting_engine = QuotingEngine(
            min_spread=settings.min_spread,
            vol_mult=settings.vol_mult,
            max_inventory=settings.max_inventory,
            skew_factor=settings.skew_factor,
            emergency_factor=1.3,
            tick_size=0.001
        )
        
        self.execution_manager = ExecutionManager(
            requote_threshold=settings.requote_threshold,
            dwell_min_seconds=3.0,
            max_open_orders=settings.max_open_orders,
            order_size_usdc=settings.order_size_usdc
        )

        # Map Polymarket market_id -> Binance asset
        self.market_to_asset: dict[str, str] = {}
        for m in settings.markets:
            if "-" in m:
                asset = m.split("-")[0]
                self.market_to_asset[m] = asset

        self._running = False

    async def _safe_cancel(self, action: CancelOrder) -> None:
        """Safely execute a cancel action and update state."""
        try:
            await self.api_client.cancel_order(action.order_id, action.market_id)
            self.execution_manager.update_order_status(action.order_id, action.market_id, "cancelled")
            logger.info("cancel_success", order_id=action.order_id)
        except Exception as e:
            logger.error("cancel_failed", order_id=action.order_id, error=str(e))
            # Restore status if cancel fails so we can try again later
            self.execution_manager.update_order_status(action.order_id, action.market_id, "live")

    async def _safe_place(self, action: PlaceOrder) -> None:
        """Safely execute a place action and track live state."""
        try:
            order_id = await self.api_client.place_order(
                market_id=action.market_id,
                side=action.side,
                price=action.price,
                size=action.size
            )
            now = time.time()
            self.execution_manager.add_live_order(LiveOrder(
                id=order_id,
                market_id=action.market_id,
                side=action.side,
                price=action.price,
                size=action.size,
                created_at=now,
                status="live"
            ))
            logger.info("place_success", market_id=action.market_id, side=action.side, price=action.price, size=action.size)
        except Exception as e:
            logger.error("place_failed", market_id=action.market_id, side=action.side, error=str(e))

    async def on_binance_mid(self, asset: str, mid: float) -> None:
        """Ingest underlying spot data."""
        self.reconciler.update_spot_mid(asset, mid)

    async def on_pm_book(self, book: OrderBook) -> None:
        """Main event loop tick. Driven by Polymarket L2 updates."""
        now = time.time()
        market_id = book.market_id
        asset = self.market_to_asset.get(market_id)
        
        pm_mid = book.mid_price
        if pm_mid is None:
            return
            
        if hasattr(self.api_client, "update_book"):
            self.api_client.update_book(book)
            
        # 1. Update Reconciler and verify sanity
        if asset:
            diverged = self.reconciler.update_polymarket_mid(market_id, pm_mid)
            if diverged:
                logger.warning("bot_sanity_check_failed", asset=asset, market_id=market_id)
                # Fail open or fail closed? Usually market makers halt quoting if desynced
                return

        # 2. Update Signal Engine
        self.signal_engine.update_market(market_id, pm_mid, now)
        
        # 3. Check Warm-Up
        vol = self.signal_engine.get_market_volatility(market_id, now)
        if vol is None:
            return

        # 4. Fetch Inventory from synchronous cache (managed by API client)
        inventory = self.api_client.get_inventory(market_id)

        # 5. Calculate Quotes
        quotes = self.quoting_engine.get_quotes(pm_mid, vol, inventory)

        # 6. Generate Execution Actions
        actions = self.execution_manager.process_quotes(market_id, quotes, now)
        if not actions:
            return

        # 7. Execute Actions (Gathered per type)
        cancels = [a for a in actions if isinstance(a, CancelOrder)]
        places = [a for a in actions if isinstance(a, PlaceOrder)]

        # Dispatch cancels first (gives them a head start)
        if cancels:
            await asyncio.gather(*[self._safe_cancel(c) for c in cancels])
            
        # Dispatch places second
        if places:
            await asyncio.gather(*[self._safe_place(p) for p in places])

    async def on_reconnect(self, market_id: str) -> None:
        """Called when a market stream reconnects to sync state."""
        try:
            logger.info("bot_reconciling_inventory", market_id=market_id)
            await self.api_client.fetch_inventory(market_id)
        except Exception as e:
            logger.error("bot_inventory_sync_failed", market_id=market_id, error=str(e))

    async def on_trade(self, trade: TradeEvent) -> None:
        """Route trade events to paper client if applicable."""
        if hasattr(self.api_client, "on_trade"):
            await self.api_client.on_trade(trade)

    async def run(self) -> None:
        """Run the bot indefinitely."""
        self._running = True
        
        # Bind callbacks
        self.binance_ws.set_callback(self.on_binance_mid)
        self.pm_ws.set_callback(self.on_pm_book)
        if hasattr(self.pm_ws, "set_trade_callback"):
            self.pm_ws.set_trade_callback(self.on_trade)
        
        # Subscribe
        self.pm_ws.subscribe(self.settings.markets)
        assets = list(set(self.market_to_asset.values()))
        if assets:
            self.binance_ws.subscribe(assets)
        
        # Initial inventory sync
        for market in self.settings.markets:
            await self.on_reconnect(market)

        # Start adapters
        pm_task = asyncio.create_task(self.pm_ws.connect_and_run())
        binance_task = asyncio.create_task(self.binance_ws.connect_and_run())
        
        try:
            # Run indefinitely
            await asyncio.gather(pm_task, binance_task)
        except asyncio.CancelledError:
            self._running = False
            await self.pm_ws.close()
            await self.binance_ws.close()
