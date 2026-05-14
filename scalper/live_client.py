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
import json
from datetime import datetime, timezone

import requests as _requests

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
        return {"dry_run": True, "action": "BUY", "token_id": token_id}

    try:
        from py_clob_client_v2.clob_types import MarketOrderArgs, OrderType
        from py_clob_client_v2.order_builder.builder import BUY

        # ── Duplicate entry guard (non-blocking) ────────────────────
        try:
            existing_shares = _retry_call(get_token_balance, token_id)
            if existing_shares is not None and existing_shares > 0.1:
                logger.warning("Already holding %.2f shares of %s. Skipping BUY.", existing_shares, label)
                return {"success": True, "shares": existing_shares, "token_id": token_id, "already_held": True}
        except Exception:
            logger.debug("Pre-check balance failed, proceeding with order")

        # ── Snapshot USDC balance BEFORE ────────────────────────────
        usdc_before = _retry_call(get_balance) or 0.0

        order_amount = round(max(size, MIN_ORDER_AMOUNT), 2)
        # Use a limit price with aggressive slippage (observed price + $0.10) to ensure fill.
        limit_price = round(min(price + 0.10, 0.99), 4)

        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=order_amount,
            side=BUY,
            price=limit_price,
            order_type=OrderType.FAK,
        )

        logger.info("Sending MARKET BUY: %s | $%.2f", label, order_amount)
        signed = _client.create_market_order(market_order)

        # NOTE: Do NOT retry post_order — a 401 may mask a successful fill.
        try:
            _t0 = time.perf_counter()
            resp = _client.post_order(signed, OrderType.FAK)
            _elapsed_ms = (time.perf_counter() - _t0) * 1000
            try:
                from scalper.latency import record_order_exec
                record_order_exec(_elapsed_ms)
            except ImportError:
                pass
            logger.info("BUY post_order roundtrip: %.0fms", _elapsed_ms)
            err = resp.get("error_message") if isinstance(resp, dict) else None
            if err:
                logger.warning("BUY order API returned error: %s", err)
        except Exception as api_exc:
            logger.warning("BUY order POST exception: %s (checking on-chain...)", api_exc)
            resp = None

        # ── Verify on-chain: shares received + USDC spent ──────────
        # Fast verification: 1 attempt with short wait
        # If API returned success, the fill is almost guaranteed
        time.sleep(2.0)
        total_shares_now = _retry_call(get_token_balance, token_id)
        usdc_after = _retry_call(get_balance) or 0.0

        if total_shares_now is not None and total_shares_now > 0:
            # Only count NEW shares acquired in this transaction
            base_shares = existing_shares if existing_shares is not None else 0.0
            new_shares = max(0.0, total_shares_now - base_shares)
            
            actual_cost = round(usdc_before - usdc_after, 2)
            actual_entry_price = round(actual_cost / new_shares, 4) if new_shares > 0 and actual_cost > 0 else price
            logger.info(
                "Verified BUY on-chain: %.4f new shares | Cost $%.2f | Avg price $%.4f",
                new_shares, actual_cost, actual_entry_price,
            )
            return {
                "success": True,
                "shares": new_shares,
                "actual_cost": actual_cost,
                "actual_entry_price": actual_entry_price,
                "token_id": token_id,
                "resp": resp,
            }

        # Fallback: check USDC delta (token balance API may lag)
        usdc_spent = round(usdc_before - usdc_after, 2)
        if usdc_spent >= size * 0.5:
            logger.warning(
                "BUY for %s: USDC spent $%.2f but no token balance yet. "
                "Assuming fill to avoid orphaned position.", label, usdc_spent,
            )
            estimated_shares = round(usdc_spent / price, 4)
            return {
                "success": True,
                "shares": estimated_shares,
                "actual_cost": usdc_spent,
                "actual_entry_price": price,
                "token_id": token_id,
                "resp": resp,
                "usdc_verified": True,
            }

        # If API returned a successful response but no on-chain change,
        # trust the API and estimate fill
        if resp and isinstance(resp, dict) and not resp.get("error_message"):
            estimated_shares = round(size / price, 4)
            logger.info("BUY API success but no on-chain data yet, trusting API response for %s", label)
            return {
                "success": True,
                "shares": estimated_shares,
                "actual_cost": size,
                "actual_entry_price": price,
                "token_id": token_id,
                "resp": resp,
                "api_trusted": True,
            }

        logger.error("BUY order failed and no on-chain balance found for %s", label)
        return None

    except Exception as exc:
        logger.error("BUY order failed for %s: %s", label, exc)
        return None


