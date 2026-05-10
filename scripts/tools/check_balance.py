"""Check Polymarket wallet balance and status."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv()

# 1. Derive proxy from current private key
from eth_account import Account
pk = os.getenv("POLY_PRIVATE_KEY", "")
funder = os.getenv("POLY_FUNDER_ADDRESS", "")
sig_type = os.getenv("POLY_SIGNATURE_TYPE", "1")

derived = Account.from_key(pk)
print(f"Proxy (from PK):  {derived.address}")
print(f"Funder (.env):    {funder}")
print(f"Sig type:         {sig_type}")

# 2. Check CLOB API balance (what matters for trading)
from scalper.live_client import init_live_client
init_live_client(dry_run=False)
from scalper.live_client import _client

from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
try:
    col = _client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    balance_raw = int(col.get("balance", 0))
    balance_usd = balance_raw / 1e6
    print(f"\n=== CLOB Balance: ${balance_usd:.2f} ===")
    print(f"Allowances:")
    for addr, val in col.get("allowances", {}).items():
        v = int(val)
        status = "UNLIMITED" if v > 1e30 else f"${v/1e6:.2f}" if v > 0 else "NOT APPROVED"
        print(f"  {addr[:10]}...: {status}")
except Exception as e:
    print(f"Balance error: {e}")
