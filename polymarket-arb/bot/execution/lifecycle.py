"""
Market lifecycle and discovery manager.
"""
import asyncio
import structlog

from bot.settings import Settings
from bot.api.polymarket import PolymarketRESTClient
from bot.api.websocket_client import PolymarketWSClient
from bot.market_discovery.discovery import MarketDiscoveryService
from bot.market_discovery.market_relationships import build_topology, MarketTopology
from bot.orderbook.local_book import LocalOrderBook
from bot.execution.position_manager import PositionManager
from bot.execution.fill_manager import FillManager
from bot.execution.executor import ExecutorProtocol
from bot.arbitrage.scanner import ArbitrageScanner
from bot.api.schemas import MarketSnapshot

logger = structlog.get_logger(__name__)


class LifecycleManager:
    def __init__(
        self,
        settings: Settings,
        discovery: MarketDiscoveryService,
        rest_api: PolymarketRESTClient,
        ws_client: PolymarketWSClient,
        scanner: ArbitrageScanner,
        position_manager: PositionManager,
        fill_manager: FillManager,
        executor: ExecutorProtocol,
        orderbooks: dict[str, LocalOrderBook],
        fee_rates: dict[str, float]
    ):
        self.settings = settings
        self.discovery = discovery
        self.rest_api = rest_api
        self.ws_client = ws_client
        self.scanner = scanner
        self.position_manager = position_manager
        self.fill_manager = fill_manager
        self.executor = executor
        self.orderbooks = orderbooks
        self.fee_rates = fee_rates
        
        self.markets: list[MarketSnapshot] = []
        self.topology: MarketTopology | None = None
        self.token_ids: list[str] = []

    async def initial_discovery(self) -> None:
        """Run initial discovery and setup."""
        self.markets = await self.discovery.discover_markets()
        self.topology = build_topology(self.markets)
        self.token_ids = [t.token_id for m in self.markets for t in m.tokens]
        self.scanner.topology = self.topology
        
        async def init_book(tid: str):
            book = LocalOrderBook(tid, stale_threshold_ms=self.settings.network.stale_feed_threshold_ms)
            self.orderbooks[tid] = book
            snapshot = await self.rest_api.get_orderbook(tid)
            await book.apply_snapshot(snapshot)
            
        async def fetch_fee_rate(tid: str):
            rate = await self.rest_api.get_fee_rate(tid)
            self.fee_rates[tid] = rate if rate is not None else self.settings.trading.polymarket_fee

        init_tasks = [init_book(t) for t in self.token_ids]
        fee_tasks = [fetch_fee_rate(t) for t in self.token_ids]
        
        if init_tasks:
            await asyncio.gather(*init_tasks)
        if fee_tasks:
            await asyncio.gather(*fee_tasks)
            
        for m in self.markets:
            if len(m.tokens) == 2:
                self.position_manager.register_parity_pair(m.tokens[0].token_id, m.tokens[1].token_id)
                
    async def discovery_loop(self) -> None:
        """Background loop to poll for new markets and settle resolved ones."""
        # Track how many consecutive cycles a token has been absent,
        # to avoid premature settlement from transient API failures.
        absence_counter: dict[str, int] = {}
        ABSENCE_THRESHOLD = 2  # require 2+ consecutive absences

        while True:
            await asyncio.sleep(60)
            try:
                new_markets = await self.discovery.discover_markets()
                
                # Guard: don't replace existing topology with empty results (likely API error)
                if not new_markets:
                    logger.warning("discovery_empty_result", keeping_existing=len(self.markets))
                    continue
                    
                new_topology = build_topology(new_markets)
                new_token_ids = [t.token_id for m in new_markets for t in m.tokens]
                
                # Subscribe to newly discovered tokens
                tokens_to_sub = set(new_token_ids) - set(self.token_ids)
                if tokens_to_sub:
                    async def init_new_book(tid: str):
                        book = LocalOrderBook(tid, stale_threshold_ms=self.settings.network.stale_feed_threshold_ms)
                        self.orderbooks[tid] = book
                        snapshot = await self.rest_api.get_orderbook(tid)
                        await book.apply_snapshot(snapshot)
                    
                    async def fetch_new_fee_rate(tid: str):
                        rate = await self.rest_api.get_fee_rate(tid)
                        self.fee_rates[tid] = rate if rate is not None else self.settings.trading.polymarket_fee
                        
                    await asyncio.gather(*(init_new_book(tid) for tid in tokens_to_sub))
                    await asyncio.gather(*(fetch_new_fee_rate(tid) for tid in tokens_to_sub))
                    self.ws_client.subscribe(list(tokens_to_sub))
                    
                # Update references safely
                self.markets = new_markets
                self.topology = new_topology
                self.scanner.topology = new_topology
                self.token_ids = new_token_ids
                
                # Register parity pairs for new markets
                for m in new_markets:
                    if len(m.tokens) == 2:
                        self.position_manager.register_parity_pair(m.tokens[0].token_id, m.tokens[1].token_id)
                
                # Market resolution check — safe copy of keys
                active_token_ids = {t.token_id for m in new_markets for t in m.tokens}
                current_book_tokens = list(self.orderbooks.keys())
                
                # Update absence counters
                for mid in current_book_tokens:
                    if mid not in active_token_ids:
                        absence_counter[mid] = absence_counter.get(mid, 0) + 1
                    else:
                        absence_counter.pop(mid, None)
                
                # Only settle tokens absent for ABSENCE_THRESHOLD consecutive cycles
                resolved_tokens = [
                    mid for mid in current_book_tokens
                    if absence_counter.get(mid, 0) >= ABSENCE_THRESHOLD
                ]
                
                for mid in resolved_tokens:
                    logger.info("market_resolved", market_id=mid[:12])
                    # Cancel inflight orders for resolved market
                    for oid, data in list(self.fill_manager.inflight_orders.items()):
                        if data.get("market") == mid:
                            await self.executor.cancel_order(oid)
                            self.fill_manager.remove_inflight_order(oid)
                    
                    # ── Deterministic parity-aware settlement ──
                    # For binary parity markets: one token resolves to 1.0, the other to 0.0.
                    # We check if the complement is also resolved. If both are resolved,
                    # we settle the first token at 1.0 and the second at 0.0 deterministically
                    # (the actual winner doesn't matter for parity arb PnL: cost = p_yes + p_no,
                    #  payout = 1.0 regardless of which side wins).
                    complement_id = self.position_manager.parity_pairs.get(mid)
                    if complement_id and complement_id in resolved_tokens:
                        # Both legs resolved — settle first alphabetically at 1.0, other at 0.0
                        # For parity arb, total payout = 1.0 regardless of assignment.
                        if mid < complement_id:
                            settle_price = 1.0
                        else:
                            settle_price = 0.0
                    else:
                        # Standalone token or complement not yet resolved
                        # Conservative default: 0.5 (neutral assumption)
                        settle_price = 0.5
                    
                    self.position_manager.settle_market(mid, settle_price=settle_price)
                    if hasattr(self.executor, 'stats') and self.executor.stats:
                        self.executor.stats.record_settlement(mid, settle_price)
                    
                    # Safe deletion (already working with a copy of keys)
                    self.orderbooks.pop(mid, None)
                    absence_counter.pop(mid, None)
                        
            except Exception as e:
                logger.error("discovery_loop_error", error=str(e))

