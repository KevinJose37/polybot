"""Test buy: $1 on France to win FIFA World Cup 2026."""
import os, sys, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv()
from scalper.live_client import init_live_client, get_balance, buy_outcome

ok = init_live_client(dry_run=False)
bal = get_balance()
print(f"Init: {ok} | Balance: ${bal}")

# Find France token from FIFA World Cup market
slug = "2026-fifa-world-cup-winner-595"
r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=10)
events = r.json()

if not events:
    # Try searching
    r = requests.get("https://gamma-api.polymarket.com/events?active=true&closed=false&limit=50", timeout=10)
    events = r.json()
    events = [e for e in events if "fifa" in e.get("title", "").lower() or "world cup" in e.get("title", "").lower()]

token_id = None
best_ask = None

for ev in events:
    print(f"\nEvent: {ev.get('title', '?')[:60]}")
    for m in ev.get("markets", []):
        q = m.get("question", "")
        if "france" in q.lower() or "francia" in q.lower():
            tids = m.get("clobTokenIds", "")
            outcomes = m.get("outcomes", "")
            print(f"  Market: {q}")
            print(f"  Outcomes: {outcomes}")
            print(f"  Tokens: {tids[:60]}...")
            
            if tids:
                # Parse tokens - could be JSON array or comma-separated
                import json as _json
                try:
                    tokens = _json.loads(tids) if tids.startswith("[") else [t.strip() for t in tids.split(",")]
                except:
                    tokens = [t.strip() for t in tids.split(",")]
                yes_token = tokens[0]
                
                # Get price
                try:
                    b = requests.get("https://clob.polymarket.com/book", 
                                     params={"token_id": yes_token}, timeout=5)
                    if b.status_code == 200:
                        data = b.json()
                        asks = data.get("asks", [])
                        if asks:
                            best_ask = float(asks[0]["price"])
                            token_id = yes_token
                            print(f"  Best ask (Yes): ${best_ask:.4f}")
                except Exception as e:
                    print(f"  Book error: {e}")

if not token_id:
    print("\nFrance market not found. Listing all markets in event...")
    for ev in events:
        for m in ev.get("markets", []):
            print(f"  {m.get('question', '?')[:60]}")
    sys.exit(1)

print(f"\n{'='*50}")
print(f"BUYING $1 on France (Yes)")
print(f"Token: {token_id[:30]}...")
print(f"Price: ${best_ask:.4f}")
print(f"{'='*50}")

result = buy_outcome(
    token_id=token_id,
    price=best_ask,
    size=1.0,
    asset="fifa-wc",
    side="France Yes",
)

print(f"\nResult: {result}")
bal_after = get_balance()
print(f"Balance after: ${bal_after}")
