"""
Market discovery polling.
"""
import structlog
from bot.api.polymarket import PolymarketAdapter
from bot.utils.clocks import current_timestamp_ms
from bot.api.schemas import MarketSnapshot
from bot.market_discovery.parsers import parse_market_slug
from bot.constants import TARGET_ASSETS, TARGET_WINDOWS

logger = structlog.get_logger(__name__)


class MarketDiscoveryService:
    def __init__(self, api: PolymarketAdapter):
        self.api = api
        self._known_ids: set[str] = set()

    async def discover_markets(self) -> list[MarketSnapshot]:
        """
        Polls market universe and filters strictly for target assets and windows.
        We calculate the exact window timestamp to query the specific slug.
        
        Tracks known markets across calls to suppress duplicate log spam.
        """
        discovered = []
        seen_ids: set[str] = set()
        now_ts = current_timestamp_ms() // 1000
        
        for asset in TARGET_ASSETS:
            for window in TARGET_WINDOWS:
                divisor = 5 * 60 if window == "5m" else 15 * 60
                
                # Check previous, current, next, and next-next windows to handle clock drift
                for offset in [-divisor, 0, divisor, 2 * divisor]:
                    window_ts = (now_ts + offset) - ((now_ts + offset) % divisor)
                    exact_slug = f"{asset.lower()}-updown-{window}-{window_ts}"
                    
                    markets = await self.api.get_markets(slug_prefix=exact_slug)
                
                    # Filter strictly
                    for market in markets:
                        if not market.active or market.closed:
                            continue
                        if market.id in seen_ids:
                            continue
                            
                        parsed = parse_market_slug(market.slug)
                        if parsed.is_valid and parsed.asset == asset.upper() and parsed.timeframe == window:
                            seen_ids.add(market.id)
                            discovered.append(market)

                            # Only log genuinely new markets (not re-discoveries)
                            if market.id not in self._known_ids:
                                self._known_ids.add(market.id)
                                logger.info(
                                    "market_discovered", 
                                    market_id=market.id, 
                                    slug=market.slug, 
                                    asset=parsed.asset, 
                                    window=parsed.timeframe
                                )
        
        logger.debug(
            "discovery_sweep_complete",
            total=len(discovered),
            new=len(seen_ids - (self._known_ids - seen_ids)),
        )
                        
        return discovered
