"""Find anomalies in live trades."""
import json

trades = json.load(open("data/trades/copy_89b5cdaa.json"))

# Find impossible entry prices
print("=== IMPOSSIBLE ENTRY PRICES (>1.0) ===")
for i, t in enumerate(trades):
    ep = t.get("entry_price", 0) or 0
    if ep > 1.0:
        print(f"  #{i} {t.get('slug','?')[:35]} | entry=${ep:.4f} | stake=${t.get('stake',0):.2f} | shares={t.get('shares',0):.4f}")
        fm = t.get("fill_meta", {})
        if fm:
            print(f"     fill_meta: best_ask={fm.get('best_ask')}, vwap={fm.get('vwap')}, slippage={fm.get('slippage')}")
            print(f"     actual_shares={fm.get('actual_shares')}, actual_stake={fm.get('actual_stake')}")

# Check entry source patterns
print()
print("=== ENTRY SOURCE PATTERNS ===")
sources = {}
for t in trades:
    src = t.get("entry_source", "none")[:40]
    sources[src] = sources.get(src, 0) + 1
for src, c in sorted(sources.items(), key=lambda x: -x[1]):
    print(f"  {c:3d}x {src}")

# Stake anomalies
print()
print("=== STAKE ANOMALIES (>$2) ===")
for i, t in enumerate(trades):
    stk = t.get("stake", 0) or 0
    if stk > 2.0:
        print(f"  #{i} stake=${stk:.2f} | {t.get('slug','?')[:30]} | entry=${t.get('entry_price',0):.4f} | mode={t.get('mode','?')}")
        fm = t.get("fill_meta", {})
        if fm:
            print(f"     actual_stake={fm.get('actual_stake')}, actual_shares={fm.get('actual_shares')}")

# Summary of P&L by category
print()
print("=== P&L BREAKDOWN ===")
categories = {"TP_positive": [], "TP_negative": [], "copy-sell": [], "ghost": [], "open": [], "other": []}
for t in trades:
    status = t.get("status", "?")
    reason = t.get("exit_reason", "") or ""
    pnl = t.get("pnl", 0) or 0
    if status == "ghost":
        categories["ghost"].append(t)
    elif status == "open":
        categories["open"].append(t)
    elif "TP" in reason and pnl > 0:
        categories["TP_positive"].append(t)
    elif "TP" in reason and pnl < 0:
        categories["TP_negative"].append(t)
    elif "copy-sell" in reason:
        categories["copy-sell"].append(t)
    else:
        categories["other"].append(t)

for cat, tlist in categories.items():
    total = sum(t.get("pnl", 0) or 0 for t in tlist)
    total_stake = sum(t.get("stake", 0) or 0 for t in tlist)
    print(f"  {cat:15} | {len(tlist):3d} trades | P&L: ${total:+.2f} | Staked: ${total_stake:.2f}")
