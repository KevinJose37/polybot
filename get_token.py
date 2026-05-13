import urllib.request
import json
import asyncio
import websockets

req = urllib.request.Request('https://clob.polymarket.com/markets', headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req)
data = json.loads(resp.read())
token_id = data['data'][0]['tokens'][0]['token_id']
print("Testing token:", token_id)

async def test():
    async with websockets.connect("wss://ws-subscriptions-clob.polymarket.com/ws/market") as ws:
        await ws.send(json.dumps({
            "assets_ids": [token_id],
            "type": "market"
        }))
        for _ in range(5):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                print(msg[:500])
            except Exception as e:
                print('Timeout or error:', e)
asyncio.run(test())
