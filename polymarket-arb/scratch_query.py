import asyncio
import time
from bot.api.polymarket import PolymarketRESTClient
from bot.utils.clocks import current_timestamp_ms

async def main():
    api = PolymarketRESTClient()
    now_ts = current_timestamp_ms() // 1000
    divisor = 300
    print(f"Current time: {now_ts}")
    for offset in [-300, 0, 300, 600]:
        window_ts = (now_ts + offset) - ((now_ts + offset) % divisor)
        exact_slug = f"btc-updown-5m-{window_ts}"
        markets = await api.get_markets(slug_prefix=exact_slug)
        for m in markets:
            print(f"Slug: {m.slug}, Active: {m.active}, Closed: {m.closed}")
    await api.close()

if __name__ == "__main__":
    asyncio.run(main())
