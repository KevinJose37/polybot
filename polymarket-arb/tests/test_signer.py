"""
Tests for EIP-712 signer.
"""
import pytest
from bot.api.signer import sign_order, _parse_token_id, _compute_amounts, _SIDE_BUY, _SIDE_SELL
from eth_account import Account


_DUMMY_KEY = "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
_CHAIN_ID = 137


@pytest.fixture
def account():
    return Account.from_key(_DUMMY_KEY)


# ── sign_order smoke tests ─────────────────────────────────────────

def test_sign_order_buy(account) -> None:
    signature = sign_order(
        private_key=_DUMMY_KEY,
        exchange_address=_EXCHANGE,
        chain_id=_CHAIN_ID,
        maker=account.address,
        signer=account.address,
        token_id="123456",
        side="BUY",
        size="10.5",
        price="0.50",
    )
    assert signature.startswith("0x")
    assert len(signature) == 132  # 65 bytes in hex + "0x"


def test_sign_order_sell(account) -> None:
    signature = sign_order(
        private_key=_DUMMY_KEY,
        exchange_address=_EXCHANGE,
        chain_id=_CHAIN_ID,
        maker=account.address,
        signer=account.address,
        token_id="123456",
        side="SELL",
        size="10.5",
        price="0.50",
    )
    assert signature.startswith("0x")
    assert len(signature) == 132


def test_sign_order_buy_sell_different_signatures(account) -> None:
    """BUY and SELL with the same params must produce different signatures."""
    buy_sig = sign_order(
        private_key=_DUMMY_KEY,
        exchange_address=_EXCHANGE,
        chain_id=_CHAIN_ID,
        maker=account.address,
        signer=account.address,
        token_id="123456",
        side="BUY",
        size="10.0",
        price="0.45",
        nonce=42,  # fixed nonce to avoid salt-only difference
    )
    sell_sig = sign_order(
        private_key=_DUMMY_KEY,
        exchange_address=_EXCHANGE,
        chain_id=_CHAIN_ID,
        maker=account.address,
        signer=account.address,
        token_id="123456",
        side="SELL",
        size="10.0",
        price="0.45",
        nonce=42,
    )
    # Signatures differ because side and makerAmount/takerAmount differ
    assert buy_sig != sell_sig


# ── _compute_amounts tests ──────────────────────────────────────────

def test_compute_amounts_buy() -> None:
    """BUY: maker gives USDC, receives tokens."""
    maker_amt, taker_amt = _compute_amounts(_SIDE_BUY, size=10.0, price=0.45)
    assert maker_amt == int(10.0 * 0.45 * 1e6)  # USDC out = 4_500_000
    assert taker_amt == int(10.0 * 1e6)           # tokens in = 10_000_000


def test_compute_amounts_sell() -> None:
    """SELL: maker gives tokens, receives USDC."""
    maker_amt, taker_amt = _compute_amounts(_SIDE_SELL, size=10.0, price=0.45)
    assert maker_amt == int(10.0 * 1e6)           # tokens out = 10_000_000
    assert taker_amt == int(10.0 * 0.45 * 1e6)   # USDC in = 4_500_000


def test_compute_amounts_symmetry() -> None:
    """BUY makerAmount == SELL takerAmount and vice versa."""
    buy_maker, buy_taker = _compute_amounts(_SIDE_BUY, size=5.0, price=0.60)
    sell_maker, sell_taker = _compute_amounts(_SIDE_SELL, size=5.0, price=0.60)
    assert buy_maker == sell_taker  # USDC amounts match
    assert buy_taker == sell_maker  # token amounts match


# ── _parse_token_id tests ───────────────────────────────────────────

def test_parse_token_id_decimal() -> None:
    assert _parse_token_id("123456") == 123456


def test_parse_token_id_hex() -> None:
    assert _parse_token_id("0x1E240") == 0x1E240


def test_parse_token_id_large_decimal() -> None:
    """Polymarket token IDs can be very large decimal strings."""
    large_id = "115792089237316195423570985008687907853269984665640564039457584007913129639935"
    result = _parse_token_id(large_id)
    assert result == int(large_id)


def test_parse_token_id_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid token_id format"):
        _parse_token_id("not_a_number")


def test_parse_token_id_empty() -> None:
    with pytest.raises(ValueError, match="Invalid token_id format"):
        _parse_token_id("")


# ── input validation tests ──────────────────────────────────────────

def test_sign_order_invalid_size(account) -> None:
    with pytest.raises(ValueError, match="size must be positive"):
        sign_order(
            private_key=_DUMMY_KEY,
            exchange_address=_EXCHANGE,
            chain_id=_CHAIN_ID,
            maker=account.address,
            signer=account.address,
            token_id="123456",
            side="BUY",
            size="0",
            price="0.50",
        )


def test_sign_order_invalid_price_zero(account) -> None:
    with pytest.raises(ValueError, match="Price must be in"):
        sign_order(
            private_key=_DUMMY_KEY,
            exchange_address=_EXCHANGE,
            chain_id=_CHAIN_ID,
            maker=account.address,
            signer=account.address,
            token_id="123456",
            side="BUY",
            size="10",
            price="0.0",
        )


def test_sign_order_invalid_price_above_one(account) -> None:
    with pytest.raises(ValueError, match="Price must be in"):
        sign_order(
            private_key=_DUMMY_KEY,
            exchange_address=_EXCHANGE,
            chain_id=_CHAIN_ID,
            maker=account.address,
            signer=account.address,
            token_id="123456",
            side="BUY",
            size="10",
            price="1.5",
        )


def test_sign_order_hex_token_id(account) -> None:
    """Should accept hex-encoded token IDs."""
    signature = sign_order(
        private_key=_DUMMY_KEY,
        exchange_address=_EXCHANGE,
        chain_id=_CHAIN_ID,
        maker=account.address,
        signer=account.address,
        token_id="0xDEADBEEF",
        side="BUY",
        size="5.0",
        price="0.30",
    )
    assert signature.startswith("0x")
    assert len(signature) == 132