# ═══════════════════════════════════════════════════════════════
# REST Orderbook Helpers (public endpoint, no auth needed)
# ═══════════════════════════════════════════════════════════════

_CLOB_BOOK_URL = "https://clob.polymarket.com/book"


def _fetch_rest_book(token_id: str) -> dict | None:
    """
    Fetch full orderbook from CLOB REST endpoint.
    Public endpoint — no authentication required.
    ~800ms latency from South America, ~100ms from US East Coast.
    """
    try:
        resp = _requests.get(
            _CLOB_BOOK_URL,
            params={"token_id": token_id},
            timeout=3,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("REST book fetch failed for %s: %s", token_id[:16], exc)
        return None


def _get_best_bid_rest(token_id: str) -> float | None:
    """Get best bid price from CLOB REST. Fallback when WS is stale."""
    data = _fetch_rest_book(token_id)
    if not data:
        return None
    bids = data.get("bids", [])
    if not bids:
        return None
    try:
        sorted_bids = sorted(bids, key=lambda b: float(b.get("price", 0)), reverse=True)
        best = float(sorted_bids[0]["price"])
        return best if best > 0 else None
    except (ValueError, IndexError, KeyError):
        return None


def _get_best_ask_rest(token_id: str) -> float | None:
    """Get best ask price from CLOB REST. Fallback when WS is stale."""
    data = _fetch_rest_book(token_id)
    if not data:
        return None
    asks = data.get("asks", [])
    if not asks:
        return None
    try:
        sorted_asks = sorted(asks, key=lambda a: float(a.get("price", 0)))
        best = float(sorted_asks[0]["price"])
        return best if best > 0 else None
    except (ValueError, IndexError, KeyError):
        return None


def check_entry_conditions(token_id: str, max_spread: float = 0.03, asset: str = "", side: str = "") -> dict:
    """
    REST snapshot before every BUY to verify the book is tradeable.

    Returns dict with:
      - can_enter: bool
      - spread: float (bid-ask spread)
      - mid_price: float (mid of bid/ask)
      - best_ask: float (real price you'd pay to buy)
      - best_bid: float
      - reason: str
    """
    def _log_result(res: dict) -> dict:
        if not asset or not side:
            return res
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "asset": asset,
            "side": side,
            "token_id": token_id,
            "can_enter": res.get("can_enter", False),
            "reason": res.get("reason", ""),
            "best_bid": res.get("best_bid"),
            "best_ask": res.get("best_ask"),
            "spread": res.get("spread"),
            "rr_ratio": res.get("rr_ratio"),
        }
        try:
            with open("rest_checks.jsonl", "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.error("Failed to write to rest_checks.jsonl: %s", e)
        return res

    data = _fetch_rest_book(token_id)
    if not data:
        return _log_result({"can_enter": False, "reason": "REST book fetch failed"})

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    if not bids or not asks:
        return _log_result({"can_enter": False, "reason": "No bilateral book (missing bids or asks)"})

    try:
        sorted_bids = sorted(bids, key=lambda b: float(b.get("price", 0)), reverse=True)
        sorted_asks = sorted(asks, key=lambda a: float(a.get("price", 0)))
        best_bid = float(sorted_bids[0]["price"])
        best_ask = float(sorted_asks[0]["price"])
        best_bid_sz = float(sorted_bids[0].get("size", 0))
        best_ask_sz = float(sorted_asks[0].get("size", 0))
    except (ValueError, IndexError, KeyError):
        return _log_result({"can_enter": False, "reason": "Failed to parse book data"})

    if best_bid <= 0.01 or best_ask >= 0.99:
        return _log_result({"can_enter": False, "reason": "Book is one-sided (resolved)"})

    spread = round(best_ask - best_bid, 4)
    mid = round((best_bid + best_ask) / 2, 4)

    if spread > max_spread:
        return _log_result({
            "can_enter": False,
            "reason": f"Spread ${spread:.4f} > max ${max_spread:.4f}",
            "spread": spread,
            "mid_price": mid,
            "best_ask": best_ask,
            "best_bid": best_bid,
        })

    if best_ask_sz < 2.0:
        return _log_result({
            "can_enter": False,
            "reason": f"Ask size too thin ({best_ask_sz:.1f} shares)",
            "spread": spread,
            "mid_price": mid,
            "best_ask": best_ask,
            "best_bid": best_bid,
        })

    # Risk/reward calculation (for logging only, filter removed for V11)
    potential_win = 1.0 - best_ask
    potential_loss = best_ask
    rr_ratio = round(potential_loss / potential_win, 2) if potential_win > 0 else 99.0

    return _log_result({
        "can_enter": True,
        "spread": spread,
        "mid_price": mid,
        "best_ask": best_ask,
        "best_bid": best_bid,
        "best_ask_sz": best_ask_sz,
        "best_bid_sz": best_bid_sz,
        "rr_ratio": rr_ratio,
        "reason": f"OK spread=${spread:.4f} ask=${best_ask:.4f} R/R={rr_ratio:.1f}x (win ${potential_win:.2f} / lose ${potential_loss:.2f})",
    })


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
        return {"dry_run": True, "action": "SELL", "token_id": token_id}

    try:
        from py_clob_client_v2.clob_types import MarketOrderArgs, OrderType
        from py_clob_client_v2.order_builder.builder import SELL

        # ── Pre-sell diagnostics ─────────────────────────────────
        usdc_before = _retry_call(get_balance) or 0.0
        actual_shares = _retry_call(get_token_balance, token_id)

        # Retrieve full book summary for diagnostics & liquidity gate
        book = None
        try:
            from scalper.orderbook_ws import get_book_summary
            book = get_book_summary(token_id)
        except ImportError:
            pass


        if book:
            pass
        else:
            pass

        # ── Determine best_bid: WS fresh > REST fallback ─────────
        best_bid = (book["best_bid"] if book and book["best_bid"] > 0.01 else None)
        if not best_bid:
            rest_bid = _get_best_bid_rest(token_id)
            if rest_bid and rest_bid > 0.01:
                best_bid = rest_bid
            else:
                pass

        # ── Calculate limit_price from REAL best_bid ──────────────
        if best_bid and best_bid > 0.01:
            # Sell up to 3 cents below the lower of best_bid or requested price
            base_price = min(best_bid, price) if price > 0.01 else best_bid
            limit_price = max(round(base_price - 0.03, 4), 0.01)
        elif price > 0.01:
            limit_price = max(round(price - 0.03, 4), 0.01)
        else:
            limit_price = None

        # Check conditional token allowance
        try:
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
            bal_allow = _client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            )
            cond_balance = float(bal_allow.get("balance", 0)) / 1e6  # USDC has 6 decimals
            logger.info("SELL DIAG: CLOB conditional balance=%s allowances=%s",
                        bal_allow.get("balance"), bal_allow.get("allowances"))
        except Exception as diag_exc:
            pass

        if actual_shares is not None and actual_shares < 0.01:
            logger.info("Shares already gone (balance: %.6f).", actual_shares)
            usdc_after = _retry_call(get_balance) or 0.0
            actual_proceeds = round(usdc_after - usdc_before, 2)
            return {
                "success": True,
                "remaining_shares": actual_shares,
                "actual_proceeds": actual_proceeds,
                "token_id": token_id,
            }

        # ── Pre-sell liquidity gate (handles WS stale + REST) ────
        if limit_price is None:
            logger.info("SELL skipped for %s: no liquidity from WS or REST", label)
            return None

        # ── Sell Retry Loop ──────────────────────────────────────
        shares_to_sell = (actual_shares if actual_shares is not None else size) * _SELL_SIZE_FACTOR
        original_shares = actual_shares if actual_shares is not None else size

        # Reduced from 3 retries to 2 since liquidity is pre-verified
        for attempt in range(1, 3):
            # Re-check balance before each retry (shares may have changed)
            if attempt > 1:
                current_bal = _retry_call(get_token_balance, token_id)
                if current_bal is not None:
                    if current_bal < 0.1:
                        # Dust remaining — treat as success
                        usdc_after = _retry_call(get_balance) or 0.0
                        actual_proceeds = round(usdc_after - usdc_before, 2)
                        sold_pct = (1 - current_bal / original_shares) * 100 if original_shares > 0 else 100
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
                price=limit_price,
                order_type=OrderType.FAK,
            )

            try:
                signed = _client.create_market_order(market_order)
                _t0 = time.perf_counter()
                resp = _client.post_order(signed, OrderType.FAK)
                _elapsed_ms = (time.perf_counter() - _t0) * 1000
                try:
                    from scalper.latency import record_order_exec
                    record_order_exec(_elapsed_ms)
                except ImportError:
                    pass
                logger.info("SELL post_order roundtrip: %.0fms", _elapsed_ms)

                # Log full response for diagnostics
                logger.info("SELL attempt %d response: %s", attempt, resp)

                if isinstance(resp, dict):
                    status = resp.get("status", "unknown")
                    err = resp.get("error_message") or resp.get("errorMsg")
                    order_id = resp.get("orderID") or resp.get("id", "?")


                    if err:
                        err_lower = str(err).lower()
                        if "not enough balance" in err_lower:
                            logger.warning("SELL %d: balance error: %s", attempt, err)
                        elif "no match" in err_lower or "not filled" in err_lower:
                            logger.warning("SELL %d: no liquidity: %s", attempt, err)
                        else:
                            logger.warning("SELL %d: API error: %s", attempt, err)
                    elif status == "matched":
                        pass
                else:
                    logger.info("SELL attempt %d non-dict response: %s", attempt, type(resp))

            except Exception as api_exc:
                err_str = str(api_exc).lower()
                if "no match" in err_str or "not filled" in err_str:
                    pass
                elif "not enough balance" in err_str:
                    pass
                else:
                    pass
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
                    return {
                        "success": True,
                        "remaining_shares": current_shares,
                        "actual_proceeds": actual_proceeds,
                        "token_id": token_id,
                    }
                elif current_shares < shares_to_sell:
                    sold_pct = (1 - current_shares / original_shares) * 100
                    logger.info("Partial fill: %.4f -> %.4f (%.0f%%)", shares_to_sell, current_shares, sold_pct)
                else:
                    pass

            time.sleep(2.0)

        logger.error("SELL failed for %s after 2 attempts. Will resolve automatically.", label)
        return None

    except Exception as exc:
        logger.error("SELL order failed for %s: %s", label, exc)
        return None


