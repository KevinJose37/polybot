import numpy as np
from scalper.signals import SignalResult

def compute_all_signals_sniper(assets: dict, markets: dict, trigger_price: float = 0.52, max_entry: float = 0.95) -> dict[str, SignalResult]:
    """
    V11 Sniper Signal (Microstructure L2 Engine).
    
    Operates STRICTLY on WebSocket Orderbook data. Gamma REST prices are ignored
    for trading decisions. 
    
    A signal is triggered only if:
      1. WS has valid live data (best_ask > 0).
      2. The best_ask breaks the trigger_price.
      3. Spread is wide enough (Liquidity Vacuum).
      4. Orderbook Imbalance is favorable (more depth supporting the move).
    """
    from scalper.orderbook_ws import get_book_summary, get_imbalance, get_mid_velocity
    signals = {}

    # Microstructure tuning parameters
    MIN_SPREAD_VACUUM = 0.015   # 1.5 cents minimum spread indicates a swept book
    MIN_FAVORABLE_IMB = 1.0     # Imbalance ratio must favor our direction (ask/bid for UP, bid/ask for DOWN)

    for asset_key in assets:
        if asset_key not in markets:
            continue

        market = markets[asset_key]
        up_token_id = market.get("up_token_id", "")
        down_token_id = market.get("down_token_id", "")

        if not up_token_id or not down_token_id:
            continue

        # 1. Fetch strictly live L2 data (None if WS is cold)
        up_book = get_book_summary(up_token_id)
        down_book = get_book_summary(down_token_id)

        direction = "NEUTRAL"
        score = 0.0
        
        # Meta-data for logging (using Gamma as fallback only for UI, NEVER for logic)
        log_up_price = market.get("up_price", 0.5)
        log_down_price = market.get("down_price", 0.5)
        current_price = 0.5

        if up_book and down_book:
            up_ask = up_book["best_ask"]
            up_spread = up_book["spread"]
            
            down_ask = down_book["best_ask"]
            down_spread = down_book["spread"]
            
            # Fetch Imbalance (get_imbalance handles the token correctly)
            up_imb_data = get_imbalance(up_token_id)
            # up_imb_data["up_imbalance"] = ask_depth / bid_depth. 
            # We want bid_depth > ask_depth, so up_imbalance < 1.0 (or we flip the logic)
            # Actually, if we want bid_depth > ask_depth, the ratio (ask/bid) should be < 1.0
            # A "favorable" imbalance for UP means buyers > sellers.
            up_favorable = up_imb_data["up_imbalance"] < MIN_FAVORABLE_IMB
            
            down_imb_data = get_imbalance(down_token_id)
            down_favorable = down_imb_data["up_imbalance"] < MIN_FAVORABLE_IMB # using up_imbalance of the down_token

            # Condition 1: UP side breaks trigger
            if trigger_price <= up_ask <= max_entry:
                if up_spread >= MIN_SPREAD_VACUUM and up_favorable:
                    direction = "UP"
                    score = 1.0
                    current_price = up_ask
                    print(f"  [V11-L2] {asset_key} UP TRIGGER: Ask=${up_ask:.3f} | Spread=${up_spread:.3f} | Imb={up_imb_data['up_imbalance']:.2f}")
                else:
                    print(f"  [V11-L2] {asset_key} UP FILTERED: Ask=${up_ask:.3f} | Spread=${up_spread:.3f} | Imb={up_imb_data['up_imbalance']:.2f}")

            # Condition 2: DOWN side breaks trigger (only if UP didn't)
            elif trigger_price <= down_ask <= max_entry:
                if down_spread >= MIN_SPREAD_VACUUM and down_favorable:
                    direction = "DOWN"
                    score = -1.0
                    current_price = down_ask
                    print(f"  [V11-L2] {asset_key} DOWN TRIGGER: Ask=${down_ask:.3f} | Spread=${down_spread:.3f} | Imb={down_imb_data['up_imbalance']:.2f}")
                else:
                    print(f"  [V11-L2] {asset_key} DOWN FILTERED: Ask=${down_ask:.3f} | Spread=${down_spread:.3f} | Imb={down_imb_data['up_imbalance']:.2f}")
        
        else:
            # WS is cold. STRICT RULE: No WS data = No Trade.
            pass

        signals[asset_key] = SignalResult(
            asset=asset_key,
            direction=direction,
            score=score,
            ema_signal=up_book["best_ask"] if up_book else log_up_price,      
            rsi_signal=down_book["best_ask"] if down_book else log_down_price,
            momentum_signal=up_book["spread"] if up_book else 0.0,
            volume_signal=down_book["spread"] if down_book else 0.0,
            vwap_signal=0.0,
            rsi_value=50.0,
            current_price=current_price,
            ema_fast=0.0,
            ema_slow=0.0,
            confidence="HIGH" if direction != "NEUTRAL" else "LOW",
        )

    return signals
