import json
trades = json.load(open("hft_trades_v2.json"))
r = [t for t in trades if t.get("status") in ("won", "lost")]
w = [t for t in r if t.get("pnl", 0) > 0]
l = [t for t in r if t.get("pnl", 0) <= 0]
pnl = sum(t.get("pnl", 0) for t in r)
stk = sum(t.get("stake", 0) for t in r)
wr = len(w) / len(r) * 100 if r else 0
roi = pnl / stk * 100 if stk > 0 else 0
print(f"V2: {len(trades)} trades | {len(r)} resolved | {len(w)}W/{len(l)}L | WR={wr:.1f}% | PnL=${pnl:+.2f} | ROI={roi:+.1f}%")
for s in ("UP", "DOWN"):
    sw = sum(1 for t in r if t["side"] == s and t["pnl"] > 0)
    sl = sum(1 for t in r if t["side"] == s and t["pnl"] <= 0)
    sp = sum(t["pnl"] for t in r if t["side"] == s)
    print(f"  {s}: {sw}W/{sl}L | PnL ${sp:+.2f}")
ep = [t["entry_price"] for t in r]
print(f"  Entry: avg=${sum(ep)/len(ep):.4f} min=${min(ep):.4f} max=${max(ep):.4f}")