def place_maker_limit_sell(token_id: str, shares: float, limit_price: float) -> str | None:
    """
    Places a GTC (Maker) limit order to sell shares at a specific target price.
    Returns the order ID if successful, or None if failed.
    """
    global _client
    if not _client:
        return None

    try:
        from py_clob_client_v2.constants import SELL
        from py_clob_client_v2.clob_types import OrderArgs, OrderType

        order_args = OrderArgs(
            token_id=token_id,
            price=round(limit_price, 2),
            size=round(shares, 2),
            side=SELL
        )

        signed = _client.create_order(order_args)
        resp = _client.post_order(signed, OrderType.GTC)

        logger.info("MAKER SELL posted for %.2f shares @ $%.4f (resp: %s)", shares, limit_price, resp)

        if isinstance(resp, dict):
            status = resp.get("status", "unknown")
            order_id = resp.get("orderID") or resp.get("id")
            err = resp.get("error_message") or resp.get("errorMsg")

            if err:
                logger.error("Failed to post maker order: %s", err)
                return None
            
            if order_id:
                return str(order_id)
            
        return None
    except Exception as e:
        logger.error("Exception placing maker limit sell: %s", e)
        return None

def get_maker_order_status(order_id: str) -> dict | None:
    """
    Checks the status of a specific order ID.
    Returns dict with {"status": "open" | "matched" | "canceled", "size_matched": float}
    """
    global _client
    if not _client:
        return None
    try:
        resp = _client.get_order(order_id)
        if isinstance(resp, list) and len(resp) > 0:
            order_data = resp[0]
            status = order_data.get("status", "")
            size_matched = float(order_data.get("sizeMatched", 0.0))
            return {"status": status, "size_matched": size_matched, "raw": order_data}
        return None
    except Exception as e:
        logger.error("Failed to fetch order status for %s: %s", order_id, e)
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
