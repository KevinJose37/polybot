"""
scalper/signals_v12_event.py — Event-Driven Microstructure Engine

This engine evaluates the orderbook tick-by-tick (nanosecond latency).
It is invoked by a WebSocket callback every time the orderbook changes.
"""

import time
import logging
from scalper.signals import SignalResult

logger = logging.getLogger(__name__)

# Track recent snipes to avoid double-firing on the same asset in a short window
_recent_snipes = {}

def evaluate_tick(
    token_id: str, 
    book: dict, 
    markets: dict, 
    assets: list[str],
    profile
) -> tuple[str, dict, SignalResult] | None:
    """
    Evaluate a single orderbook tick for V12 Event-Driven Sniper.
    Returns a SignalResult if a SNIPE condition is met, otherwise None.
    """
    from scalper.orderbook_ws import get_ask_velocity, get_imbalance

    now = time.time()
    
    # 1. Reverse-lookup the asset and direction from the token_id
    asset_key = None
    direction = None
    market_data = None
    
    for a in assets:
        if a in markets:
            m = markets[a]
            if m.get("up_token_id") == token_id:
                asset_key = a
                direction = "UP"
                market_data = m
                break
            elif m.get("down_token_id") == token_id:
                asset_key = a
                direction = "DOWN"
                market_data = m
                break
                
    if not asset_key:
        return None
        
    # Anti-spam: Only 1 snipe per asset per 120 seconds
    last_snipe = _recent_snipes.get(asset_key, 0)
    if now - last_snipe < 120:
        return None

    # 2. Extract best bid/ask
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    
    if not bids or not asks:
        return None
        
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    spread = round(best_ask - best_bid, 4)

    # 3. Check trigger conditions (Microstructure V12)
    MIN_SPREAD_VACUUM = 0.015
    MIN_FAVORABLE_IMB = 1.0     
    TOXIC_VELOCITY_THRESHOLD = 0.04  # If ask moved > 4 cents in 500ms, it's toxic/exhausted

    # Only evaluate if price crosses our sniper threshold (0.505)
    if best_ask < profile.sniper_trigger_price:
        return None
        
    # 4. Check Ask Velocity (Toxicity Filter)
    # We measure how fast the ask moved in the last 500ms
    ask_vel_500ms = get_ask_velocity(token_id, window_ms=500)
    
    if ask_vel_500ms >= TOXIC_VELOCITY_THRESHOLD:
        # The move is exhausted, we arrived too late.
        # Log it for analysis but DO NOT snipe.
        if now - getattr(evaluate_tick, "_last_log", 0) > 1:
            print(f"  [V12-SKIP] {asset_key} {direction}: Toxic Velocity ({ask_vel_500ms:+.3f} in 500ms). Ask={best_ask:.2f}")
            evaluate_tick._last_log = now
        return None

    # 5. Check Liquidity Vacuum (Spread expansion)
    if spread < MIN_SPREAD_VACUUM:
        return None

    # 6. Check Imbalance (Bid support)
    imb_data = get_imbalance(token_id)
    # We want strong bid support for the token we are buying
    # If up_imbalance (ask/bid ratio) is < 1.0, it means bid depth > ask depth
    if imb_data["up_imbalance"] >= MIN_FAVORABLE_IMB:
        return None

    # 7. Check Price Cap
    if getattr(profile, "poly_price_filter", False):
        if best_ask > profile.poly_price_cap:
            return None

    # --- SNIPE CONDITION MET ---
    # We found a healthy breakout!
    _recent_snipes[asset_key] = now
    
    print(f"\n  🎯 [V12-SNIPE] {asset_key} {direction} at {best_ask:.2f}!")
    print(f"      Spread: {spread:.3f} | Vel 500ms: {ask_vel_500ms:+.3f} | Imb: {imb_data['up_imbalance']:.2f}")
    
    return (
        asset_key,
        market_data,
        SignalResult(
            direction=direction,
            score=1.0,
            price=best_ask,
            details={
                "spread": spread,
                "ask_vel": ask_vel_500ms,
                "imbalance": imb_data["up_imbalance"]
            }
        )
    )
