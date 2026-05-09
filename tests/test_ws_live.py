"""Quick test: subscribe to current market tokens and print price_change messages."""
import asyncio
import json
import time
import requests
import websockets

async def test():
    now = int(time.time())
    slot = (now // 300) * 300
    slug = f"btc-updown-5m-{slot}"
    r = requests.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
    data = r.json()
    if not data:
        print("No market found")
        return
    m = data[0]["markets"][0]
    raw_ids = m.get("clobTokenIds", "[]")
    ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
    print(f"Subscribing to {len(ids)} tokens for {slug}")

    async with websockets.connect(
        "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        ping_interval=20,
    ) as ws:
        sub = {"assets_ids": ids, "type": "market", "custom_feature_enabled": True}
        await ws.send(json.dumps(sub))

        count = 0
        start = time.time()
        while time.time() - start < 15:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3)
                d = json.loads(msg)
                if isinstance(d, list):
                    print(f"ACK: {d}")
                    continue
                et = d.get("event_type", "")
                if et == "price_change":
                    changes = d.get("price_changes", d.get("changes", []))
                    for c in changes:
                        aid = c.get("asset_id", "")[:20]
                        bb = c.get("best_bid", "MISSING")
                        ba = c.get("best_ask", "MISSING")
                        side = c.get("side", "?")
                        print(f"  price_change: asset={aid}... side={side} best_bid={bb} best_ask={ba}")
                        count += 1
                elif et:
                    print(f"  event: {et}")
            except asyncio.TimeoutError:
                print("  (no message for 3s)")
        print(f"\nTotal price_change messages in 15s: {count}")

asyncio.run(test())
