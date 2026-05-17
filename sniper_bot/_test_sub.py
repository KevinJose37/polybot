import asyncio, json, websockets
from datetime import datetime, timezone
import requests

def get_tokens():
    now = datetime.now(timezone.utc)
    slot1 = (int(now.timestamp()) // 300) * 300
    slot2 = slot1 + 300
    
    t1 = requests.get(f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{slot1}").json()[0]['markets'][0]['clobTokenIds']
    t2 = requests.get(f"https://gamma-api.polymarket.com/events?slug=eth-updown-5m-{slot1}").json()[0]['markets'][0]['clobTokenIds']
    return t1, t2

async def test():
    t1, t2 = get_tokens()
    async with websockets.connect('wss://ws-subscriptions-clob.polymarket.com/ws/market') as ws:
        # Sub 1
        await ws.send(json.dumps({'assets_ids': t1, 'type': 'market'}))
        print("Sent Sub 1")
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), 1.0)
                data = json.loads(msg)
                print(f"Sub 1 recv: type={type(data)} events={len(data) if isinstance(data, list) else data.get('event_type')}")
            except asyncio.TimeoutError:
                break
                
        # Sub 2
        print("Sending Sub 2")
        await ws.send(json.dumps({'assets_ids': t1 + t2, 'type': 'market'}))
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), 1.0)
                data = json.loads(msg)
                print(f"Sub 2 recv: type={type(data)} events={len(data) if isinstance(data, list) else data.get('event_type')}")
            except asyncio.TimeoutError:
                break

asyncio.run(test())
