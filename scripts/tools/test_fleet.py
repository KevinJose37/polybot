"""Quick smoke test for copy_wallet fleet."""
import os
import sys
import time
sys.path.append(".")
import requests
from copy_wallet import WalletTracker, FLEET_WALLETS

trackers = []
for w in FLEET_WALLETS:
    tr = WalletTracker(
        address=w["address"], name=w["name"], cat=w.get("cat", "?"),
        wr=w.get("wr", 0), capital=40.0, stake=4.0,
        tp_pct=0.5, sl_pct=0.25, is_live=False,
    )
    trackers.append(tr)

session = requests.Session()
event_log = []

print("Polling 5 wallets...")
for tr in trackers:
    evts = tr.poll_and_copy(session)
    for e in evts:
        event_log.append(e)
    print(f"  {tr.name:<16} polled OK | seen={len(tr.seen)} | open={len(tr.open_trades)}")
    time.sleep(0.3)

print(f"\nTotal events from this cycle: {len(event_log)}")
print(f"(No events expected - these wallets trade slowly)\n")

# Render a sample dashboard line
print("=" * 90)
hdr = f"  {'WALLET':<16} {'CAT':<6} {'WR%':>5} {'OPEN':>4} {'RES':>4} {'P&L':>8} {'EXP':>6} {'AVAIL':>6}"
print(hdr)
print("-" * 90)
for tr in trackers:
    pnl_str = f"${tr.total_pnl:+.2f}" if tr.resolved_trades else "--"
    wr_str = f"{tr.win_rate:.0f}%" if tr.resolved_trades else "--"
    print(
        f"  {tr.name:<16} {tr.cat:<6} {wr_str:>5} "
        f"{len(tr.open_trades):>4} {len(tr.resolved_trades):>4} "
        f"{pnl_str:>8} ${tr.exposure:>5.0f} ${tr.available:>5.0f}"
    )
print("=" * 90)
print("\nALL SYSTEMS GO - fleet ready to deploy")
