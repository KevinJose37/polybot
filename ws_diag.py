"""Quick WS diagnostic — see raw message format from Polymarket."""
import asyncio
import json
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Use a known active token_id (BTC UP from current market)
# We'll discover one first via REST
import requests
resp = requests.get("https://gamma-api.polymarket.com/events", params={"slug": "btc-updown-5m-1777834500"}, timeout=8)
events = resp.json()
if events:
    mkt = events[0].get("markets", [{}])[0]
    token_ids_raw = mkt.get("clobTokenIds", "[]")
    if isinstance(token_ids_raw, str):
        token_ids = json.loads(token_ids_raw)
    else:
        token_ids = token_ids_raw
    print(f"Market: {mkt.get('question', '?')}")
    print(f"Token IDs: {token_ids}")
else:
    # Fallback
    token_ids = []
    print("No market found, using empty token list")

async def main():
    async with websockets.connect(WS_URL) as ws:
        sub = {
            "assets_ids": token_ids[:2] if token_ids else [],
            "type": "market",
            "custom_feature_enabled": True,
        }
        print(f"\nSending: {json.dumps(sub)}")
        await ws.send(json.dumps(sub))
        
        count = 0
        async for msg in ws:
            data = json.loads(msg)
            print(f"\n--- Message {count+1} ---")
            print(f"Type: {type(data).__name__}")
            if isinstance(data, list):
                print(f"Length: {len(data)}")
                for i, item in enumerate(data[:3]):
                    print(f"  [{i}] type={type(item).__name__}")
                    if isinstance(item, dict):
                        print(f"      keys={list(item.keys())}")
                        print(f"      event_type={item.get('event_type', 'N/A')}")
                        if item.get("event_type") == "book":
                            print(f"      asset_id={item.get('asset_id', '?')[:20]}...")
                            bids = item.get("bids", [])
                            asks = item.get("asks", [])
                            print(f"      bids={len(bids)} asks={len(asks)}")
                            if bids:
                                print(f"      top_bid={bids[0]}")
                            if asks:
                                print(f"      top_ask={asks[0]}")
                    else:
                        print(f"      value={str(item)[:100]}")
            elif isinstance(data, dict):
                print(f"Keys: {list(data.keys())}")
                print(f"event_type: {data.get('event_type', 'N/A')}")
                print(json.dumps(data, indent=2)[:500])
            
            count += 1
            if count >= 5:
                break

asyncio.run(main())
