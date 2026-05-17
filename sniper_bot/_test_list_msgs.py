import asyncio, json, websockets
from sniper_bot.scanner import _slug_for_asset, _fetch_event_by_slug, _extract_market
from datetime import datetime, timezone

async def test():
    now = datetime.now(timezone.utc)
    slot = (int(now.timestamp()) // 300) * 300
    tokens = []
    for asset in ['XRP', 'SOL']:
        ev = _fetch_event_by_slug(_slug_for_asset(asset, slot, '5m'), 'https://gamma-api.polymarket.com')
        if ev:
            info = _extract_market(ev, asset)
            tokens.extend([info.up_token_id, info.down_token_id])
    
    async with websockets.connect('wss://ws-subscriptions-clob.polymarket.com/ws/market', ping_interval=10, ping_timeout=10) as ws:
        await ws.send(json.dumps({'assets_ids': tokens, 'type': 'market', 'custom_feature_enabled': True}))
        try:
            for _ in range(5):
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                if isinstance(data, list):
                    print(f"List length {len(data)}")
                    for i, item in enumerate(data):
                        print(f"  Item {i}: type={type(item).__name__} keys={list(item.keys()) if isinstance(item, dict) else []}")
                        if isinstance(item, dict):
                            print(f"    event_type={item.get('event_type')} asset_id={str(item.get('asset_id', ''))[:8]}")
        except asyncio.TimeoutError:
            pass

asyncio.run(test())
