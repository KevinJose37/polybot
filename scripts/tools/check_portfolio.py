"""Check real portfolio on Polymarket CLOB."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from scalper.live_client import init_live_client, get_balance, get_token_balance
import json

print("=" * 60)
print("  POLYMARKET PORTFOLIO CHECK")
print("=" * 60)

# Init client
ok = init_live_client(dry_run=True)
if not ok:
    print("  FAILED to init client")
    sys.exit(1)

# Check USDC balance
bal = get_balance()
print(f"\n  USDC Balance: ${bal:.2f}" if bal else "\n  USDC Balance: UNKNOWN")

# Check all open trades from the trades file
print(f"\n  Checking positions from hft_trades_v2opt.json...")
print("-" * 60)

try:
    trades = json.load(open("hft_trades_v2opt.json"))
except FileNotFoundError:
    trades = []

open_trades = [t for t in trades if t.get("status") == "open"]
print(f"  Bot thinks it has {len(open_trades)} open positions:\n")

for t in open_trades:
    token_id = t.get("token_id", "")
    asset = t.get("asset", "?")
    side = t.get("side", "?")
    bot_shares = t.get("shares", 0)
    bot_stake = t.get("stake", 0)
    
    # Check actual on-chain balance
    actual = get_token_balance(token_id) if token_id else None
    
    match_str = ""
    if actual is not None:
        if abs(actual - bot_shares) < 0.01:
            match_str = "MATCH"
        elif actual > 0:
            match_str = f"MISMATCH (bot={bot_shares:.4f})"
        else:
            match_str = "NO SHARES ON-CHAIN"
    
    print(f"  {asset} {side}")
    print(f"    Bot:      {bot_shares:.4f} shares | ${bot_stake:.2f} stake")
    print(f"    On-chain: {actual:.4f} shares | {match_str}" if actual is not None else f"    On-chain: CHECK FAILED")
    print(f"    Token:    {token_id[:20]}...")
    print()

# Also check for any known orphaned positions
print("-" * 60)
print("  Checking for orphaned positions (GOD v1 trades)...")

# Load god trades to check if any old positions are still open
for fname in ["hft_trades.json", "hft_tradesgodv1.json"]:
    try:
        old_trades = json.load(open(fname))
        old_open = [t for t in old_trades if t.get("status") == "open" and t.get("token_id")]
        if old_open:
            print(f"\n  Found {len(old_open)} open trades in {fname}:")
            for t in old_open[:5]:
                token_id = t.get("token_id", "")
                if token_id:
                    actual = get_token_balance(token_id)
                    asset = t.get("asset", "?")
                    side = t.get("side", "?")
                    if actual and actual > 0:
                        print(f"    {asset} {side}: {actual:.4f} shares ON-CHAIN")
                    else:
                        print(f"    {asset} {side}: resolved (0 shares)")
    except FileNotFoundError:
        pass

print(f"\n{'=' * 60}")
print(f"  Final USDC: ${bal:.2f}" if bal else "  Final USDC: UNKNOWN")
print("=" * 60)
