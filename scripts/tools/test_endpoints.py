"""Compare /trades vs /activity endpoints for EB99999."""
import requests
import time

addr = "0x5d0f03cf1243a3e21262d6cf844795afd9fff0ad"
now = int(time.time())

print("=== /trades endpoint (CURRENT) ===")
r1 = requests.get(f"https://data-api.polymarket.com/trades?user={addr}&limit=5", timeout=10)
for t in r1.json():
    ts = int(float(t.get("timestamp", 0)))
    age_h = (now - ts) / 3600
    side = t.get("side", "?")
    outcome = t.get("outcome", "?")
    title = t.get("title", "?")[:45]
    tx = t.get("transactionHash", "")[:12]
    print(f"  {age_h:.1f}h ago | {side:4} {outcome:5} | tx={tx} | {title}")

print()
print("=== /activity endpoint (MORE COMPLETE) ===")
r2 = requests.get(f"https://data-api.polymarket.com/activity?user={addr}&limit=5", timeout=10)
acts = r2.json()
for a in acts:
    ts = int(float(a.get("timestamp", 0)))
    age_h = (now - ts) / 3600
    typ = a.get("type", "?")
    size = a.get("size", 0)
    usdc = a.get("usdcSize", 0)
    tx = a.get("transactionHash", "")[:12]
    print(f"  {age_h:.1f}h ago | {typ:6} | {size:.1f} shares | ${usdc:.2f} | tx={tx}")

print()
newest_trades = int(float(r1.json()[0].get("timestamp", 0)))
newest_activity = int(float(acts[0].get("timestamp", 0)))
diff = (newest_activity - newest_trades) / 3600
print(f"TRADES newest: {(now - newest_trades)/3600:.1f}h ago")
print(f"ACTIVITY newest: {(now - newest_activity)/3600:.1f}h ago")
print(f"ACTIVITY is {diff:.1f}h MORE RECENT than TRADES")
print()
if diff > 0:
    print("** /activity has newer data! Must switch to this endpoint **")
