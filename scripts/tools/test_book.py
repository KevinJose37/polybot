"""Test orderbook availability for different market types."""
import requests
import json

print("=" * 70)
print("  ORDERBOOK AVAILABILITY TEST")
print("=" * 70)

# 1. Test EB99999's geopolitics market
print("\n  1. EB99999 (geopolitics)")
r = requests.get(
    "https://data-api.polymarket.com/trades?user=0x5d0f03cf1243a3e21262d6cf844795afd9fff0ad&limit=1",
    timeout=10,
)
t = r.json()[0]
token = t.get("asset", "")
title = t.get("title", "?")
print(f"  Title: {title[:50]}")
print(f"  Token: {token[:30]}...")
r2 = requests.get("https://clob.polymarket.com/book", params={"token_id": token}, timeout=5)
print(f"  Book: {r2.status_code} - {r2.text[:80]}")

# 2. Test memain's sports market
print("\n  2. memain (sports)")
r = requests.get(
    "https://data-api.polymarket.com/trades?user=0xdb15fbbcc1a8d1cbe112f7a2d74f6f752f2314f1&limit=1",
    timeout=10,
)
t = r.json()[0]
token = t.get("asset", "")
title = t.get("title", "?")
print(f"  Title: {title[:50]}")
print(f"  Token: {token[:30]}...")
r2 = requests.get("https://clob.polymarket.com/book", params={"token_id": token}, timeout=5)
print(f"  Book: {r2.status_code} - {r2.text[:80]}")

# 3. Test a crypto market (known to work)
print("\n  3. Crypto market (reference)")
r3 = requests.get("https://gamma-api.polymarket.com/markets?limit=1&active=true&tag=crypto", timeout=5)
mkts = r3.json()
if mkts:
    m = mkts[0]
    q = m.get("question", "?")
    cid_raw = m.get("clobTokenIds", "[]")
    if isinstance(cid_raw, str):
        cid = json.loads(cid_raw)
    else:
        cid = cid_raw
    print(f"  Market: {q[:50]}")
    if cid:
        tid = cid[0]
        print(f"  Token: {tid[:30]}...")
        r4 = requests.get("https://clob.polymarket.com/book", params={"token_id": tid}, timeout=5)
        print(f"  Book: {r4.status_code}")
        if r4.status_code == 200:
            d = r4.json()
            bids = d.get("bids", [])
            asks = d.get("asks", [])
            print(f"  Bids: {len(bids)} | Asks: {len(asks)}")
            if bids:
                print(f"  Best bid: ${float(bids[0]['price']):.4f}")
            if asks:
                print(f"  Best ask: ${float(asks[0]['price']):.4f}")

# 4. Conclusion
print("\n" + "=" * 70)
print("  CONCLUSION")
print("=" * 70)
print("  If sports/geo markets return 404, it means the market has been")
print("  resolved or delisted from CLOB. The book check will fail and")
print("  the trade will be SKIPPED (realistic behavior).")
print("")
print("  For ACTIVE markets with live orderbooks, the check will pass")
print("  and use the REAL best_ask as entry price.")
print("=" * 70)
