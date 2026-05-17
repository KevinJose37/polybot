import asyncio, json, websockets
from sniper_bot.scanner import _slug_for_asset, _fetch_event_by_slug, _extract_market
from datetime import datetime, timezone

async def test():
    now = datetime.now(timezone.utc)
    slot = (int(now.timestamp()) // 300) * 300
    ev = _fetch_event_by_slug(_slug_for_asset('BTC', slot, '5m'), 'https://gamma-api.polymarket.com')
    info = _extract_market(ev, 'BTC')
    tokens = [info.up_token_id]
    
    async with websockets.connect('wss://ws-subscriptions-clob.polymarket.com/ws/market', ping_interval=10) as ws:
        await ws.send(json.dumps({'assets_ids': tokens, 'type': 'market'}))
        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
        data = json.loads(msg)
        if isinstance(data, list):
            for item in data:
                aid = item.get("asset_id")
                mkt = item.get("market")
                print(f"asset_id: {aid} | market: {mkt}")
asyncio.run(test())
