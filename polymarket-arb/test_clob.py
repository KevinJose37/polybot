import asyncio
import time
import json
from bot.api.polymarket import PolymarketRESTClient
from bot.utils.clocks import current_timestamp_ms

async def main():
    api = PolymarketRESTClient()
    now_ts = current_timestamp_ms() // 1000
    divisor = 300
    
    # Check old markets that should be closed
    for offset in [-1200, -900, -600, -300]:
        window_ts = (now_ts + offset) - ((now_ts + offset) % divisor)
        exact_slug = f"btc-updown-5m-{window_ts}"
        markets = await api.get_markets(slug_prefix=exact_slug)
        for m in markets:
            print(f"Slug: {m.slug}, Active: {m.active}, Closed: {m.closed}")
            if m.closed or not m.active:
                session = await api._get_session()
                url = f"{api.clob_api_url}/markets/{m.id}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    print(json.dumps(data, indent=2))
                    return

    await api.close()

if __name__ == "__main__":
    asyncio.run(main())
