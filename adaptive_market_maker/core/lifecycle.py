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
                await self._check_settlements()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("lifecycle_discovery_error", error=str(e))
                
    async def _check_settlements(self):
        """Poll Gamma API for expired markets with paper inventory to simulate realistic settlement."""
        if not hasattr(self.bot.api_client, "synthetic_inventory") or not hasattr(self.bot.api_client, "get_market_resolution"):
            return
            
        current_tokens = set(self.settings.active_token_ids)
        
        for token_id in list(self.bot.api_client.synthetic_inventory.keys()):
            if token_id not in current_tokens:
                shares = self.bot.api_client.synthetic_inventory[token_id]
                if abs(shares) > 1e-6:
                    try:
                        payout = await self.bot.api_client.get_market_resolution(token_id)
                        if payout is not None and hasattr(self.bot.api_client, "settle_market"):
                            self.bot.api_client.settle_market(token_id, payout)
                            logger.info("lifecycle_settled_market", market_id=token_id, payout=payout)
                    except Exception as e:
                        logger.error("lifecycle_settlement_error", market_id=token_id, error=str(e))

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
                    if token.outcome.upper() == "YES":
                        self.bot.yes_tokens.add(token.token_id)
        
        # [H-1] Use active_token_ids for runtime token list — do NOT overwrite
        # settings.markets which is validated for ASSET-WINDOW format.
        current_tokens = set(self.settings.active_token_ids)
        discovered_tokens = set(new_token_ids)
        
        if discovered_tokens != current_tokens:
            logger.info("lifecycle_updating_markets", current=len(current_tokens), discovered=len(discovered_tokens))
            
            # [H-4] Cleanup expired/removed tokens from bot state
            removed_tokens = current_tokens - discovered_tokens
            for token_id in removed_tokens:
                await self.bot.remove_market(token_id)

            self.settings.active_token_ids = list(discovered_tokens)
            
            # Instruct bot WebSocket to subscribe to the new complete list
            if hasattr(self.bot.pm_ws, "subscribe"):
                self.bot.pm_ws.subscribe(self.settings.active_token_ids)
