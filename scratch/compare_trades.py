import json
import requests
import datetime
import collections

# Load bot trades
try:
    with open(r"d:\Proyectos\polystudio\polystudio\data\trades\copy_89b5cdaa.json", 'r', encoding='utf-8') as f:
        bot_trades = json.load(f)
except Exception as e:
    print(f"Error loading bot trades: {e}")
    exit(1)

bot_resolved = [t for t in bot_trades if t.get('status') in ('won', 'lost', 'sold')]
if not bot_resolved:
    print("No resolved bot trades to compare.")
    exit(0)

# Ohanism full address
ohanism_addr = "0x89b5cdaaa4866c1e738406712012a630b4078beb"

# Fetch ohanism's recent trades
try:
    resp = requests.get(f"https://clob.polymarket.com/data/trades?user={ohanism_addr}&limit=1000", timeout=10)
    resp.raise_for_status()
    ohanism_raw_trades = resp.json()
except Exception as e:
    print(f"Error fetching ohanism trades: {e}")
    exit(1)

ohanism_trades = [t for t in ohanism_raw_trades if t.get('type') == 'TRADE']

print(f"Loaded {len(bot_resolved)} resolved bot trades and {len(ohanism_trades)} recent ohanism raw trades.")

# Match trades by token_id or slug + side
# Note: ohanism might scale in/out with multiple trades for the same asset.
# Let's aggregate ohanism's trades by asset (token_id).

ohanism_by_asset = collections.defaultdict(list)
for t in ohanism_trades:
    asset = t.get('asset')
    if asset:
        ohanism_by_asset[asset].append(t)

# Now iterate over bot's resolved trades and find the corresponding ohanism trades
bot_total_pnl = 0.0
bot_total_invested = 0.0

ohanism_total_pnl = 0.0
ohanism_total_invested = 0.0

matched_count = 0

# We'll normalize to a $10 stake for comparison to make the P&L comparable,
# or calculate the ROI per trade. Let's calculate ROI% per trade.
comparison_results = []

for bot_t in bot_resolved:
    token_id = bot_t.get('token_id')
    slug = bot_t.get('slug', 'unknown')
    
    # Ohanism trades for this token
    o_trades = ohanism_by_asset.get(token_id, [])
    
    # Filter ohanism trades that happened around the same time or before our exit
    # Actually, we can just look at ohanism's average buy price and average sell price for this token
    # in the recent timeframe. 
    # To keep it simple, let's aggregate ohanism's BUYs and SELLs for this token.
    o_buys = [t for t in o_trades if t.get('side', '').upper() == 'BUY']
    o_sells = [t for t in o_trades if t.get('side', '').upper() == 'SELL']
    
    if not o_buys:
        continue
        
    # Calculate ohanism avg entry
    o_total_cost = sum(float(t.get('size', 0)) * float(t.get('price', 0)) for t in o_buys)
    o_total_shares_bought = sum(float(t.get('size', 0)) for t in o_buys)
    o_avg_entry = o_total_cost / o_total_shares_bought if o_total_shares_bought > 0 else 0
    
    # Calculate ohanism avg exit (could be SELL or resolved to 1 or 0)
    o_total_proceeds = sum(float(t.get('size', 0)) * float(t.get('price', 0)) for t in o_sells)
    o_total_shares_sold = sum(float(t.get('size', 0)) for t in o_sells)
    
    # If ohanism hasn't sold all, assume resolution or still holding. 
    # For a fair comparison, let's just look at the ROI%.
    # If they didn't sell, and market resolved, the price is 1 (if won) or 0 (if lost).
    # We can use the bot's resolution status to guess the final price if ohanism held.
    final_price = 1.0 if bot_t.get('status') == 'won' else 0.0
    
    # Unsold shares
    unsold_shares = o_total_shares_bought - o_total_shares_sold
    if unsold_shares > 0:
        o_total_proceeds += unsold_shares * final_price
        
    o_roi = (o_total_proceeds - o_total_cost) / o_total_cost * 100 if o_total_cost > 0 else 0
    
    # Bot's performance
    bot_invested = bot_t.get('stake', 0)
    bot_pnl = bot_t.get('pnl', 0)
    bot_roi = (bot_pnl / bot_invested * 100) if bot_invested > 0 else 0
    
    # Assuming bot invests $10 for normalization
    norm_stake = 10.0
    norm_bot_pnl = norm_stake * (bot_roi / 100)
    norm_o_pnl = norm_stake * (o_roi / 100)
    
    bot_total_pnl += norm_bot_pnl
    ohanism_total_pnl += norm_o_pnl
    matched_count += 1
    
    comparison_results.append({
        'slug': slug,
        'bot_roi': bot_roi,
        'o_roi': o_roi,
        'bot_exit': bot_t.get('exit_reason', 'unknown'),
        'bot_pnl_norm': norm_bot_pnl,
        'o_pnl_norm': norm_o_pnl
    })

print(f"\n--- COMPARISON ON {matched_count} MATCHED TRADES ---")
print(f"Normalized to $10 per trade for fair comparison.\n")

print(f"Bot Total P&L (Norm):     ${bot_total_pnl:+.2f}")
print(f"Ohanism Total P&L (Norm): ${ohanism_total_pnl:+.2f}\n")

if bot_total_pnl > ohanism_total_pnl:
    print("=> The Bot is OUTPERFORMING Ohanism on these specific trades!")
else:
    print("=> Ohanism is OUTPERFORMING the Bot on these specific trades.")

print("\n--- Breakdown by Exit Strategy ---")
# Let's see how our TP vs Ohanism's strategy compares
tp_bot_pnl = sum(r['bot_pnl_norm'] for r in comparison_results if 'TP' in r['bot_exit'])
tp_o_pnl = sum(r['o_pnl_norm'] for r in comparison_results if 'TP' in r['bot_exit'])
tp_count = sum(1 for r in comparison_results if 'TP' in r['bot_exit'])

res_bot_pnl = sum(r['bot_pnl_norm'] for r in comparison_results if 'resolution' in r['bot_exit'].lower())
res_o_pnl = sum(r['o_pnl_norm'] for r in comparison_results if 'resolution' in r['bot_exit'].lower())
res_count = sum(1 for r in comparison_results if 'resolution' in r['bot_exit'].lower())

print(f"When Bot hits Take Profit (TP) [{tp_count} trades]:")
print(f"  Bot P&L:     ${tp_bot_pnl:+.2f}")
print(f"  Ohanism P&L: ${tp_o_pnl:+.2f}")

print(f"\nWhen Bot holds to Resolution [{res_count} trades]:")
print(f"  Bot P&L:     ${res_bot_pnl:+.2f}")
print(f"  Ohanism P&L: ${res_o_pnl:+.2f}")

