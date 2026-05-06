"""
scalper/oracle_sniper.py — Post-Expiry Oracle Arbitrage Bot

Waits until exactly `event_end`, checks Binance price, and sweeps Polymarket.
"""
import time
import argparse
import logging
from datetime import datetime, timezone
import os

from scalper.binance_ws import BinanceTickManager
from scalper.market_scanner import scan_active_markets
from scalper.live_client import init_live_client, buy_outcome, _fetch_rest_book
import scalper.config as config

def get_historical_strike_price(event_start_dt, symbol="BTCUSDT"):
    import requests
    ts = int(event_start_dt.timestamp() * 1000)
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&startTime={ts}&limit=1'
    try:
        resp = requests.get(url, timeout=5)
        data = resp.json()
        if data and len(data) > 0:
            return float(data[0][1])  # Index 1 is Open price
    except Exception as e:
        pass
    return 0.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot.oracle_sniper")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stake", type=float, default=1.0, help="Amount to bet in USDC")
    parser.add_argument("--live", action="store_true", help="Enable live trading on mainnet")
    parser.add_argument("--max-price", type=float, default=0.85, help="Maximum price to sweep (e.g. 0.85)")
    parser.add_argument("--pre-seconds", type=float, default=0.0, help="Seconds before expiry to fire (e.g. 5.0)")
    args = parser.parse_args()

    print(f"[*] Starting Oracle Sniper | Stake: ${args.stake} | Max Price: ${args.max_price} | Pre-fire: {args.pre_seconds}s")
    print(f"[*] Mode: {'LIVE' if args.live else 'PAPER (DRY-RUN)'}")
    
    # Initialize live client if live
    if args.live:
        if not init_live_client(dry_run=False):
            print("[X] Live client init failed. Exiting.")
            return
    else:
        init_live_client(dry_run=True)

    # Initialize Binance WS for all HFT assets
    ws_assets = {
        "BTC": "btcusdt",
        "ETH": "ethusdt",
        "SOL": "solusdt",
        "XRP": "xrpusdt"
    }
    bm = BinanceTickManager(assets=ws_assets)
    bm.start()
    
    print("[~] Waiting for Binance WS to warm up...")
    while not bm.is_warm("BTC"):
        time.sleep(0.5)
    print("[+] Binance WS Ready.")

    session_spent = 0.0
    session_won = 0.0
    
    already_fired_gamma_ids = set()
    strike_price_cache = {}

    while True:
        try:
            # We scan for BOTH 5m and 15m markets for all configured assets
            markets_5m = scan_active_markets(assets=config.HFT_ASSETS, duration_minutes=5)
            markets_15m = scan_active_markets(assets=config.HFT_ASSETS, duration_minutes=15)
            
            # Combine
            active_markets = []
            for asset in config.HFT_ASSETS:
                if asset in markets_5m and markets_5m[asset]: active_markets.append(markets_5m[asset])
                if asset in markets_15m and markets_15m[asset]: active_markets.append(markets_15m[asset])

            now = datetime.now(timezone.utc)
            
            for m in active_markets:
                gamma_id = m["gamma_id"]
                if gamma_id in already_fired_gamma_ids:
                    continue

                event_end = m["event_end"]
                strike_price = m.get("strike_price", 0.0)
                
                # Check cache first
                if strike_price <= 0 and gamma_id in strike_price_cache:
                    strike_price = strike_price_cache[gamma_id]
                
                if strike_price <= 0 and "Up or Down" in m['title']:
                    symbol = ws_assets.get(m['asset'], f"{m['asset'].lower()}usdt").upper()
                    strike_price = get_historical_strike_price(m['event_start'], symbol=symbol)
                    if strike_price > 0:
                        strike_price_cache[gamma_id] = strike_price
                        print(f"[*] Fetched dynamic strike price from Binance for {m['asset']}: ${strike_price:.4f}")
                
                if strike_price <= 0:
                    print(f"[!] Could not parse strike price from: {m['title']}")
                    already_fired_gamma_ids.add(gamma_id)
                    continue
                
                time_to_expiry = (event_end - now).total_seconds()

                if time_to_expiry > 10:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m['slug']} | Expiry in {time_to_expiry:.1f}s | Strike: ${strike_price}")
                    continue

                # We are within 10 seconds of expiry! Enter TIGHT LOOP
                print(f"[>] Entering TIGHT LOOP for {m['slug']} | Expiry at {event_end}")
                
                while True:
                    now_tight = datetime.now(timezone.utc)
                    time_to_expiry_tight = (event_end - now_tight).total_seconds()
                    
                    if time_to_expiry_tight <= args.pre_seconds:
                        # IT IS TIME
                        exec_start = time.perf_counter()
                        asset = m["asset"]
                        current_binance = bm.get_current_price(asset)
                        
                        time_label = "EARLY" if args.pre_seconds > 0 else "EXACT"
                        print(f"[*] {time_label} EXPIRY REACHED! Binance {asset}: ${current_binance:.4f} | Strike: ${strike_price:.4f}")
                        
                        # We use a percentage-based safety margin so it works across all assets (BTC=$65k, XRP=$0.50)
                        # Base margin: 0.025% of the strike price (e.g. ~$16 for BTC).
                        # Plus 0.008% per second early (e.g. 5 sec = 0.04% extra margin).
                        safety_margin_pct = 0.00025 + (args.pre_seconds * 0.00008)
                        safety_margin = strike_price * safety_margin_pct
                        
                        diff = current_binance - strike_price
                        if abs(diff) < safety_margin:
                            print(f"[!] Difference too small (${abs(diff):.4f} vs safe ${safety_margin:.4f}). Skipping to avoid last-second swings risk.")
                            already_fired_gamma_ids.add(gamma_id)
                            break
                            
                        # Decide winner
                        if current_binance > strike_price:
                            winning_side = "UP"
                            winning_token = m["up_token_id"]
                        else:
                            winning_side = "DOWN"
                            winning_token = m["down_token_id"]
                            
                        print(f"[+] Oracle prediction: {winning_side} wins!")
                        
                        # Sweep the book
                        # We use buy_outcome which sends a Market FOK order.
                        if args.live:
                            print(f"[$] Sweeping {winning_side} up to ${args.max_price}...")
                            result = buy_outcome(
                                token_id=winning_token,
                                price=args.max_price,  # passed as limit price to avoid absurd slippage
                                size=args.stake,
                                asset=asset,
                                side=winning_side
                            )
                            if result and result.get("success"):
                                cost = result.get("actual_cost", args.stake)
                                shares = result.get("shares", 0.0)
                                session_spent += cost
                                session_won += shares * 1.0  # Winning shares are worth $1.00 at settlement
                        else:
                            print("[?] [DRY-RUN] Checking live orderbook liquidity...")
                            book = _fetch_rest_book(winning_token)
                            if book and book.get("asks"):
                                asks = sorted(book["asks"], key=lambda x: float(x.get("price", 1.0)))
                                best_ask = float(asks[0]["price"])
                                best_ask_size = float(asks[0].get("size", 0.0))
                                
                                print(f"[=] [DRY-RUN] Real Book: {best_ask_size:.1f} shares @ ${best_ask:.4f}")
                                if best_ask <= args.max_price:
                                    cost = min(args.stake, best_ask_size * best_ask)
                                    shares = cost / best_ask
                                    print(f"[+] [DRY-RUN] Order WOULD HAVE FILLED: {shares:.2f} shares @ ${best_ask:.4f} (Cost: ${cost:.2f})")
                                    session_spent += cost
                                    session_won += shares * 1.0
                                else:
                                    print(f"[-] [DRY-RUN] Order WOULD HAVE FAILED: Best ask ${best_ask:.4f} is higher than max_price ${args.max_price:.2f}")
                            else:
                                print("[-] [DRY-RUN] Order WOULD HAVE FAILED: No ASKs available on the book.")
                        
                        exec_latency = (time.perf_counter() - exec_start) * 1000
                        print(f"[⚡] Execution latency (decision + network): {exec_latency:.1f} ms")
                        
                        already_fired_gamma_ids.add(gamma_id)
                        
                        pnl = session_won - session_spent
                        print(f"\n──────────────────────────────────────────────────")
                        print(f"💵 SESSION TRACKER | Spent: ${session_spent:.2f} | Expected Return: ${session_won:.2f} | Net PnL: ${pnl:+.2f}")
                        print(f"──────────────────────────────────────────────────\n")
                        
                        print("[OK] Sniper sequence complete. Cooldown 10s...")
                        time.sleep(10)
                        break
                    
                    # Sleep very little in tight loop
                    time.sleep(0.01)

        except Exception as e:
            logger.error(f"Scanner error: {e}")
            time.sleep(1)
            
        time.sleep(2)

if __name__ == "__main__":
    main()
