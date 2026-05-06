import json
import os

path = r"c:\Users\USER\Documents\Estudio\polystudio\archive\hft_trades.json"

with open(path, "r") as f:
    trades = json.load(f)

closed = [t for t in trades if t.get("status") in ("won", "lost", "sold")]

# 1. P&L as it actually happened, by asset
actual_pnl = {"BTC": 0, "ETH": 0, "SOL": 0, "XRP": 0}
for t in closed:
    asset = t.get("asset")
    if asset in actual_pnl:
        actual_pnl[asset] += t.get("pnl", 0)

# 2. What if we held EVERYTHING to resolution? (Calculate hold P&L per asset)
hold_pnl = {"BTC": 0, "ETH": 0, "SOL": 0, "XRP": 0}

for t in closed:
    asset = t.get("asset")
    if asset not in hold_pnl:
        continue
    
    status = t.get("status")
    shares = t.get("shares", 0)
    entry_price = t.get("entry_price", 0)
    
    # If the trade resolved naturally (won/lost), the hold P&L is the actual P&L
    if status in ("won", "lost"):
        hold_pnl[asset] += t.get("pnl", 0)
    elif status == "sold":
        # If it was sold, we use the hindsight data to see what WOULD have happened
        hindsight = t.get("hindsight")
        if hindsight:
            held_pnl = hindsight.get("held_pnl", 0)
            hold_pnl[asset] += held_pnl
        else:
            # If no hindsight is available, we assume the actual P&L (fallback)
            hold_pnl[asset] += t.get("pnl", 0)

print("=== V1 ASSET PERFORMANCE ===")
for asset in ["BTC", "ETH", "SOL", "XRP"]:
    print(f"{asset}: Actual P&L = ${actual_pnl[asset]:+.2f} | Hold P&L = ${hold_pnl[asset]:+.2f}")

# Calculate optimal scenario: Only trade winning assets (BTC & ETH), and HOLD them.
optimal_pnl = hold_pnl["BTC"] + hold_pnl["ETH"] + hold_pnl["XRP"] # let's see which are positive first!
