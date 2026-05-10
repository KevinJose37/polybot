"""Revert all SL-exited trades back to open status."""
import json
from pathlib import Path

DATA_DIR = Path("data/trades")

total_reverted = 0
total_pnl_recovered = 0

for f in sorted(DATA_DIR.glob("copy_*.json")):
    if "_seen" in f.name:
        continue

    trades = json.loads(f.read_text())
    changed = False
    reverted = 0

    for t in trades:
        exit_reason = t.get("exit_reason") or ""
        if exit_reason.startswith("SL"):
            old_pnl = t.get("pnl", 0) or 0
            total_pnl_recovered -= old_pnl  # Recovering the loss

            t["status"] = "open"
            t["exit_price"] = None
            t["exit_time"] = None
            t["exit_reason"] = None
            t["pnl"] = None
            reverted += 1
            changed = True

    if changed:
        f.write_text(json.dumps(trades, indent=2), encoding="utf-8")
        wallet = f.stem.replace("copy_", "")
        print(f"  {wallet}: reverted {reverted} SL trades back to OPEN")
        total_reverted += reverted

print(f"\n  TOTAL: {total_reverted} trades reverted to OPEN")
print(f"  P&L recovered: ${total_pnl_recovered:+.2f}")
print(f"  These trades will now resolve naturally at $1.00 or $0.00")
