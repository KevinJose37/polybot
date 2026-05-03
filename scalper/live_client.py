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

# Sell 99% of shares to work around CLOB server-side balance cache bug.
# After a status=matched buy, the CLOB's cached balance is slightly stale,
# causing 100% sells to fail with "not enough balance/allowance".
# The ~1% dust auto-settles at market resolution.
_SELL_SIZE_FACTOR = 0.99


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
    # 0=EOA direct, 1=proxy (email/magic), 2=proxy (browser wallet/MetaMask)
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))

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
            client_kwargs["signature_type"] = sig_type
            logger.info(
                "Using proxy wallet (sig_type=%d) with funder: %s",
                sig_type, funder[:10],
            )

        _client = ClobClient(**client_kwargs)

        # CRITICAL: Use server time for HMAC signatures to avoid clock drift 401s
        _client.use_server_time = True

        mode_str = "DRY-RUN" if dry_run else "LIVE"
        logger.info("CLOB V2 client initialized in %s mode (sig_type=%d, server-time sync)", mode_str, sig_type)
        print(f"  [CLOB V2] Client initialized: {mode_str} mode (sig_type={sig_type})")
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
            order_type=OrderType.FAK,
        )

        logger.info("Sending MARKET BUY: %s | $%.2f", label, order_amount)
        signed = _client.create_market_order(market_order)

        # NOTE: Do NOT retry post_order — a 401 may mask a successful fill.
        try:
            resp = _client.post_order(signed, OrderType.FAK)
            err = resp.get("error_message") if isinstance(resp, dict) else None
            if err:
                logger.warning("BUY order API returned error: %s", err)
        except Exception as api_exc:
            logger.warning("BUY order POST exception: %s (checking on-chain...)", api_exc)
            resp = None

        # ── Verify on-chain: shares received + USDC spent ──────────
        # Retry verification — blockchain can take 3-8 seconds to reflect
        for verify_attempt in range(3):
            wait_time = 4.0 if verify_attempt == 0 else 3.0
            time.sleep(wait_time)
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

            logger.info("BUY verify attempt %d: no shares yet, retrying...", verify_attempt + 1)

        # Final check: also verify USDC decreased (order might have filled
        # but token balance API is slow)
        usdc_after = _retry_call(get_balance) or 0.0
        usdc_spent = round(usdc_before - usdc_after, 2)
        if usdc_spent >= size * 0.5:
            # USDC decreased significantly — order likely filled but token balance lagging
            logger.warning(
                "BUY for %s: USDC spent $%.2f but no token balance yet. "
                "Assuming fill to avoid orphaned position.", label, usdc_spent,
            )
            estimated_shares = round(usdc_spent / price, 4)
            print(f"  [LIVE BUY] {label} | ~{estimated_shares:.2f} shares (USDC-verified, cost ${usdc_spent:.2f})")
            return {
                "success": True,
                "shares": estimated_shares,
                "actual_cost": usdc_spent,
                "actual_entry_price": price,
                "token_id": token_id,
                "resp": resp,
                "usdc_verified": True,
            }

        logger.error("BUY order failed and no on-chain balance found for %s", label)
        print(f"  [BUY FAILED] {label} | No fill detected after 3 attempts")
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

        # ── Pre-sell diagnostics ─────────────────────────────────
        usdc_before = _retry_call(get_balance) or 0.0
        actual_shares = _retry_call(get_token_balance, token_id)

        print(f"\n  ┌─ SELL DIAGNOSTICS: {label} ─────────────────────")
        print(f"  │ Token:       {token_id[:24]}...")
        print(f"  │ On-chain:    {actual_shares:.4f} shares" if actual_shares else "  │ On-chain:    UNKNOWN")
        print(f"  │ Bot expects: {size:.4f} shares")
        print(f"  │ USDC before: ${usdc_before:.2f}")
        print(f"  │ Ask price:   ${price:.4f}")

        # Check conditional token allowance
        try:
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
            bal_allow = _client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            )
            cond_balance = float(bal_allow.get("balance", 0)) / 1e6  # USDC has 6 decimals
            print(f"  │ CLOB cond balance: {cond_balance:.4f}")
            logger.info("SELL DIAG: CLOB conditional balance=%s allowances=%s",
                        bal_allow.get("balance"), bal_allow.get("allowances"))
        except Exception as diag_exc:
            print(f"  │ CLOB cond balance: ERROR ({diag_exc})")

        print(f"  └──────────────────────────────────────────────────")

        if actual_shares is not None and actual_shares < 0.01:
            logger.info("Shares already gone (balance: %.6f).", actual_shares)
            usdc_after = _retry_call(get_balance) or 0.0
            actual_proceeds = round(usdc_after - usdc_before, 2)
            print(f"  [LIVE SELL] {label} | Already resolved (Δ ${actual_proceeds:.2f})")
            return {
                "success": True,
                "remaining_shares": actual_shares,
                "actual_proceeds": actual_proceeds,
                "token_id": token_id,
            }

        # ── Sell Retry Loop ──────────────────────────────────────
        shares_to_sell = (actual_shares if actual_shares is not None else size) * _SELL_SIZE_FACTOR
        original_shares = actual_shares if actual_shares is not None else size

        for attempt in range(1, 4):
            # Re-check balance before each retry (shares may have changed)
            if attempt > 1:
                current_bal = _retry_call(get_token_balance, token_id)
                if current_bal is not None:
                    if current_bal < 0.1:
                        # Dust remaining — treat as success
                        usdc_after = _retry_call(get_balance) or 0.0
                        actual_proceeds = round(usdc_after - usdc_before, 2)
                        sold_pct = (1 - current_bal / original_shares) * 100 if original_shares > 0 else 100
                        print(f"  [LIVE SELL] {label} | ✅ Sold {sold_pct:.0f}% (dust {current_bal:.4f} remaining, proceeds ${actual_proceeds:.2f})")
                        return {
                            "success": True,
                            "remaining_shares": current_bal,
                            "actual_proceeds": actual_proceeds,
                            "token_id": token_id,
                        }
                    # Recalculate sell amount from actual remaining balance
                    shares_to_sell = current_bal * _SELL_SIZE_FACTOR

            logger.info(
                "SELL attempt %d: %.4f shares (FAK, token=%s)",
                attempt, shares_to_sell, token_id[:20],
            )

            market_order = MarketOrderArgs(
                token_id=token_id,
                amount=shares_to_sell,
                side=SELL,
                order_type=OrderType.FAK,
            )

            try:
                signed = _client.create_market_order(market_order)
                resp = _client.post_order(signed, OrderType.FAK)

                # Log full response for diagnostics
                logger.info("SELL attempt %d response: %s", attempt, resp)

                if isinstance(resp, dict):
                    status = resp.get("status", "unknown")
                    err = resp.get("error_message") or resp.get("errorMsg")
                    order_id = resp.get("orderID") or resp.get("id", "?")

                    print(f"  [SELL #{attempt}] status={status} | order={str(order_id)[:12]}")

                    if err:
                        err_lower = str(err).lower()
                        if "not enough balance" in err_lower:
                            print(f"  [SELL #{attempt}] ⚠️ BALANCE/ALLOWANCE issue")
                            logger.warning("SELL %d: balance error: %s", attempt, err)
                        elif "no match" in err_lower or "not filled" in err_lower:
                            print(f"  [SELL #{attempt}] ⚠️ NO LIQUIDITY on book")
                            logger.warning("SELL %d: no liquidity: %s", attempt, err)
                        else:
                            print(f"  [SELL #{attempt}] ⚠️ Error: {err}")
                            logger.warning("SELL %d: API error: %s", attempt, err)
                    elif status == "matched":
                        print(f"  [SELL #{attempt}] ✅ Order matched!")
                else:
                    logger.info("SELL attempt %d non-dict response: %s", attempt, type(resp))

            except Exception as api_exc:
                err_str = str(api_exc).lower()
                if "no match" in err_str or "not filled" in err_str:
                    print(f"  [SELL #{attempt}] ⚠️ NO LIQUIDITY: {api_exc}")
                elif "not enough balance" in err_str:
                    print(f"  [SELL #{attempt}] ⚠️ BALANCE ERROR: {api_exc}")
                else:
                    print(f"  [SELL #{attempt}] ❌ Exception: {api_exc}")
                logger.warning("SELL attempt %d exception: %s", attempt, api_exc)

            # Wait for blockchain sync then verify
            time.sleep(3.0)

            current_shares = _retry_call(get_token_balance, token_id)
            if current_shares is not None:
                if current_shares < 0.1:
                    # Sold (or dust remaining) — SUCCESS
                    usdc_after = _retry_call(get_balance) or 0.0
                    actual_proceeds = round(usdc_after - usdc_before, 2)
                    sold_pct = (1 - current_shares / original_shares) * 100 if original_shares > 0 else 100
                    logger.info("Verified SELL on-chain. %.0f%% sold, proceeds: $%.2f", sold_pct, actual_proceeds)
                    print(f"  [LIVE SELL] {label} | ✅ Sold {sold_pct:.0f}% (proceeds ${actual_proceeds:.2f})")
                    return {
                        "success": True,
                        "remaining_shares": current_shares,
                        "actual_proceeds": actual_proceeds,
                        "token_id": token_id,
                    }
                elif current_shares < shares_to_sell:
                    sold_pct = (1 - current_shares / original_shares) * 100
                    print(f"  [SELL #{attempt}] Partial fill: {sold_pct:.0f}% sold ({current_shares:.4f} remaining)")
                    logger.info("Partial fill: %.4f -> %.4f (%.0f%%)", shares_to_sell, current_shares, sold_pct)
                else:
                    print(f"  [SELL #{attempt}] ❌ No fill (shares unchanged: {current_shares:.4f})")

            time.sleep(2.0)

        print(f"  [SELL FAILED] {label} | 3 attempts failed → waiting for market resolution")
        logger.error("SELL failed for %s after 3 attempts. Will resolve automatically.", label)
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
