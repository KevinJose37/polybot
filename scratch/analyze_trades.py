import json
import collections

trade_file = r"d:\Proyectos\polystudio\polystudio\data\trades\copy_89b5cdaa.json"

try:
    with open(trade_file, 'r', encoding='utf-8') as f:
        trades = json.load(f)
except Exception as e:
    print(f"Error loading {trade_file}: {e}")
    exit(1)

resolved = [t for t in trades if t.get('status') in ('won', 'lost', 'sold')]
open_trades = [t for t in trades if t.get('status') == 'open']

total_trades = len(resolved)
if total_trades == 0:
    print("No resolved trades to analyze.")
    exit(0)

wins = sum(1 for t in resolved if t.get('pnl', 0) > 0)
total_pnl = sum(t.get('pnl', 0) for t in resolved)
overall_wr = (wins / total_trades) * 100

print(f"Total Resolved Trades: {total_trades}")
print(f"Overall Win Rate: {overall_wr:.1f}% ({wins}/{total_trades})")
print(f"Overall P&L: ${total_pnl:.2f}\n")

# Analysis by coin/market (using slug or title words)
# A lot of Polymarket slugs have common prefixes, let's group by slug prefix or just the slug itself if it's broad.
# Actually, the user asked for "monedas" (coins/markets). We can group by slug.
slug_stats = collections.defaultdict(lambda: {'wins': 0, 'total': 0, 'pnl': 0.0})

for t in resolved:
    slug = t.get('slug', 'unknown')
    pnl = t.get('pnl', 0)
    slug_stats[slug]['total'] += 1
    if pnl > 0:
        slug_stats[slug]['wins'] += 1
    slug_stats[slug]['pnl'] += pnl

print("--- Performance by Market (Slug) ---")
sorted_slugs_by_pnl = sorted(slug_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)

print("Top 5 Best Performing Markets (by P&L):")
for slug, stats in sorted_slugs_by_pnl[:5]:
    wr = (stats['wins'] / stats['total']) * 100
    print(f"  - {slug[:40]}: P&L ${stats['pnl']:+.2f} | WR: {wr:.0f}% ({stats['wins']}/{stats['total']})")

print("\nBottom 5 Worst Performing Markets (by P&L):")
for slug, stats in sorted_slugs_by_pnl[-5:]:
    wr = (stats['wins'] / stats['total']) * 100
    print(f"  - {slug[:40]}: P&L ${stats['pnl']:+.2f} | WR: {wr:.0f}% ({stats['wins']}/{stats['total']})")

print("\n--- Performance by Exit Reason ---")
exit_stats = collections.defaultdict(lambda: {'wins': 0, 'total': 0, 'pnl': 0.0})
for t in resolved:
    reason = t.get('exit_reason', 'unknown') or 'unknown'
    # Group reasons
    if 'TP' in reason:
        reason_group = 'Take Profit (TP)'
    elif 'SL' in reason:
        reason_group = 'Stop Loss (SL)'
    elif 'resolution' in reason.lower():
        reason_group = 'Market Resolution'
    else:
        reason_group = 'Other/Manual'
        
    pnl = t.get('pnl', 0)
    exit_stats[reason_group]['total'] += 1
    if pnl > 0:
        exit_stats[reason_group]['wins'] += 1
    exit_stats[reason_group]['pnl'] += pnl

for reason, stats in exit_stats.items():
    wr = (stats['wins'] / stats['total']) * 100
    print(f"  - {reason}: P&L ${stats['pnl']:+.2f} | WR: {wr:.0f}% ({stats['wins']}/{stats['total']})")
