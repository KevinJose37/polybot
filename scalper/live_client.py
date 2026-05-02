"""
scalper/live_client.py — Live trading client for Polymarket CLOB.

Wraps py-clob-client to provide BUY/SELL operations for the HFT scalper.
Only activated when POLY_LIVE_MODE=true in .env.

This module does NOT modify any existing bot logic. It provides
standalone functions that trader.py calls when in live mode.
"""

import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("polybot.scalper.live_client")

# ═══════════════════════════════════════════════════════════════
# Client Initialization
# ═══════════════════════════════════════════════════════════════

_client = None
_dry_run = False


def init_live_client(dry_run: bool = False) -> bool:
    """
    Initialize the CLOB client with credentials from .env.

    Returns True if successful, False if missing credentials.
    """
    global _client, _dry_run
    _dry_run = dry_run

    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    api_key = os.getenv("POLY_API_KEY", "")
    api_secret = os.getenv("POLY_API_SECRET", "")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE", "")

    if not all([private_key, api_key, api_secret, api_passphrase]):
        logger.error("Missing Polymarket credentials in .env")
        return False

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )

        _client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,  # Polygon
            key=private_key,
            creds=creds,
        )

        mode_str = "DRY-RUN" if dry_run else "LIVE"
        logger.info("CLOB client initialized in %s mode", mode_str)
        print(f"  🔗 CLOB client: {mode_str} mode")
        return True

    except Exception as exc:
        logger.error("Failed to initialize CLOB client: %s", exc)
        return False


def is_live() -> bool:
    """Check if live trading is enabled."""
    return _client is not None


def is_dry_run() -> bool:
    """Check if running in dry-run mode (log orders but don't send)."""
    return _dry_run


# ═══════════════════════════════════════════════════════════════
# Order Operations
# ═══════════════════════════════════════════════════════════════


def buy_outcome(
    token_id: str,
    price: float,
    size: float,
    asset: str = "",
    side: str = "",
) -> dict | None:
    """
    Buy outcome tokens on the CLOB.

    Args:
        token_id: The outcome token ID (from Gamma API)
        price: Limit price (0-1)
        size: Amount in USDC to spend
        asset: Asset name for logging (e.g., "BTC")
        side: Direction for logging (e.g., "UP")

    Returns order response dict or None on failure.
    """
    if not _client:
        logger.error("CLOB client not initialized")
        return None

    label = f"{asset} {side}" if asset else "unknown"

    if _dry_run:
        logger.info(
            "DRY-RUN BUY: %s | token=%s price=%.4f size=%.2f",
            label, token_id[:16], price, size,
        )
        print(f"  🏷️ DRY-RUN BUY: {label} @ {price:.4f} | ${size:.2f}")
        return {"dry_run": True, "action": "BUY", "token_id": token_id}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType

        order_args = OrderArgs(
            price=price,
            size=size,
            side="BUY",
            token_id=token_id,
        )

        logger.info("Sending BUY order: %s @ %.4f | $%.2f", label, price, size)

        resp = _client.create_and_post_order(order_args)

        logger.info("BUY order response: %s", resp)
        print(f"  ✅ LIVE BUY: {label} @ {price:.4f} | ${size:.2f}")
        return resp

    except Exception as exc:
        logger.error("BUY order failed for %s: %s", label, exc)
        print(f"  ❌ BUY FAILED: {label} | {exc}")
        return None


def sell_outcome(
    token_id: str,
    price: float,
    size: float,
    asset: str = "",
    side: str = "",
) -> dict | None:
    """
    Sell outcome tokens on the CLOB.

    Args:
        token_id: The outcome token ID
        price: Limit price (0-1)
        size: Number of shares to sell
        asset: Asset name for logging
        side: Direction for logging

    Returns order response dict or None on failure.
    """
    if not _client:
        logger.error("CLOB client not initialized")
        return None

    label = f"{asset} {side}" if asset else "unknown"

    if _dry_run:
        logger.info(
            "DRY-RUN SELL: %s | token=%s price=%.4f size=%.2f",
            label, token_id[:16], price, size,
        )
        print(f"  🏷️ DRY-RUN SELL: {label} @ {price:.4f} | {size:.2f} shares")
        return {"dry_run": True, "action": "SELL", "token_id": token_id}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType

        order_args = OrderArgs(
            price=price,
            size=size,
            side="SELL",
            token_id=token_id,
        )

        logger.info("Sending SELL order: %s @ %.4f | %.2f shares", label, price, size)

        resp = _client.create_and_post_order(order_args)

        logger.info("SELL order response: %s", resp)
        print(f"  ✅ LIVE SELL: {label} @ {price:.4f} | {size:.2f} shares")
        return resp

    except Exception as exc:
        logger.error("SELL order failed for %s: %s", label, exc)
        print(f"  ❌ SELL FAILED: {label} | {exc}")
        return None


def get_balance() -> float | None:
    """Get current USDC balance from the CLOB."""
    if not _client:
        return None

    try:
        # The py-clob-client doesn't have a direct balance method,
        # but we can check allowances
        logger.debug("Balance check requested")
        return None  # Will be implemented when needed
    except Exception as exc:
        logger.error("Balance check failed: %s", exc)
        return None
