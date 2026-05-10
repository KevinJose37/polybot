"""
Simulation: What if we copied ALL ohanism trades at $1 and HELD to resolution?
vs what we actually did (with TP auto-sells).
"""
import json, os, sys, requests, time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

ADDR = "0x89b5cdaaa4866c1e738406712012a630b4078beb"
STAKE = 1.0

# ─── 1. Fetch ohanism trades (last 500) ───
print("Fetching ohanism's trade history...")
all_api = []
offset = 0
while offset < 500:
    r = requests.get("https://data-api.polymarket.com/activity",
                      params={"user": ADDR, "limit": 100, "offset": offset}, timeout=20)
    batch = r.json()
    if not isinstance(batch, list) or not batch:
        break
    all_api.extend(batch)
    offset += len(batch)
    if len(batch) < 100:
        break
    time.sleep(0.3)

trades_api = [t for t in all_api if isinstance(t, dict) and t.get("type") == "TRADE"]
buys_api = [t for t in trades_api if t.get("side") == "BUY"]
sells_api = [t for t in trades_api if t.get("side") == "SELL"]

# Only trades from today (our session)
now_ts = int(time.time())
cutoff = now_ts - 6 * 3600  # last 6 hours
recent_buys = [t for t in buys_api if t.get("timestamp", 0) > cutoff]
recent_sells = [t for t in sells_api if t.get("timestamp", 0) > cutoff]

print(f"Total API records: {len(all_api)}")
print(f"All buys: {len(buys_api)}, All sells: {len(sells_api)}")
print(f"Recent buys (6h): {len(recent_buys)}, Recent sells (6h): {len(recent_sells)}")

# ─── 2. Group by market slug ───
market_buys = defaultdict(list)
for t in recent_buys:
    slug = t.get("slug", "") or t.get("eventSlug", "")
    if slug:
        market_buys[slug].append(t)

print(f"Unique markets entered (6h): {len(market_buys)}")

# ─── 3. Load OUR trades ───
our_file = "data/trades/copy_89b5cdaa.json"
our_trades = json.loads(open(our_file, encoding="utf-8").read()) if os.path.exists(our_file) else []
our_slugs = set(t.get("slug", "") for t in our_trades)
our_resolved = [t for t in our_trades if t.get("status") in ("won", "lost", "sold")]

# ─── 4. Simulate: enter every market at ohanism's first entry price, hold to resolution ───
print(f"\n{'='*80}")
print("SIMULATION: $1/trade, HOLD to resolution (like ohanism)")
print(f"{'='*80}\n")

sim_wins = 0
sim_losses = 0
sim_pending = 0
sim_pnl = 0.0
sim_total = 0

for slug, entries in sorted(market_buys.items(), key=lambda x: min(e.get("timestamp", 0) for e in x[1])):
    first = entries[0]
    side = first.get("outcome", "")
    price = float(first.get("price", 0))
    title = (first.get("title") or slug)[:55]
    oh_total_usd = sum(float(e.get("usdcSize", 0)) for e in entries)
    oh_num_entries = len(entries)
    
    if price <= 0 or price >= 1.0:
        continue
    
    we_copied = slug in our_slugs
    
    # Check resolution via gamma markets API
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/events/slug/{slug}", timeout=8)
        if r.status_code == 200:
            data = r.json()
            markets = data.get("markets", []) if isinstance(data, dict) else []
            if not markets and isinstance(data, list):
                markets = data
            
            resolved = False
            won = False
            
            for m in markets:
                if not isinstance(m, dict):
                    continue
                op = m.get("outcomePrices", "")
                if not op:
                    continue
                prices = json.loads(op) if isinstance(op, str) else op
                if len(prices) < 2:
                    continue
                
                up_p = float(prices[0])
                down_p = float(prices[1])
                
                is_resolved = m.get("resolved", False) or m.get("closed", False)
                if not is_resolved and (up_p > 0.95 or down_p > 0.95):
                    is_resolved = True  # effectively resolved
                
                if is_resolved:
                    resolved = True
                    if side in ("Down", "No"):
                        won = down_p > 0.9
                    else:
                        won = up_p > 0.9
                    break
            
            if resolved:
                sim_total += 1
                shares = STAKE / price
                if won:
                    pnl = (1.0 - price) * shares
                    sim_wins += 1
                else:
                    pnl = -STAKE
                    sim_losses += 1
                sim_pnl += pnl
                
                status = "WIN " if won else "LOSS"
                mark = "✅" if we_copied else "❌ MISS"
                print(f"  {status} pnl=${pnl:+.2f} entry=${price:.3f} {side:5s} oh=${oh_total_usd:6.1f}({oh_num_entries}x) | {mark} | {title}")
            else:
                sim_pending += 1
                mark = "✅" if we_copied else "❌ MISS"
                print(f"  PEND entry=${price:.3f} {side:5s} oh=${oh_total_usd:6.1f}({oh_num_entries}x) | {mark} | {title}")
        else:
            sim_pending += 1
    except Exception as e:
        sim_pending += 1
    
    time.sleep(0.15)

# ─── 5. Summary ───
our_actual_pnl = sum(t.get("pnl", 0) or 0 for t in our_resolved)
our_wins = len([t for t in our_resolved if (t.get("pnl") or 0) > 0])
our_losses = len([t for t in our_resolved if (t.get("pnl") or 0) <= 0])
our_wr = our_wins / len(our_resolved) * 100 if our_resolved else 0
sim_wr = sim_wins / sim_total * 100 if sim_total else 0

print(f"\n{'='*80}")
print("FINAL COMPARISON")
print(f"{'='*80}")
print(f"{'':>35} {'US (with TP)':>15} {'SIM (hold)':>15}")
print(f"{'-'*65}")
print(f"{'Resolved trades':>35} {len(our_resolved):>15} {sim_total:>15}")
print(f"{'Wins':>35} {our_wins:>15} {sim_wins:>15}")
print(f"{'Losses':>35} {our_losses:>15} {sim_losses:>15}")
print(f"{'Win Rate':>35} {our_wr:>14.0f}% {sim_wr:>14.0f}%")
print(f"{'P&L ($1/trade)':>35} ${our_actual_pnl:>+13.2f} ${sim_pnl:>+13.2f}")
print(f"{'Avg P&L per trade':>35} ${our_actual_pnl/len(our_resolved) if our_resolved else 0:>+13.2f} ${sim_pnl/sim_total if sim_total else 0:>+13.2f}")
print(f"{'Pending/Open':>35} {len([t for t in our_trades if t.get('status')=='open']):>15} {sim_pending:>15}")
print(f"{'Markets entered':>35} {len(our_slugs):>15} {len(market_buys):>15}")
print(f"{'Markets MISSED':>35} {len(market_buys) - len(our_slugs & set(market_buys.keys())):>15} {'N/A':>15}")

oh_total_spent = sum(float(t.get("usdcSize", 0)) for t in recent_buys)
oh_total_recv = sum(float(t.get("usdcSize", 0)) for t in recent_sells)
print(f"\nOhanism's actual capital flow (6h):")
print(f"  Bought: ${oh_total_spent:.2f} across {len(recent_buys)} orders")
print(f"  Sold:   ${oh_total_recv:.2f} across {len(recent_sells)} orders")
print(f"  Avg entry size: ${oh_total_spent/len(market_buys):.2f}/market" if market_buys else "")
