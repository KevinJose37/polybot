"""
scalper/live_client.py — Live trading client for Polymarket CLOB V2.

Uses py-clob-client-v2 SDK for V2-compatible order signing and posting.
Only activated when --live flag is passed.

This module does NOT modify any existing bot logic. It provides
standalone functions that trader.py calls when in live mode.
"""

import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("polybot.scalper.live_client")

# Polymarket minimum order size for BUY market orders
MIN_ORDER_AMOUNT = 1.0


def _retry_call(fn, *args, retries=3, delay=2.0, **kwargs):
    """Retry a CLOB API call on 401 (rate limit masquerading as auth error)."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if "401" in str(exc) and attempt < retries - 1:
                wait = delay * (2 ** attempt)
                logger.debug("Rate-limited (401), retry %d/%d in %.1fs", attempt + 1, retries, wait)
                time.sleep(wait)
            else:
                raise


# ═══════════════════════════════════════════════════════════════
# Client Initialization
# ═══════════════════════════════════════════════════════════════

_client = None
_dry_run = False


def init_live_client(dry_run: bool = False) -> bool:
    """
    Initialize the CLOB V2 client with credentials from .env.

    Returns True if successful, False if missing credentials.
    """
    global _client, _dry_run
    _dry_run = dry_run

    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    api_key = os.getenv("POLY_API_KEY", "")
    api_secret = os.getenv("POLY_API_SECRET", "")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE", "")
    funder = os.getenv("POLY_FUNDER_ADDRESS", "")

    if not all([private_key, api_key, api_secret, api_passphrase]):
        logger.error("Missing Polymarket credentials in .env")
        return False

    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds

        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )

        client_kwargs = {
            "host": "https://clob.polymarket.com",
            "chain_id": 137,  # Polygon
            "key": private_key,
            "creds": creds,
        }

        if funder:
            client_kwargs["funder"] = funder
            client_kwargs["signature_type"] = 1
            logger.info("Using proxy wallet with funder: %s", funder[:10])

        _client = ClobClient(**client_kwargs)

        # CRITICAL: Use server time for HMAC signatures to avoid clock drift 401s
        _client.use_server_time = True

        mode_str = "DRY-RUN" if dry_run else "LIVE"
        logger.info("CLOB V2 client initialized in %s mode (server-time sync)", mode_str)
        print(f"  [CLOB V2] Client initialized: {mode_str} mode (server-time sync)")
        return True

    except Exception as exc:
        logger.error("Failed to initialize CLOB V2 client: %s", exc)
        return False


def is_live() -> bool:
    """Check if live trading is enabled."""
    return _client is not None


def is_dry_run() -> bool:
    """Check if running in dry-run mode (log orders but don't send)."""
    return _dry_run


# ═══════════════════════════════════════════════════════════════
# Order Operations  (CLOB V2 — create_market_order + post_order)
# ═══════════════════════════════════════════════════════════════


def buy_outcome(
    token_id: str,
    price: float,
    size: float,
    asset: str = "",
    side: str = "",
) -> dict | None:
    """
    Buy outcome tokens on the CLOB using market order (FOK).

    Returns dict with on-chain verified data:
      - shares: actual shares received
      - actual_cost: USDC spent (from balance delta)
      - actual_entry_price: real cost per share
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
        print(f"  [DRY-RUN BUY] {label} @ {price:.4f} | ${size:.2f}")
        return {"dry_run": True, "action": "BUY", "token_id": token_id}

    try:
        from py_clob_client_v2.clob_types import MarketOrderArgs, OrderType
        from py_clob_client_v2.order_builder.builder import BUY

        # ── Duplicate entry guard (non-blocking) ────────────────────
        try:
            existing_shares = _retry_call(get_token_balance, token_id)
            if existing_shares is not None and existing_shares > 0:
                logger.warning("Already holding %.2f shares of %s. Skipping BUY.", existing_shares, label)
                print(f"  [BUY SKIPPED] {label} | Already holding {existing_shares:.2f} shares")
                return {"success": True, "shares": existing_shares, "token_id": token_id, "already_held": True}
        except Exception:
            logger.debug("Pre-check balance failed, proceeding with order")

        # ── Snapshot USDC balance BEFORE ────────────────────────────
        usdc_before = _retry_call(get_balance) or 0.0

        order_amount = max(size, MIN_ORDER_AMOUNT)
        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=order_amount,
            side=BUY,
            order_type=OrderType.FOK,
        )

        logger.info("Sending MARKET BUY: %s | $%.2f", label, order_amount)
        signed = _client.create_market_order(market_order)

        # NOTE: Do NOT retry post_order — a 401 may mask a successful fill.
        try:
            resp = _client.post_order(signed, OrderType.FOK)
            err = resp.get("error_message") if isinstance(resp, dict) else None
            if err:
                logger.warning("BUY order API returned error: %s", err)
        except Exception as api_exc:
            logger.warning("BUY order POST exception: %s (checking on-chain...)", api_exc)
            resp = None

        # ── Verify on-chain: shares received + USDC spent ──────────
        time.sleep(2.0)
        actual_shares = _retry_call(get_token_balance, token_id)
        usdc_after = _retry_call(get_balance) or 0.0

        if actual_shares is not None and actual_shares > 0:
            actual_cost = round(usdc_before - usdc_after, 2)
            actual_entry_price = round(actual_cost / actual_shares, 4) if actual_shares > 0 else price

            logger.info(
                "Verified BUY on-chain: %.4f shares | Cost $%.2f | Avg price $%.4f",
                actual_shares, actual_cost, actual_entry_price,
            )
            print(f"  [LIVE BUY] {label} | {actual_shares:.2f} shares @ ${actual_entry_price:.4f} (cost ${actual_cost:.2f})")
            return {
                "success": True,
                "shares": actual_shares,
                "actual_cost": actual_cost,
                "actual_entry_price": actual_entry_price,
                "token_id": token_id,
                "resp": resp,
            }

        logger.error("BUY order failed and no on-chain balance found for %s", label)
        print(f"  [BUY FAILED] {label} | No fill detected")
        return None

    except Exception as exc:
        logger.error("BUY order failed for %s: %s", label, exc)
        print(f"  [BUY FAILED] {label} | {exc}")
        return None


def sell_outcome(
    token_id: str,
    price: float,
    size: float,
    asset: str = "",
    side: str = "",
) -> dict | None:
    """
    Sell outcome tokens on the CLOB using market order (FOK).
    Retries up to 3 times with on-chain verification.

    Returns dict with on-chain verified data:
      - actual_proceeds: USDC received (from balance delta)
      - remaining_shares: shares left after sell
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
        print(f"  [DRY-RUN SELL] {label} @ {price:.4f} | {size:.2f} shares")
        return {"dry_run": True, "action": "SELL", "token_id": token_id}

    try:
        from py_clob_client_v2.clob_types import MarketOrderArgs, OrderType
        from py_clob_client_v2.order_builder.builder import SELL

        logger.info("Sending MARKET SELL: %s | %.2f shares @ ~$%.4f", label, size, price)

        # ── Snapshot USDC balance BEFORE sell ───────────────────────
        usdc_before = _retry_call(get_balance) or 0.0

        # ── Sell Retry Loop & Fill Verification ─────────────────────
        for attempt in range(1, 4):
            # Check actual on-chain balance before each attempt
            actual_shares = _retry_call(get_token_balance, token_id)
            if actual_shares is not None and actual_shares < 0.01:
                logger.info("Shares already sold (balance: %.6f).", actual_shares)
                usdc_after = _retry_call(get_balance) or 0.0
                actual_proceeds = round(usdc_after - usdc_before, 2)
                print(f"  [LIVE SELL] {label} | Sold (proceeds ${actual_proceeds:.2f})")
                return {
                    "success": True,
                    "remaining_shares": actual_shares,
                    "actual_proceeds": actual_proceeds,
                    "token_id": token_id,
                }

            # Use actual balance for sell amount (no $1 minimum — that's BUY-only)
            shares_to_sell = actual_shares if actual_shares is not None else size
            sell_amount = shares_to_sell * price

            market_order = MarketOrderArgs(
                token_id=token_id,
                amount=sell_amount,
                side=SELL,
                order_type=OrderType.FOK,
            )

            try:
                signed = _client.create_market_order(market_order)
                resp = _client.post_order(signed, OrderType.FOK)
                err = resp.get("error_message") if isinstance(resp, dict) else None
                if err:
                    logger.warning("SELL attempt %d API error: %s", attempt, err)
            except Exception as api_exc:
                logger.warning("SELL attempt %d POST exception: %s", attempt, api_exc)

            # Wait for blockchain sync then verify
            time.sleep(2.0)

            current_shares = _retry_call(get_token_balance, token_id)
            if current_shares is not None:
                if current_shares < 0.01:
                    usdc_after = _retry_call(get_balance) or 0.0
                    actual_proceeds = round(usdc_after - usdc_before, 2)
                    logger.info("Verified SELL on-chain. Proceeds: $%.2f", actual_proceeds)
                    print(f"  [LIVE SELL] {label} | Sold (proceeds ${actual_proceeds:.2f})")
                    return {
                        "success": True,
                        "remaining_shares": current_shares,
                        "actual_proceeds": actual_proceeds,
                        "token_id": token_id,
                    }
                elif current_shares < shares_to_sell:
                    logger.info("Partial fill detected: %.4f -> %.4f", shares_to_sell, current_shares)
                else:
                    logger.warning("SELL attempt %d failed to fill (shares unchanged).", attempt)

            logger.info("Retrying SELL in 2s...")
            time.sleep(2.0)

        logger.error("SELL completely failed for %s after 3 attempts.", label)
        print(f"  [SELL FAILED] {label} | Could not fill")
        return None

    except Exception as exc:
        logger.error("SELL order failed for %s: %s", label, exc)
        print(f"  [SELL FAILED] {label} | {exc}")
        return None


# ═══════════════════════════════════════════════════════════════
# Balance Queries
# ═══════════════════════════════════════════════════════════════


def get_balance() -> float | None:
    """Get current USDC/PolyUSD balance from the CLOB (in dollars)."""
    if not _client:
        return None

    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        res = _client.get_balance_allowance(params)

        if isinstance(res, dict) and 'balance' in res:
            # Raw balance is in 6-decimal USDC units (e.g. 40000000 = $40.00)
            return float(res['balance']) / 1e6
        return None
    except Exception as exc:
        logger.error("Balance check failed: %s", exc)
        return None


def get_token_balance(token_id: str) -> float | None:
    """Get current outcome token balance from the CLOB (in shares)."""
    if not _client:
        return None

    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
        res = _client.get_balance_allowance(params)

        if isinstance(res, dict) and 'balance' in res:
            # Raw balance is in 6-decimal units (e.g. 2000000 = 2.0 shares)
            return float(res['balance']) / 1e6
        return 0.0
    except Exception as exc:
        logger.error("Token balance check failed for %s: %s", token_id[:8], exc)
        return 0.0
