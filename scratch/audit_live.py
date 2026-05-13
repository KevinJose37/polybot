"""Audit the live session trades."""
import json

trades = json.load(open("data/trades/copy_89b5cdaa.json"))

print(f"Total trades: {len(trades)}")
statuses = {}
for t in trades:
    s = t.get("status", "?")
    statuses[s] = statuses.get(s, 0) + 1
for s, c in sorted(statuses.items()):
    print(f"  {s}: {c}")

total_pnl = sum(t.get("pnl", 0) or 0 for t in trades if t.get("status") in ("sold", "won", "lost"))
total_stake = sum(t.get("stake", 0) for t in trades)
print(f"\nTotal P&L: ${total_pnl:.2f}")
print(f"Total staked: ${total_stake:.2f}")

print("\n=== ALL TRADES (chronological) ===")
for i, t in enumerate(trades):
    mode = t.get("mode", "?")
    status = t.get("status", "?")
    slug = t.get("slug", "?")[:40]
    side = t.get("side", "?")
    ep = t.get("entry_price", 0) or 0
    xp = t.get("exit_price", 0) or 0
    pnl = t.get("pnl", 0) or 0
    stake = t.get("stake", 0) or 0
    shares = t.get("shares", 0) or 0
    reason = (t.get("exit_reason", "") or "")[:35]
    entry_t = (t.get("entry_time", "") or "")[:19]
    exit_t = (t.get("exit_time", "") or "")[:19]
    fm = t.get("fill_meta") or {}
    delay = t.get("signal_delay_s", fm.get("signal_delay_s", 0))
    spread = fm.get("book_spread", 0) or 0
    slip = fm.get("slippage", 0) or 0
    
    print(f"#{i:2d} {mode:5} {status:6} | {slug:40} | {side:8} | "
          f"entry=${ep:.4f} exit=${xp:.4f} | stk=${stake:.2f} shr={shares:.4f} | "
          f"pnl=${pnl:+.2f} | {reason:35} | {entry_t} | delay={delay}s spr={spread:.3f} slip={slip:+.4f}")

# Categorize issues
print("\n\n=== ISSUE ANALYSIS ===")

ghosts = [t for t in trades if t.get("status") == "ghost"]
print(f"\n--- GHOST trades (order failed on CLOB): {len(ghosts)} ---")
for t in ghosts:
    print(f"  {t.get('slug', '?')[:30]} | stake=${t.get('stake',0):.2f} | Capital was locked until swept")

negative_tps = [t for t in trades if t.get("status") == "sold" and "TP" in (t.get("exit_reason") or "") and (t.get("pnl", 0) or 0) < 0]
print(f"\n--- NEGATIVE TP trades (sold at loss despite TP trigger): {len(negative_tps)} ---")
for t in negative_tps:
    print(f"  {t.get('slug', '?')[:30]} | {t.get('side')} | entry=${t.get('entry_price',0):.4f} exit=${t.get('exit_price',0):.4f} | pnl=${t.get('pnl',0):+.2f} | {t.get('exit_reason','')}")

positive_trades = [t for t in trades if (t.get("pnl", 0) or 0) > 0]
print(f"\n--- WINNING trades: {len(positive_trades)} ---")
for t in positive_trades:
    print(f"  {t.get('slug', '?')[:30]} | {t.get('side')} | entry=${t.get('entry_price',0):.4f} exit=${t.get('exit_price',0):.4f} | pnl=${t.get('pnl',0):+.2f}")

void_trades = [t for t in trades if t.get("status") == "void"]
print(f"\n--- VOID/ZOMBIE trades: {len(void_trades)} ---")
for t in void_trades:
    print(f"  {t.get('slug', '?')[:30]} | stake=${t.get('stake',0):.2f}")

# Check for extreme exit prices
extreme_exits = [t for t in trades if t.get("exit_price") and (t.get("exit_price", 0) > 1.0 or t.get("exit_price", 0) < 0)]
print(f"\n--- IMPOSSIBLE exit prices (>1.0 or <0): {len(extreme_exits)} ---")
for t in extreme_exits:
    print(f"  {t.get('slug', '?')[:30]} | exit=${t.get('exit_price',0):.4f}")

# Sell failures (copy-sell with bad results)
copy_sells = [t for t in trades if t.get("exit_reason") == "copy-sell"]
print(f"\n--- COPY-SELL exits: {len(copy_sells)} ---")
for t in copy_sells:
    print(f"  {t.get('slug', '?')[:30]} | {t.get('side')} | entry=${t.get('entry_price',0):.4f} exit=${t.get('exit_price',0):.4f} | pnl=${t.get('pnl',0):+.2f}")

# Open positions still hanging
open_trades = [t for t in trades if t.get("status") == "open"]
print(f"\n--- STILL OPEN positions: {len(open_trades)} ---")
for t in open_trades:
    print(f"  {t.get('slug', '?')[:30]} | {t.get('side')} | entry=${t.get('entry_price',0):.4f} | stake=${t.get('stake',0):.2f} | {t.get('entry_time','')[:19]}")
