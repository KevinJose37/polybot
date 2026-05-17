"""Quick WS message debug - log first 20 raw messages."""
import asyncio
import json
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Use a known active token from BTC
# We'll discover it first
from sniper_bot.scanner import scan_markets
from sniper_bot.config import SniperConfig

cfg = SniperConfig()
markets = scan_markets(cfg)
tokens = []
for asset, info in markets.items():
    if info.up_token_id:
        tokens.append(info.up_token_id)
    if info.down_token_id:
        tokens.append(info.down_token_id)
    print(f"{asset}: up={info.up_token_id[:16]}... down={info.down_token_id[:16]}...")

print(f"\nSubscribing to {len(tokens)} tokens")

async def debug_ws():
    async with websockets.connect(WS_URL, ping_interval=10, ping_timeout=10) as ws:
        msg = json.dumps({
            "assets_ids": tokens[:4],  # Just first 2 markets
            "type": "market",
            "custom_feature_enabled": True,
        })
        await ws.send(msg)
        print("Subscribed. Waiting for messages...\n")

        for i in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(raw)
            is_list = isinstance(data, list)
            is_dict = isinstance(data, dict)

            if is_dict:
                et = data.get("event_type", "NO_TYPE")
                aid = data.get("asset_id", "")[:16]
                keys = list(data.keys())
                print(f"[{i}] DICT | event_type={et} | asset_id={aid}...")
                print(f"    keys={keys}")
                if et == "price_change":
                    changes = data.get("price_changes", data.get("changes", []))
                    print(f"    changes_key={'price_changes' if 'price_changes' in data else 'changes' if 'changes' in data else 'NONE'}")
                    print(f"    n_changes={len(changes)}")
                    if changes:
                        c = changes[0]
                        print(f"    first_change: asset={c.get('asset_id','')[:16]}, price={c.get('price')}, side={c.get('side')}, size={c.get('size')}")
                        print(f"    bb={c.get('best_bid')}, ba={c.get('best_ask')}")
                elif et == "book":
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])
                    print(f"    bids={len(bids)} asks={len(asks)}")
                    if bids:
                        print(f"    top_bid: {bids[0]}")
                    if asks:
                        print(f"    top_ask: {asks[0]}")
            elif is_list:
                print(f"[{i}] LIST | len={len(data)}")
                if data:
                    first = data[0]
                    print(f"    first_item type={type(first).__name__}")
                    if isinstance(first, dict):
                        print(f"    first_keys={list(first.keys())}")
            else:
                print(f"[{i}] OTHER type={type(data).__name__}")
            print()

asyncio.run(debug_ws())
