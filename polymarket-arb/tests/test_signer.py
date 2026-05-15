"""
Tests for EIP-712 signer.
"""
from bot.api.signer import sign_order
from eth_account import Account
import os

def test_sign_order() -> None:
    # Use a dummy private key
    dummy_key = "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    account = Account.from_key(dummy_key)
    
    signature = sign_order(
        private_key=dummy_key,
        exchange_address="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        chain_id=137,
        maker=account.address,
        signer=account.address,
        token_id="123456",
        side="BUY",
        size="10.5",
        price="0.50",
        fee_rate_bps="0",
        expiration="0"
    )
    
    # Signature is hex string
    assert signature.startswith("0x")
    assert len(signature) == 132 # 65 bytes in hex + "0x"
