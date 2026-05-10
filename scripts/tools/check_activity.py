"""Check recent activity of candidate wallets for copy fleet."""
import requests
import time
from datetime import datetime, timezone

CANDIDATES = [
    # Gravia top scorers
    ("h00ch",        "0xae7f00473f325d2eda0813cee59006d48951d4fe"),
    ("8934394839",   "0x88c4919de76e60050f2f5635db5b4c4c060ddddf"),  # placeholder - need real addr
    ("nojnn",        "0x7f9e2d1df786fcaeaa16fb19e28da0a2a11d53bf"),  # placeholder
    ("kobitagnkyfv", "0xe0e899bfbc61ad63c59bb1e05213c3a3a7b53713"),  # placeholder
    ("StoneMarble",  "0xbef5ab169458f87c92e8e64a80edc4db43f5c59e"),  # placeholder
    ("hhhhhcgg",     "0xf6891d5f12873776e4dc7c38fe586219a09b9d83"),
    ("bin8888",      "0xa80e3fe5e7a4c5a8052c7ed14e71dfa0ecec6502"),  # placeholder
    ("65765757",     "0x2974bd0059e4ce0b5ede5d1c56b2de5e7c7afc3e"),  # placeholder
    # User's manual additions
    ("manual_1",     "0xb373fc427f2f8b06ba168663016f55b98f512aa2"),
    ("manual_2",     "0x55e2436d747835c7e40b0c6cf92f632bf1215fc9"),
    ("manual_3",     "0x89b5cdaaa4866c1e738406712012a630b4078beb"),
    ("manual_4",     "0x101888282092fb5be3764b1c615200b2f14a23fe"),
    # Already in fleet (for comparison)
    ("EB99999",      "0x5d0f03cf1243a3e21262d6cf844795afd9fff0ad"),
    ("memain",       "0xdb15fbbcc1a8d1cbe112f7a2d74f6f752f2314f1"),
    ("bobthetradoor","0xe7348e92f76c26e879a9d0c1ff37cdbc4a926a78"),
    ("tdrhrhhd",     "0xd7f85d0eb0fe0732ca38d9107ad0d4d01b1289e4"),
    ("vovatoxic",    "0xf989bd9c62b1eae2c388515fcc766527a8b147cc"),
    ("crypto",       "0x5490687ee61406afbb1fd887937fdbb7fe1cb051"),
    ("bobe2",        "0xed107a85a4585a381e48c7f7ca4144909e7dd2e5"),
    ("ohanism",      "0x89b5cdaaa4866c1e738406712012a630b4078beb"),
]

# Also get real addresses from gravia_scored.json
import json
from pathlib import Path
scored_file = Path("data/gravia_scored.json")
if scored_file.exists():
    scored = json.loads(scored_file.read_text())
    name_to_addr = {w["name"]: w["address"] for w in scored}
    # Update placeholder addresses
    for i, (name, addr) in enumerate(CANDIDATES):
        if name in name_to_addr:
            CANDIDATES[i] = (name, name_to_addr[name])

# De-duplicate by address
seen_addrs = set()
unique = []
for name, addr in CANDIDATES:
    addr_lower = addr.lower()
    if addr_lower not in seen_addrs:
        seen_addrs.add(addr_lower)
        unique.append((name, addr))

now = int(time.time())

print("=" * 95)
print("  WALLET ACTIVITY CHECK — All Candidates")
print("=" * 95)
print(f"\n  {'NAME':<16} {'ADDRESS':<14} {'LAST':>8} {'TRADES':>6} {'SIDE':>5} {'LATEST MARKET':<35}")
print("-" * 95)

results = []

for name, addr in unique:
    try:
        r = requests.get(
            f"https://data-api.polymarket.com/activity?user={addr}&limit=3",
            timeout=10,
        )
        acts = r.json()
        trades_only = [a for a in acts if a.get("type") == "TRADE"]

        if not trades_only:
            age_str = "NO DATA"
            latest = "No trades found"
            results.append((name, addr, 999999, "DEAD", latest))
        else:
            t = trades_only[0]
            ts = int(float(t.get("timestamp", 0)))
            age_h = (now - ts) / 3600
            side = t.get("side", "?")
            title = t.get("title", "?")[:35]
            real_name = t.get("name", name)

            if age_h < 1:
                age_str = f"{age_h*60:.0f}m"
            elif age_h < 24:
                age_str = f"{age_h:.0f}h"
            else:
                age_str = f"{age_h/24:.0f}d"

            results.append((real_name or name, addr, age_h, side, title))
            print(f"  {(real_name or name):<16} {addr[:14]} {age_str:>8} {len(trades_only):>6} {side:>5} {title}")

        time.sleep(0.3)
    except Exception as e:
        results.append((name, addr, 999999, "ERR", str(e)[:35]))
        print(f"  {name:<16} {addr[:14]} {'ERROR':>8} {'?':>6} {'?':>5} {str(e)[:35]}")

# Sort by recency and show summary
print("\n\n  ACTIVITY SUMMARY (sorted by most recent)")
print("-" * 95)
results.sort(key=lambda x: x[2])

active_24h = []
active_7d = []
inactive = []

for name, addr, age_h, side, title in results:
    if age_h < 24:
        status = "ACTIVE (24h)"
        active_24h.append((name, addr, age_h))
    elif age_h < 168:
        status = "RECENT (7d)"
        active_7d.append((name, addr, age_h))
    else:
        status = "INACTIVE"
        inactive.append((name, addr, age_h))

    if age_h < 1:
        age_str = f"{age_h*60:.0f}m ago"
    elif age_h < 24:
        age_str = f"{age_h:.0f}h ago"
    elif age_h < 99999:
        age_str = f"{age_h/24:.0f}d ago"
    else:
        age_str = "NO DATA"
    
    print(f"  {status:<16} {name:<16} {age_str:>10} {addr}")

print(f"\n  Active <24h: {len(active_24h)} | Recent <7d: {len(active_7d)} | Inactive: {len(inactive)}")
