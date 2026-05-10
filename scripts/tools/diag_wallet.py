"""Check on-chain wallet status for Polymarket trading."""
import os, json, requests
from dotenv import load_dotenv
load_dotenv()

ALCHEMY_KEY = os.getenv("ALCHEMY_POLYGON_KEY", "AAH9PMi13mOhWG0z-E1Kp")
ALCHEMY_URL = f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
FUNDER = os.getenv("POLY_FUNDER_ADDRESS", "")
PROXY = "0xb752bfc2B7A06941Ed372AB0Ccf72fCF1758441c"  # from private key

# Polymarket contracts
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC
POLYUSD = "0xbBa35d8027e86FEB3b84E0B10d3f42CbFec5bC0E"  # PolyUSD (pUSD) 

# Exchange contracts (from allowances diagnostic)
EXCHANGE_V1 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"

def call_rpc(method, params):
    r = requests.post(ALCHEMY_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params
    }, timeout=10)
    return r.json().get("result")

def get_erc20_balance(token, wallet):
    """Call balanceOf(address) on ERC20."""
    # balanceOf(address) = 0x70a08231 + address padded to 32 bytes
    addr_padded = wallet.lower().replace("0x", "").zfill(64)
    data = f"0x70a08231{addr_padded}"
    result = call_rpc("eth_call", [{"to": token, "data": data}, "latest"])
    if result and len(result) > 2:
        return int(result, 16)
    return 0

def get_allowance(token, owner, spender):
    """Call allowance(owner, spender) on ERC20."""
    owner_padded = owner.lower().replace("0x", "").zfill(64)
    spender_padded = spender.lower().replace("0x", "").zfill(64)
    data = f"0xdd62ed3e{owner_padded}{spender_padded}"
    result = call_rpc("eth_call", [{"to": token, "data": data}, "latest"])
    if result and len(result) > 2:
        return int(result, 16)
    return 0

print("=" * 60)
print("POLYMARKET ON-CHAIN WALLET DIAGNOSTICS")
print("=" * 60)

# Check balances for both addresses
for label, addr in [("Funder", FUNDER), ("Proxy", PROXY)]:
    print(f"\n── {label}: {addr[:10]}...{addr[-6:]} ──")
    
    # MATIC balance
    matic = call_rpc("eth_getBalance", [addr, "latest"])
    matic_val = int(matic, 16) / 1e18 if matic else 0
    print(f"  MATIC:       {matic_val:.4f}")
    
    # USDC.e balance
    usdc_e = get_erc20_balance(USDC_E, addr)
    print(f"  USDC.e:      {usdc_e / 1e6:.2f}")
    
    # Native USDC
    usdc_native = get_erc20_balance(USDC_NATIVE, addr)
    print(f"  USDC native: {usdc_native / 1e6:.2f}")
    
    # PolyUSD  
    pusd = get_erc20_balance(POLYUSD, addr)
    print(f"  PolyUSD:     {pusd / 1e6:.2f}")

# Check allowances (from funder to exchanges)
print(f"\n── Allowances (Funder → Exchanges) ──")
for ex_label, ex_addr in [("Exchange V1", EXCHANGE_V1), ("NegRisk", NEG_RISK), ("Exchange V2", EXCHANGE_V2)]:
    for token_label, token_addr in [("USDC.e", USDC_E), ("USDC", USDC_NATIVE), ("pUSD", POLYUSD)]:
        allow = get_allowance(token_addr, FUNDER, ex_addr)
        if allow > 0:
            status = "UNLIMITED" if allow > 1e30 else f"${allow / 1e6:.2f}"
        else:
            status = "NOT APPROVED ❌"
        print(f"  {ex_label:15} ← {token_label:6}: {status}")

# Check allowances (from proxy to exchanges)
print(f"\n── Allowances (Proxy → Exchanges) ──")
for ex_label, ex_addr in [("Exchange V1", EXCHANGE_V1), ("NegRisk", NEG_RISK), ("Exchange V2", EXCHANGE_V2)]:
    for token_label, token_addr in [("USDC.e", USDC_E), ("USDC", USDC_NATIVE), ("pUSD", POLYUSD)]:
        allow = get_allowance(token_addr, PROXY, ex_addr)
        if allow > 0:
            status = "UNLIMITED" if allow > 1e30 else f"${allow / 1e6:.2f}"
        else:
            status = "NOT APPROVED ❌"
        print(f"  {ex_label:15} ← {token_label:6}: {status}")

print(f"\n{'=' * 60}")
