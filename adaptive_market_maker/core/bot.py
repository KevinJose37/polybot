"""Core Bot Orchestrator."""

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from adapters.polymarket_ws import PolymarketWSAdapter, OrderBook, TradeEvent
from adapters.binance_ws import BinanceWSAdapter
from adapters.mid_reconciler import MidReconciler, ReconcilerConfig, parse_strike_from_question
from engine.signal_engine import SignalEngine
from engine.quoting_engine import QuotingEngine
from engine.execution_manager import ExecutionManager, LiveOrder, PlaceOrder, CancelOrder
from config.settings import Config
from core.interfaces import PolymarketClientProtocol, MarketContext
from core.portfolio import PortfolioManager
from market_discovery.oracle_monitor import OracleMonitor

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
        
        # Initialize Reconciler (6% tolerance, matching ReconcilerConfig default)
        self.reconciler = MidReconciler(ReconcilerConfig(divergence_threshold=0.06))
        
        # Initialize Engine stores (instantiated per-market later)
        self.signal_engines: dict[str, SignalEngine] = {}
        self.quoting_engines: dict[str, QuotingEngine] = {}

        self.portfolio_manager = PortfolioManager(
            max_capital_deployed_pct=settings.max_capital_deployed_pct,
            total_capital=settings.total_capital
        )
        self.market_contexts: dict[str, MarketContext] = {}
        self.price_cache: dict[str, float] = {}
        
        self.oracle_monitors: dict[str, OracleMonitor] = {}
        for underlying in ["ETH", "BTC", "SOL"]:
            threshold = 0.01 if underlying == "SOL" else 0.005
            feed = settings.chainlink_feeds.get(underlying, "")
            self.oracle_monitors[underlying] = OracleMonitor(
                underlying=underlying,
                feed_address=feed,
                deviation_threshold=threshold,
                oracle_pause_seconds=settings.oracle_pause_seconds,
                rpc_url=settings.polygon_rpc_url,
                pause_cleared_after=settings.oracle_pause_cooldown_seconds
            )
        
        self.execution_manager = ExecutionManager(
            requote_threshold=settings.requote_threshold,
            dwell_min_seconds=settings.dwell_min_seconds,
            max_open_orders=settings.max_open_orders,
            order_size_usdc=settings.order_size_usdc,
            cancel_cooldown_seconds=settings.cancel_cooldown_seconds,
            requote_cooldown_seconds=settings.requote_cooldown_seconds,
        )

        # Map Polymarket market_id -> Binance asset
        self.market_to_asset: dict[str, str] = {}
        for m in settings.markets:
            if "-" in m:
                asset = m.split("-")[0]
                self.market_to_asset[m] = asset

        self._running = False
        # F-07: Markets currently undergoing inventory reconciliation after reconnect
        self._reconciling_markets: set[str] = set()
        # F-06: Markets that have successfully completed context initialization
        self._initialized_markets: set[str] = set()
        self._failed_markets: set[str] = set()

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
            # Add latency buffer to local send time
            latency_buffer = self.settings.latency.place_mean_ms / 1000.0
            now = time.time() + latency_buffer
            
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

    async def remove_market(self, market_id: str) -> None:
        """[H-4] Safely teardown bot state for an expired or removed market."""
        logger.info("bot_removing_market", market_id=market_id)
        
        # 1. Cancel any live orders
        cancels = [
            CancelOrder(order_id=o.id, market_id=market_id)
            for o in self.execution_manager.live_orders.get(market_id, [])
            if o.status == "live"
        ]
        if cancels:
            await asyncio.gather(*[self._safe_cancel(c) for c in cancels])
            
        # 2. Cleanup state dicts
        self.market_contexts.pop(market_id, None)
        self.signal_engines.pop(market_id, None)
        self.quoting_engines.pop(market_id, None)
        self.price_cache.pop(market_id, None)
        self._initialized_markets.discard(market_id)
        self._reconciling_markets.discard(market_id)
        
        if market_id in self.execution_manager.live_orders:
            del self.execution_manager.live_orders[market_id]
            
        # Note: Oracle monitors are tracked by asset (e.g. "ETH"), which might be
        # shared across multiple markets. We don't remove them here unless we refactor
        # reference counting.
        
        asset = self.market_to_asset.pop(market_id, None)
        if asset:
            logger.debug("bot_removed_asset_mapping", market_id=market_id, asset=asset)

    async def on_binance_mid(self, asset: str, mid: float, recv_time: float | None = None) -> None:
        """Ingest underlying spot data."""
        self.reconciler.update_spot_mid(asset, mid, timestamp=recv_time)
        if asset in self.oracle_monitors:
            self.oracle_monitors[asset].on_binance_tick(mid)

    async def on_pm_book(self, book: OrderBook) -> None:
        """Main event loop tick. Driven by Polymarket L2 updates."""
        now = time.time()
        market_id = book.market_id
        asset = self.market_to_asset.get(market_id)
        
        pm_mid = book.mid_price
        if pm_mid is None:
            return
            
        self.price_cache[market_id] = pm_mid
            
        if hasattr(self.api_client, "update_book"):
            self.api_client.update_book(book)
            
        if market_id in self._failed_markets:
            return
            
        # F-07: Skip quoting while market is reconciling after a reconnect
        if market_id in self._reconciling_markets:
            return

        if market_id not in self.market_contexts:
            await self.initialize_market_context(market_id)
            asset = self.market_to_asset.get(market_id)
            
        ctx = self.market_contexts.get(market_id)
        if not ctx:
            # F-06: Log clearly when context is missing so it's not silently ignored
            if market_id not in self._initialized_markets:
                logger.warning("bot_skipping_uninitalized_market", market_id=market_id)
            return

        # Clamp pm_mid to valid limits based on tick size
        pm_mid = max(ctx.tick_size, min(1.0 - ctx.tick_size, pm_mid))

        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
        expiry_paused = (ctx.expiry_utc - now_dt).total_seconds() <= self.settings.expiry_pause_seconds
        
        oracle_monitor = self.oracle_monitors.get(asset) if asset else None
        oracle_paused = False
        if oracle_monitor:
            oracle_paused = (
                oracle_monitor.pause_event.is_set() or
                oracle_monitor.seconds_until_heartbeat(now) <= self.settings.oracle_pause_seconds
            )

        # 0.5 Binance spot staleness guard (F-03: must come BEFORE EWMA update)
        spot_stale = False
        if asset:
            spot_age = now - self.reconciler.spot_timestamps.get(asset, 0)
            if spot_age > 10.0:
                spot_stale = True
                # [H-1] If Binance spot is stale, we must reset the warm-up trackers.
                # Otherwise, when Binance reconnects, the bot would immediately resume
                # quoting with a frozen/stale volatility estimate.
                self.signal_engines[market_id].reset_warmup(market_id, now)

        # 1. Update Signal Engine (only if not structurally paused)
        if not expiry_paused and not oracle_paused and not spot_stale:
            self.signal_engines[market_id].update_market(market_id, pm_mid, now)
        
        # 2. Check Warm-Up
        vol = self.signal_engines[market_id].get_market_volatility(market_id, now)
        if vol is None:
            return

        # 3. Update Reconciler and verify sanity
        if asset:
            time_to_expiry_years = max(0.0001, (ctx.expiry_utc - now_dt).total_seconds() / (365 * 24 * 3600))
            diverged = self.reconciler.update_polymarket_mid(
                market_id=market_id,
                pm_mid=pm_mid,
                asset=asset,
                strike=ctx.strike_price,
                sigma=vol,
                time_to_expiry_years=time_to_expiry_years
            )
            if diverged:
                logger.warning("bot_sanity_check_failed", asset=asset, market_id=market_id)
                # EMERGENCY: Cancel all live orders on this market immediately
                cancels = [
                    CancelOrder(order_id=o.id, market_id=market_id)
                    for o in self.execution_manager.live_orders.get(market_id, [])
                    if o.status == "live"
                ]
                if cancels:
                    await asyncio.gather(*[self._safe_cancel(c) for c in cancels])
                return

        # 4. Fetch Inventory from synchronous cache (managed by API client)
        inventory = self.api_client.get_inventory(market_id)

        # 4.5 Global Portfolio Check and Pauses
        # [H-1] Use active_token_ids for runtime market list
        active_markets = self.settings.active_token_ids or self.settings.markets
        all_inventories = {m: self.api_client.get_inventory(m) for m in active_markets}
        open_orders_usdc = float(self.execution_manager.get_live_count() * self.settings.order_size_usdc)
        current_prices = self.price_cache
        
        is_paused = False
        if self.portfolio_manager.is_capacity_exceeded(all_inventories, open_orders_usdc, current_prices):
            is_paused = True

        # [H-4] Drawdown kill-switch
        if hasattr(self.api_client, 'check_drawdown') and self.api_client.check_drawdown(current_prices):
            is_paused = True
            
        if expiry_paused or oracle_paused:
            is_paused = True
            
        if spot_stale:
            is_paused = True
        
        if is_paused:
            quotes = self.quoting_engines[market_id].get_quotes(pm_mid, vol, inventory, ctx.tick_size)
            quotes.bid = None
            quotes.ask = None
        else:
            # 5. Calculate Quotes
            quotes = self.quoting_engines[market_id].get_quotes(pm_mid, vol, inventory, ctx.tick_size)

        # 6. Generate Execution Actions
        min_order_size = ctx.min_order_size if ctx else self.settings.min_order_size
        actions = self.execution_manager.process_quotes(market_id, quotes, now, min_order_size=min_order_size)
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

    async def initialize_market_context(self, condition_id: str) -> None:
        """Initialize market context metadata via REST."""
        try:
            info = await self.api_client.get_clob_market_info(condition_id)
            market = await self.api_client.get_market(condition_id)

            strike = parse_strike_from_question(market.question)
            expiry_raw = datetime.fromisoformat(market.end_date_iso.replace('Z', '+00:00'))
            expiry = expiry_raw if expiry_raw.tzinfo else expiry_raw.replace(tzinfo=timezone.utc)
            tick_size = float(info.mts)
            
            valid_tick_sizes = {0.1, 0.01, 0.001, 0.0001}
            if tick_size not in valid_tick_sizes:
                raise ValueError(f"Unexpected tick size {tick_size} for market {condition_id}")

            asset = self.market_to_asset.get(condition_id, "ETH")
            
            binance_spot = self.reconciler.spot_mids.get(asset)
            if strike is not None and binance_spot and binance_spot > 0:
                if abs(strike - binance_spot) / binance_spot > 0.5:
                    raise ValueError(f"Strike {strike} is > 50% away from spot {binance_spot} for {condition_id}")

            chainlink_feed = self.settings.chainlink_feeds.get(asset, "")

            # F-05: Resolve overrides by market slug key (e.g., "ETH-5m"), then
            # fall back to asset key (e.g., "ETH"), then to condition_id.
            # This allows per-window overrides to take priority over per-asset ones.
            market_slug_key = None
            for mk in self.settings.markets:
                if "-" in mk and self.market_to_asset.get(mk) == asset:
                    # Check if this market key maps to the same condition_id
                    # For lifecycle-discovered markets, condition_id IS the token ID,
                    # so we check if this market key is the condition_id itself
                    if mk == condition_id:
                        market_slug_key = mk
                        break
            # If we couldn't find a slug key from markets list, try parsing
            # the condition_id itself (it may be in slug format like "ETH-5m")
            if not market_slug_key and "-" in condition_id:
                market_slug_key = condition_id
            
            override = None
            # Priority 1: Full market slug key (e.g., "ETH-5m")
            if market_slug_key:
                override = self.settings.market_overrides.get(market_slug_key)
            # Priority 2: Asset-level key (e.g., "ETH")
            if not override:
                override = self.settings.market_overrides.get(asset, None)
            # Priority 3: Raw condition_id (token ID)
            if not override:
                override = self.settings.market_overrides.get(condition_id)
            min_spread = override.min_spread if override and override.min_spread else self.settings.min_spread
            vol_mult = override.vol_mult if override and override.vol_mult else self.settings.vol_mult
            max_pos_usdc = override.max_position_usdc if override and override.max_position_usdc else self.settings.max_position_usdc

            tau = -1.0 / math.log(self.settings.vol_lambda) if self.settings.vol_lambda < 1.0 else 16.15
            self.signal_engines[condition_id] = SignalEngine(
                tau=tau,
                min_spread=min_spread,
                warm_up_seconds=self.settings.warm_up_seconds,
                warm_up_min_obs=self.settings.warm_up_min_observations
            )
            self.quoting_engines[condition_id] = QuotingEngine(
                min_spread=min_spread,
                vol_mult=vol_mult,
                max_position_usdc=max_pos_usdc,
                skew_factor=self.settings.skew_factor,
                emergency_factor=self.settings.emergency_factor
            )

            self.market_contexts[condition_id] = MarketContext(
                condition_id=condition_id,
                tick_size=tick_size,
                min_order_size=float(info.mos),
                expiry_utc=expiry,
                chainlink_feed=chainlink_feed,
                strike_price=strike,
                token_id_yes=info.t[0].t,
                token_id_no=info.t[1].t,
            )
            # F-06: Track successful initialization
            self._initialized_markets.add(condition_id)
            logger.info("initialized_market_context", market_id=condition_id, tick_size=tick_size, strike=strike)
        except Exception as e:
            logger.error("market_context_init_failed", market_id=condition_id, error=str(e))
            self._failed_markets.add(condition_id)

    async def on_reconnect(self, market_id: str) -> None:
        """Called when a market stream reconnects to sync state."""
        if market_id in self._failed_markets:
            return
            
        # F-07: Block quoting on this market until reconciliation completes
        self._reconciling_markets.add(market_id)
        logger.info("bot_reconciling_inventory", market_id=market_id)
        try:
            await self.initialize_market_context(market_id)
        except Exception as e:
            logger.error("bot_market_context_init_failed", market_id=market_id, error=str(e))
            
        try:
            inv = await self.api_client.fetch_inventory(market_id)
            logger.info("bot_inventory_reconciled", market_id=market_id, inventory=inv)
        except Exception as e:
            logger.error("bot_inventory_sync_failed", market_id=market_id, error=str(e))
        finally:
            # F-07: Always clear reconciliation flag, even on failure
            self._reconciling_markets.discard(market_id)

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
        if hasattr(self.pm_ws, "set_reconnect_callback"):
            self.pm_ws.set_reconnect_callback(self.on_reconnect)
        
        # Subscribe — [H-1] use active_token_ids for runtime subscriptions
        active_markets = self.settings.active_token_ids or self.settings.markets
        self.pm_ws.subscribe(active_markets)
        assets = list(set(self.market_to_asset.values()))
        if assets:
            self.binance_ws.subscribe(assets)
        
        # Initial inventory sync
        for market in active_markets:
            await self.on_reconnect(market)

        # Start adapters
        pm_task = asyncio.create_task(self.pm_ws.connect_and_run())
        binance_task = asyncio.create_task(self.binance_ws.connect_and_run())
        
        for monitor in self.oracle_monitors.values():
            await monitor.start_polling()
        
        try:
            # Run indefinitely
            await asyncio.gather(pm_task, binance_task)
        except asyncio.CancelledError:
            self._running = False
            await self.pm_ws.close()
            await self.binance_ws.close()
            for monitor in self.oracle_monitors.values():
                await monitor.stop_polling()
