"""Audit: compare our copy trades vs ohanism's actual trades."""
import json, sys, os, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# 1. Load OUR trades
our_file = "data/trades/copy_89b5cdaa.json"
our_trades = json.loads(open(our_file, encoding="utf-8").read()) if os.path.exists(our_file) else []

print(f"{'='*80}")
print("COPY AUDIT: Our Trades vs Ohanism's Trades")
print(f"{'='*80}")

# 2. Our stats
open_t = [t for t in our_trades if t.get("status") == "open"]
won_t = [t for t in our_trades if t.get("status") == "won"]
lost_t = [t for t in our_trades if t.get("status") == "lost"]
sold_t = [t for t in our_trades if t.get("status") == "sold"]
resolved = won_t + lost_t + sold_t

print(f"\n--- OUR PERFORMANCE ---")
print(f"Total trades: {len(our_trades)}")
print(f"Open: {len(open_t)} | Won: {len(won_t)} | Lost: {len(lost_t)} | Sold(TP): {len(sold_t)}")

total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved)
total_stake = sum(t.get("stake", 0) or 0 for t in resolved)
wr = len(won_t) / len(resolved) * 100 if resolved else 0
tp_wr = (len(won_t) + len([s for s in sold_t if (s.get("pnl") or 0) > 0])) / len(resolved) * 100 if resolved else 0

print(f"Total P&L: ${total_pnl:+.2f}")
print(f"Total staked (resolved): ${total_stake:.2f}")
print(f"ROI: {total_pnl/total_stake*100:+.1f}%" if total_stake > 0 else "ROI: N/A")
print(f"Win rate (resolutions only): {wr:.0f}%")
print(f"Win rate (incl TP sells): {tp_wr:.0f}%")

print(f"\n--- RESOLVED TRADES DETAIL ---")
for t in resolved:
    status = t.get("status", "?")
    stake = t.get("stake", 0)
    pnl = t.get("pnl", 0) or 0
    ep = t.get("entry_price", 0)
    xp = t.get("exit_price", 0)
    reason = (t.get("exit_reason") or "?")[:30]
    q = (t.get("question") or "")[:40]
    shares = t.get("shares", 0)
    mode = t.get("mode", "?")
    print(f"  {status:4s} stake=${stake:.2f} entry=${ep:.3f} exit=${xp:.3f} sh={shares:.2f} pnl=${pnl:+.2f} | {reason} | {mode} | {q}")

# 3. Ohanism's ACTUAL trades from API
print(f"\n--- OHANISM'S ACTUAL TRADES (last 50) ---")
addr = "0x89b5cdaaa4866c1e738406712012a630b4078beb"
r = requests.get("https://data-api.polymarket.com/activity",
                  params={"user": addr, "limit": 50}, timeout=15)
api_trades = [t for t in r.json() if t.get("type") == "TRADE"]

oh_buys = 0
oh_sells = 0
oh_total_spent = 0
oh_total_received = 0

for t in api_trades:
    action = t.get("action", "?")
    usd = float(t.get("usdcSize", 0))
    price = float(t.get("price", 0))
    q = (t.get("title") or "")[:40]
    outcome = t.get("outcome", "?")
    ts = t.get("timestamp", "")
    
    if action == "BUY":
        oh_buys += 1
        oh_total_spent += usd
    elif action == "SELL":
        oh_sells += 1
        oh_total_received += usd
    
    print(f"  {action:4s} ${usd:6.2f} @ ${price:.3f} {outcome:5s} | {str(ts)[:19]} | {q}")

oh_net = oh_total_received - oh_total_spent
print(f"\nOhanism summary (last 50 trades):")
print(f"  Buys: {oh_buys} (spent ${oh_total_spent:.2f})")
print(f"  Sells: {oh_sells} (received ${oh_total_received:.2f})")
print(f"  Net flow: ${oh_net:+.2f}")

# 4. Key comparison
print(f"\n{'='*80}")
print("KEY DIFFERENCES")
print(f"{'='*80}")
print(f"1. STAKE SIZE: Ohanism trades ${oh_total_spent/oh_buys:.2f}/trade avg vs our $1/trade" if oh_buys else "")
print(f"2. OUR TP: We sell early when price moves in our favor (sold {len(sold_t)} via TP)")
print(f"3. HIS SELLS: He manually sells {oh_sells} times vs our auto-TP")
print(f"4. TIMING: We enter {sum(t.get('signal_delay_s', 0) for t in our_trades)/len(our_trades):.0f}s after him on avg" if our_trades else "")
