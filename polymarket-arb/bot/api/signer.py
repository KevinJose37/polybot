"""
EIP-712 signer for Polymarket CTF Exchange.

Polymarket CTF Exchange order semantics:
  - BUY (side=0): Maker provides USDC (makerAmount) and receives conditional
    tokens (takerAmount).
  - SELL (side=1): Maker provides conditional tokens (makerAmount) and receives
    USDC (takerAmount).

Amount encoding:
  - USDC amounts are scaled to 6 decimal places (1e6 = 1 USDC).
  - Conditional token amounts are also scaled to 1e6.
  - makerAmount / takerAmount encode the *raw* quantities, not the price.
    Price is derived implicitly: price = USDC_amount / token_amount.

References:
  - https://docs.polymarket.com/#create-and-sign-an-order
  - CTF Exchange contract ABI on Polygonscan
"""
from eth_account import Account
from eth_account.messages import encode_typed_data
from bot.utils.clocks import current_timestamp_ms

import structlog

logger = structlog.get_logger(__name__)

# EIP-712 domain separator for the Polymarket CTF Exchange
_DOMAIN = {
    "name": "Polymarket CTF Exchange",
    "version": "1",
}

# EIP-712 type definition for an Order
_ORDER_TYPES = {
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
        {"name": "signatureType", "type": "uint8"},
    ]
}

# Polymarket side constants
_SIDE_BUY = 0
_SIDE_SELL = 1

# USDC has 6 decimal places
_USDC_DECIMALS = 10**6


def _parse_token_id(token_id: str) -> int:
    """Parse a token ID string to int, handling both decimal and hex formats."""
    token_id = token_id.strip()
    try:
        if token_id.startswith("0x") or token_id.startswith("0X"):
            return int(token_id, 16)
        return int(token_id)
    except (ValueError, OverflowError) as e:
        raise ValueError(
            f"Invalid token_id format: '{token_id}'. "
            "Expected a decimal integer or 0x-prefixed hex string."
        ) from e


def _compute_amounts(
    side: int, size: float, price: float
) -> tuple[int, int]:
    """Compute makerAmount and takerAmount per CTF Exchange semantics.

    BUY (side=0):
      - Maker gives USDC      → makerAmount = price × size × 1e6
      - Maker receives tokens  → takerAmount = size × 1e6

    SELL (side=1):
      - Maker gives tokens     → makerAmount = size × 1e6
      - Maker receives USDC    → takerAmount = price × size × 1e6
    """
    token_amount = int(size * _USDC_DECIMALS)
    usdc_amount = int(size * price * _USDC_DECIMALS)

    if side == _SIDE_BUY:
        return usdc_amount, token_amount
    else:
        return token_amount, usdc_amount


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
    expiration: str = "0",
    nonce: int = 0,
) -> str:
    """
    Sign a Polymarket order using EIP-712.

    Args:
        private_key: Hex-encoded private key with 0x prefix.
        exchange_address: CTF Exchange contract address on Polygon.
        chain_id: Chain ID (137 for Polygon mainnet).
        maker: Maker wallet address.
        signer: Signer address (usually same as maker for EOA).
        token_id: Conditional token ID (decimal or hex string).
        side: "BUY" or "SELL".
        size: Number of shares as a string.
        price: Price per share as a string (0.0 to 1.0).
        fee_rate_bps: Fee rate in basis points as a string.
        expiration: Unix timestamp for order expiry; "0" means no expiry.
        nonce: Order nonce for replay protection.

    Returns:
        Hex-encoded signature with 0x prefix.

    Raises:
        ValueError: If token_id format is invalid or side is unrecognised.
    """
    side_int = _SIDE_BUY if side == "BUY" else _SIDE_SELL

    parsed_size = float(size)
    parsed_price = float(price)

    if parsed_size <= 0:
        raise ValueError(f"Order size must be positive, got {parsed_size}")
    if not (0.0 < parsed_price <= 1.0):
        raise ValueError(f"Price must be in (0.0, 1.0], got {parsed_price}")

    token_id_int = _parse_token_id(token_id)
    maker_amount, taker_amount = _compute_amounts(side_int, parsed_size, parsed_price)

    domain = {
        **_DOMAIN,
        "chainId": chain_id,
        "verifyingContract": exchange_address,
    }

    message = {
        "salt": current_timestamp_ms(),
        "maker": maker,
        "signer": signer,
        "taker": "0x0000000000000000000000000000000000000000",
        "tokenId": token_id_int,
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": int(expiration),
        "nonce": nonce,
        "feeRateBps": int(fee_rate_bps),
        "side": side_int,
        "signatureType": 0,  # EOA
    }

    signable_message = encode_typed_data(
        domain_data=domain, message_types=_ORDER_TYPES, message_data=message
    )
    account = Account.from_key(private_key)
    signed_message = account.sign_message(signable_message)

    return "0x" + signed_message.signature.hex()
