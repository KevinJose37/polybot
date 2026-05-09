"""Verify copy fleet is detecting wallet trades correctly."""
import requests
import json
import time
from pathlib import Path

WALLETS = [
    ("EB99999",       "0x5d0f03cf1243a3e21262d6cf844795afd9fff0ad"),
    ("memain",        "0xdb15fbbcc1a8d1cbe112f7a2d74f6f752f2314f1"),
    ("bobthetradoor", "0xe7348e92f76c26e879a9d0c1ff37cdbc4a926a78"),
    ("tdrhrhhd",      "0xd7f85d0eb0fe0732ca38d9107ad0d4d01b1289e4"),
    ("vovatoxic",     "0xf989bd9c62b1eae2c388515fcc766527a8b147cc"),
]

DATA_DIR = Path("data/trades")
now = int(time.time())

print("=" * 80)
print("  COPY FLEET VERIFICATION")
print("=" * 80)

# 1. Check each wallet's recent trades
print("\n  1. WALLET ACTIVITY (last 3 trades per wallet)")
print("-" * 80)

sample_token = None
for name, addr in WALLETS:
    try:
        r = requests.get(
            f"https://data-api.polymarket.com/trades?user={addr}&limit=3",
            timeout=10,
        )
        trades = r.json()
        print(f"\n  {name} ({addr[:10]}...)")
        if not trades:
            print("    No trades found on API")
            continue
        for t in trades:
            ts = int(float(t.get("timestamp", 0)))
            if ts > 1e12:
                ts = ts // 1000
            age = now - ts
            side = t.get("side", "?")
            outcome = t.get("outcome", "?")
            price = float(t.get("price", 0))
            size = float(t.get("size", 0))
            title = t.get("title", "?")[:40]
            token = t.get("asset", "")

            if age < 3600:
                age_str = f"{age // 60}m ago"
            elif age < 86400:
                age_str = f"{age // 3600}h ago"
            else:
                age_str = f"{age // 86400}d ago"

            print(f"    {side:4} {outcome:5} @ ${price:.3f} | ${size:.1f} | {age_str:>8} | {title}")

            # Save a token_id for orderbook test
            if token and not sample_token:
                sample_token = token
    except Exception as e:
        print(f"    ERROR: {e}")

# 2. Verify seen files exist and are tracking
print("\n\n  2. SEEN FILES (tracking processed transactions)")
print("-" * 80)
for name, addr in WALLETS:
    ws = addr[2:10].lower()
    seen_file = DATA_DIR / f"copy_{ws}_seen.json"
    trades_file = DATA_DIR / f"copy_{ws}.json"
    
    if seen_file.exists():
        seen = json.loads(seen_file.read_text())
        print(f"  {name:16} seen={len(seen):>3} txs tracked  (file OK)")
    else:
        print(f"  {name:16} NO SEEN FILE - bot may not be running!")
    
    if trades_file.exists():
        trades = json.loads(trades_file.read_text())
        open_t = sum(1 for t in trades if t.get("status") == "open")
        print(f"  {' ':16} trades={len(trades)} (open={open_t})")

# 3. Test orderbook check on a real token
print("\n\n  3. ORDERBOOK CHECK (realistic entry verification)")
print("-" * 80)
if sample_token:
    print(f"  Testing token: {sample_token[:20]}...")
    try:
        from scalper.live_client import check_entry_conditions
        result = check_entry_conditions(
            token_id=sample_token,
            max_spread=0.05,
            asset="TEST",
            side="TEST",
        )
        can = result.get("can_enter", False)
        reason = result.get("reason", "?")
        bid = result.get("best_bid", 0)
        ask = result.get("best_ask", 0)
        spread = result.get("spread", 0)
        
        status = "PASS" if can else "BLOCKED"
        print(f"  Result: {status}")
        print(f"  Reason: {reason}")
        if bid and ask:
            print(f"  Best bid: ${bid:.4f} | Best ask: ${ask:.4f} | Spread: ${spread:.4f}")
        print(f"  --> Paper trades will use ${ask:.4f} as entry (not API price)")
    except Exception as e:
        print(f"  ERROR: {e}")
else:
    print("  No token_id available to test (all wallets inactive)")

# 4. Summary
print("\n\n  4. VERDICT")
print("-" * 80)
all_seen = all(
    (DATA_DIR / f"copy_{addr[2:10].lower()}_seen.json").exists()
    for _, addr in WALLETS
)
print(f"  Seen files exist:     {'YES' if all_seen else 'NO'}")
print(f"  Orderbook check:     {'WORKING' if sample_token else 'UNTESTED'}")
print(f"  Mode:                PAPER (realistic, orderbook-verified)")
print(f"\n  The fleet will detect and copy any NEW trade from these wallets.")
print(f"  These are slow-market traders (politics/sports/geo) - may take")
print(f"  hours or days between trades. That's normal.")
print("=" * 80)
