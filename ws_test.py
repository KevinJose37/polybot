import asyncio
import websockets
import json

async def test():
    uri = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    async with websockets.connect(uri) as ws:
        req = {"assets_ids": ["16678291189211314787145083999015737376658799626183230671758641503291735614088"], "type": "market"}
        await ws.send(json.dumps(req))
        print("Sent subscribe")
        for _ in range(5):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(msg)
                print(f"RCV: {str(data)[:200]}")
            except asyncio.TimeoutError:
                print("Timeout waiting for msg")
                break

asyncio.run(test())
