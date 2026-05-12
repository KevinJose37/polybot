import json
import collections
import re

# Load bot trades
try:
    with open(r"d:\Proyectos\polystudio\polystudio\data\trades\copy_89b5cdaa.json", 'r', encoding='utf-8') as f:
        bot_trades = json.load(f)
except Exception as e:
    print(f"Error loading bot trades: {e}")
    exit(1)

resolved = [t for t in bot_trades if t.get('status') in ('won', 'lost', 'sold')]
if not resolved:
    print("No resolved bot trades to compare.")
    exit(0)

# Groupings
coin_stats = collections.defaultdict(lambda: {'wins': 0, 'total': 0, 'pnl': 0.0})
timeframe_stats = collections.defaultdict(lambda: {'wins': 0, 'total': 0, 'pnl': 0.0})
detailed_stats = collections.defaultdict(lambda: {'wins': 0, 'total': 0, 'pnl': 0.0})

def get_coin(slug):
    slug_lower = slug.lower()
    if 'btc' in slug_lower or 'bitcoin' in slug_lower: return 'BTC'
    if 'eth' in slug_lower or 'ethereum' in slug_lower: return 'ETH'
    if 'sol' in slug_lower or 'solana' in slug_lower: return 'SOL'
    if 'xrp' in slug_lower: return 'XRP'
    if 'doge' in slug_lower: return 'DOGE'
    if 'bnb' in slug_lower: return 'BNB'
    return 'Other'

def get_timeframe(slug, title):
    text = (slug + " " + title).lower()
    if '5m' in text or '5-min' in text or '5 min' in text: return '5m'
    if '15m' in text or '15-min' in text or '15 min' in text: return '15m'
    if '30m' in text or '30-min' in text: return '30m'
    if '1h' in text or '1-hour' in text or 'hour' in text: return '1h+'
    if 'et' in text and ('pm' in text or 'am' in text):
        # Look for range like 9:45PM-9:50PM
        if re.search(r'\d+:\d+[ap]m-\d+:\d+[ap]m', text):
            return '5m/15m Range'
        return 'Daily/Fixed Time'
    return 'Unknown'

for t in resolved:
    slug = t.get('slug', '')
    title = t.get('question', slug)
    pnl = t.get('pnl', 0)
    is_win = pnl > 0
    
    coin = get_coin(slug)
    tf = get_timeframe(slug, title)
    combo = f"{coin} ({tf})"
    
    # Update Coin Stats
    coin_stats[coin]['total'] += 1
    if is_win: coin_stats[coin]['wins'] += 1
    coin_stats[coin]['pnl'] += pnl
    
    # Update TF Stats
    timeframe_stats[tf]['total'] += 1
    if is_win: timeframe_stats[tf]['wins'] += 1
    timeframe_stats[tf]['pnl'] += pnl
    
    # Update Detailed Stats
    detailed_stats[combo]['total'] += 1
    if is_win: detailed_stats[combo]['wins'] += 1
    detailed_stats[combo]['pnl'] += pnl

def print_stats(stats_dict, title):
    print(f"\n=== {title} ===")
    sorted_stats = sorted(stats_dict.items(), key=lambda x: x[1]['pnl'], reverse=True)
    for key, stats in sorted_stats:
        wr = (stats['wins'] / stats['total']) * 100 if stats['total'] > 0 else 0
        print(f"  {key:<15} | P&L: ${stats['pnl']:+6.2f} | WR: {wr:5.1f}% ({stats['wins']}/{stats['total']})")

print(f"Total Resolved Trades Analyzed: {len(resolved)}")
print(f"Total P&L: ${sum(t.get('pnl', 0) for t in resolved):.2f}")

print_stats(coin_stats, "Rendimiento por Moneda")
print_stats(timeframe_stats, "Rendimiento por Temporalidad")
print_stats(detailed_stats, "Desglose Detallado (Moneda + Temporalidad)")
