"""Lifecycle Manager for continuous market discovery."""
import asyncio
import structlog
from market_discovery.discovery import MarketDiscoveryService
from core.bot import AdaptiveMarketMakerBot
from config.settings import Config

logger = structlog.get_logger(__name__)

class LifecycleManager:
    def __init__(self, settings: Config, bot: AdaptiveMarketMakerBot, discovery: MarketDiscoveryService):
        self.settings = settings
        self.bot = bot
        self.discovery = discovery
        
        # Token ID to human-readable name mapping
        # e.g., "0x123..." -> "BTC 5m (Yes)"
        self.token_to_name: dict[str, str] = {}
        
    async def initial_discovery(self):
        """Run a single discovery sweep."""
        await self._sweep()
        
    async def discovery_loop(self):
        """Continuously sweep for new markets every 30 seconds."""
        while True:
            await asyncio.sleep(30)
            try:
                await self._sweep()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("lifecycle_discovery_error", error=str(e))
                
    async def _sweep(self):
        snapshots = await self.discovery.discover_markets()
        new_token_ids = []
        
        for snapshot in snapshots:
            for token in snapshot.tokens:
                # E.g. "BTC updown 5m" or similar from the slug
                # We want to format the name nicely
                from market_discovery.parsers import parse_market_slug
                parsed = parse_market_slug(snapshot.slug)
                if parsed.is_valid:
                    name = f"{parsed.asset} {parsed.timeframe} ({token.outcome})"
                    self.token_to_name[token.token_id] = name
                    self.bot.market_to_asset[token.token_id] = parsed.asset
                    new_token_ids.append(token.token_id)
        
        # Determine if we have genuinely new markets
        current_markets = set(self.settings.markets)
        discovered_markets = set(new_token_ids)
        
        if discovered_markets != current_markets:
            logger.info("lifecycle_updating_markets", current=len(current_markets), discovered=len(discovered_markets))
            self.settings.markets = list(discovered_markets)
            
            # Instruct bot WebSocket to subscribe to the new complete list
            if hasattr(self.bot.pm_ws, "subscribe"):
                self.bot.pm_ws.subscribe(self.settings.markets)
