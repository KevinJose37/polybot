"""Test V2 SDK initialization."""
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, MarketOrderArgs, OrderType
print("V2 SDK imports OK")
options = [x for x in dir(OrderType) if not x.startswith("_")]
print(f"OrderType options: {options}")

from scalper.live_client import init_live_client, get_balance
ok = init_live_client(dry_run=False)
print(f"Init: {ok}")
if ok:
    bal = get_balance()
    print(f"USDC: {bal}")
