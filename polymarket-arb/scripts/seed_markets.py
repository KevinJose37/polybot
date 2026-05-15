import asyncio
import json
import structlog
from redis.asyncio import Redis

from bot.api.polymarket import PolymarketRESTClient
from bot.market_discovery.discovery import MarketDiscoveryService
from bot.market_discovery.market_relationships import build_topology
from bot.settings import Settings

logger = structlog.get_logger()

async def main() -> None:
    settings = Settings.load()
    api = PolymarketRESTClient()
    discovery = MarketDiscoveryService(api)
    
    logger.info("Starting market discovery...")
    markets = await discovery.discover_markets()
    
    if not markets:
        logger.warning("No target markets found.")
        await api.close()
        return
        
    topology = build_topology(markets)
    
    logger.info(f"Discovered {len(markets)} active target markets.")
    for market in markets:
        print(f" - {market.slug} (ID: {market.id})")
        
    logger.info("Topology pairs:")
    logger.info(f" - Parity targets: {len(topology.parity_markets)}")
    logger.info(f" - Monotonicity sets: {len(topology.monotonicity_pairs)}")
    
    # Store to Redis
    redis_client = Redis.from_url(settings.redis_url)
    
    try:
        await redis_client.ping()
        logger.info("Connected to Redis, storing markets...")
        
        # We can store them in a hash
        for market in markets:
            market_data = market.model_dump(by_alias=True)
            await redis_client.hset(
                "polymarket_arb:active_markets",
                market.id,
                json.dumps(market_data)
            )
            
        logger.info("Successfully stored markets to Redis.")
    except Exception as e:
        logger.error(f"Failed to connect or write to Redis: {e}")
    finally:
        await redis_client.aclose()
        await api.close()

if __name__ == "__main__":
    asyncio.run(main())
