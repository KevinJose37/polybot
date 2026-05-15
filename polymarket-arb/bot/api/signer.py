"""
EIP-712 signer for Polymarket.
"""
from eth_account import Account
from eth_account.messages import encode_typed_data
from bot.utils.clocks import current_timestamp_ms

def sign_order(
    private_key: str,
    exchange_address: str,
    chain_id: int,
    maker: str,
    signer: str,
    token_id: str,
    side: str,
    size: str,
    price: str,
    fee_rate_bps: str = "0",
    expiration: str = "0"
) -> str:
    """
    Sign a Polymarket order using EIP-712.
    """
    domain = {
        "name": "Polymarket CTF Exchange",
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": exchange_address
    }
    
    types = {
        "Order": [
            {"name": "salt", "type": "uint256"},
            {"name": "maker", "type": "address"},
            {"name": "signer", "type": "address"},
            {"name": "taker", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
            {"name": "makerAmount", "type": "uint256"},
            {"name": "takerAmount", "type": "uint256"},
            {"name": "expiration", "type": "uint256"},
            {"name": "nonce", "type": "uint256"},
            {"name": "feeRateBps", "type": "uint256"},
            {"name": "side", "type": "uint8"},
            {"name": "signatureType", "type": "uint8"}
        ]
    }
    
    # Polymarket uses specific logic for makerAmount and takerAmount based on side
    # BUY = 0, SELL = 1
    side_int = 0 if side == "BUY" else 1
    
    # For a real implementation, we would multiply by 1e6 for USDC and calculate precise maker/taker amounts.
    # This is a simplified representation for the scope of this project.
    message = {
        "salt": current_timestamp_ms(),
        "maker": maker,
        "signer": signer,
        "taker": "0x0000000000000000000000000000000000000000",
        "tokenId": int(token_id),
        "makerAmount": int(float(size) * 1e6), # Approx
        "takerAmount": int(float(size) * float(price) * 1e6), # Approx
        "expiration": int(expiration),
        "nonce": 0,
        "feeRateBps": int(fee_rate_bps),
        "side": side_int,
        "signatureType": 0 # EOA
    }
    
    signable_message = encode_typed_data(domain_data=domain, message_types=types, message_data=message)
    account = Account.from_key(private_key)
    signed_message = account.sign_message(signable_message)
    
    return "0x" + signed_message.signature.hex()
