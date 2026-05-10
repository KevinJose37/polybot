"""Test VWAP simulation with real orderbook data."""
import sys, requests, json
sys.path.insert(0, '.')
from copy_wallet import _simulate_fill, _get_live_mid

# Fetch EB99999's latest trade token
act = requests.get(
    'https://data-api.polymarket.com/activity?user=0x5d0f03cf1243a3e21262d6cf844795afd9fff0ad&limit=1',
    timeout=10,
).json()[0]
token = act.get('asset', '')
title = act.get('title', '?')

# Fetch real book
book = requests.get(f'https://clob.polymarket.com/book?token_id={token}', timeout=3).json()
asks = book.get('asks', [])
bids = book.get('bids', [])
sorted_asks = sorted(asks, key=lambda a: float(a.get('price', 0)))
sorted_bids = sorted(bids, key=lambda b: float(b.get('price', 0)), reverse=True)

best_ask = float(sorted_asks[0]['price'])
best_bid = float(sorted_bids[0]['price'])

total_depth = sum(float(a['price']) * float(a.get('size', 0)) for a in sorted_asks[:10])

print(f"Market: {title}")
print(f"Best bid: ${best_bid:.4f} | Best ask: ${best_ask:.4f} | Spread: ${best_ask-best_bid:.4f}")
print(f"Ask levels: {len(sorted_asks)} | Bid levels: {len(sorted_bids)}")
print(f"Total ask depth (10 lvl): ${total_depth:.0f}")
print()

# Test VWAP fill at different order sizes
print("VWAP FILL SIMULATION:")
print(f"  {'Order':>8}  {'VWAP':>8}  {'Slippage':>10}  {'Levels':>6}  {'Filled':>8}  {'Full':>5}")
print("-" * 55)
for order_usd in [2, 4, 10, 50, 200]:
    sim = _simulate_fill(asks, order_usd)
    slip = sim['vwap'] - best_ask if sim['vwap'] > 0 else 0
    print(
        f"  ${order_usd:>6}  ${sim['vwap']:.4f}  {slip:>+10.4f}  "
        f"{sim['levels_consumed']:>6}  ${sim['filled_usd']:>6.1f}  "
        f"{'YES' if sim['fully_filled'] else 'NO':>5}"
    )

# Test live mid
mid = _get_live_mid(token)
print(f"\nLive mid: ${mid:.4f}" if mid else "\nLive mid: UNAVAILABLE")

# Test TP/SL thresholds
entry_px = best_ask
tp_pct, sl_pct = 0.50, 0.25
upside = 1.0 - entry_px
tp_target = entry_px + upside * tp_pct
sl_target = entry_px * (1.0 - sl_pct)
print(f"\nTP/SL example (entry=${entry_px:.3f}, TP={tp_pct*100:.0f}%, SL={sl_pct*100:.0f}%):")
print(f"  TP target: ${tp_target:.3f} (need +${tp_target-entry_px:.3f})")
print(f"  SL target: ${sl_target:.3f} (allow -${entry_px-sl_target:.3f})")
if mid:
    status = "TP HIT" if mid >= tp_target else "SL HIT" if mid <= sl_target else "HOLD"
    print(f"  Current mid: ${mid:.4f} -> {status}")

# Compare old vs new
print(f"\n--- REALISM COMPARISON (for $4 order) ---")
sim4 = _simulate_fill(asks, 4)
old_entry = best_ask
new_entry = sim4['vwap']
diff = new_entry - old_entry
old_shares = 4 / old_entry if old_entry > 0 else 0
new_shares = sim4['filled_shares']
print(f"  OLD (best_ask): entry=${old_entry:.4f}, shares={old_shares:.2f}")
print(f"  NEW (VWAP):     entry=${new_entry:.4f}, shares={new_shares:.2f}")
print(f"  Slippage:       {diff:+.4f} ({diff/old_entry*100:+.2f}%)")
print(f"  P&L impact on $4: ${(old_shares - new_shares) * 1.0:.3f} fewer shares if wins")
