"""Check on-chain activity of fleet wallets."""
import requests, json, sys
sys.path.insert(0, ".")
from copy_wallet import FLEET_WALLETS

URL = "https://polygon-mainnet.g.alchemy.com/v2/AAH9PMi13mOhWG0z-E1Kp"
CTF = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
wallets = set(w["address"].lower() for w in FLEET_WALLETS)

r = requests.post(URL, json={"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}).json()
latest = int(r["result"], 16)
start = 86635476
print(f"Current block: {latest} | Since listener start: {latest - start} blocks")

r2 = requests.post(URL, json={
    "jsonrpc":"2.0","id":2,"method":"alchemy_getAssetTransfers",
    "params":[{
        "fromBlock": hex(latest - 50),
        "toBlock": hex(latest),
        "contractAddresses":[CTF],
        "category":["erc1155"],
        "order":"desc",
        "maxCount":"0x14"
    }]
}).json()

transfers = r2.get("result",{}).get("transfers",[])
print(f"Last 50 blocks: {len(transfers)} CTF transfers total")
matched = 0
for t in transfers:
    fr = (t.get("from") or "").lower()
    to = (t.get("to") or "").lower()
    blk = t.get("blockNum", "?")
    if fr in wallets or to in wallets:
        matched += 1
        who = fr if fr in wallets else to
        d = "SELL" if fr in wallets else "BUY"
        print(f"  MATCH! {d} wallet={who[:14]}.. blk={blk}")
    else:
        print(f"  other: from={fr[:14]}.. to={to[:14]}.. blk={blk}")
if matched == 0:
    print("  No fleet wallet trades in last 50 blocks (normal between rounds)")
