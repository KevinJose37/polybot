
import asyncio
import json
import websockets

async def test():
    async with websockets.connect('wss://ws-subscriptions-clob.polymarket.com/ws/market', ping_interval=20, ping_timeout=10) as ws:
        # Subscribe to BTC up token (just grabbed from market_scanner)
        sub_msg = {'assets_ids': ['16678291189211314787145083999015737376658799626183230671758641503291735614088'], 'type': 'market', 'custom_feature_enabled': True}
        await ws.send(json.dumps(sub_msg))
        
        seen_book = False
        seen_change = False
        
        while not (seen_book and seen_change):
            msg = await ws.recv()
            data = json.loads(msg)
            if isinstance(data, list): continue
            
            t = data.get('event_type')
            if t == 'book' and not seen_book:
                print('BOOK:', json.dumps(data, indent=2)[:500])
                seen_book = True
            if t == 'price_change' and not seen_change:
                print('CHANGE:', json.dumps(data, indent=2)[:500])
                seen_change = True

asyncio.run(test())
