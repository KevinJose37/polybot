"""Analyze V5 historical consistency across Day vs Night."""
import json
import os
import glob
from datetime import datetime
from collections import defaultdict

def analyze_v5_history():
    files = glob.glob('archive/**/*v5*.json', recursive=True)
    files.extend(glob.glob('backup_trades_*/**/*v5*.json', recursive=True))
    
    print(f"Found {len(files)} V5 trade files.")
    
    stats_by_period = defaultdict(lambda: {
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl': 0.0,
        'assets': defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0})
    })
    
    total_trades = 0
    
    for fpath in files:
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                trades = json.load(f)
                if not isinstance(trades, list):
                    continue
                    
                for t in trades:
                    status = t.get('status')
                    if status not in ['won', 'lost']:
                        continue # V5 is hold-to-resolution, so we care about won/lost. Or if there are 'sold' in older versions, we count them.
                        
                    entry_time_str = t.get('entry_time')
                    if not entry_time_str:
                        continue
                        
                    # Parse time (assumes UTC, but we just need a rough categorization)
                    # The timestamps are like '2026-05-07T05:07:00Z'
                    dt = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
                    
                    # Let's use local time logic (UTC-5 roughly based on logs). 
                    # Actually, the user's local time is UTC-5. Let's adjust to local time for day/night.
                    local_hour = (dt.hour - 5) % 24
                    
                    if 6 <= local_hour < 18:
                        period = "Day (6AM - 6PM)"
                    else:
                        period = "Night (6PM - 6AM)"
                        
                    pnl = t.get('pnl', 0) or 0
                    asset = t.get('asset', '?')
                    
                    p_stats = stats_by_period[period]
                    p_stats['total_trades'] += 1
                    p_stats['total_pnl'] += pnl
                    
                    a_stats = p_stats['assets'][asset]
                    a_stats['pnl'] += pnl
                    
                    if pnl > 0:
                        p_stats['wins'] += 1
                        a_stats['wins'] += 1
                    else:
                        p_stats['losses'] += 1
                        a_stats['losses'] += 1
                        
                    total_trades += 1
        except Exception as e:
            pass

    print("=" * 60)
    print(f"V5 Historical Consistency Analysis (Total Trades: {total_trades})")
    print("=" * 60)
    
    for period, s in stats_by_period.items():
        if s['total_trades'] == 0:
            continue
            
        wr = s['wins'] / s['total_trades'] * 100
        print(f"[{period}]")
        print(f"  Total Trades: {s['total_trades']}")
        print(f"  Total PnL:    ${s['total_pnl']:+.2f}")
        print(f"  Win Rate:     {wr:.1f}% ({s['wins']}W / {s['losses']}L)")
        print("  By Asset:")
        for asset, a_stats in s['assets'].items():
            a_total = a_stats['wins'] + a_stats['losses']
            a_wr = a_stats['wins'] / a_total * 100 if a_total > 0 else 0
            print(f"    {asset:<5}: PnL ${a_stats['pnl']:>+6.2f} | WR {a_wr:.0f}% ({a_stats['wins']}W / {a_stats['losses']}L)")
        print("-" * 60)

    # Also aggregate all files that might not have 'v5' in the name but where strategy was V5?
    # Actually, V5 trades are stored in hft_trades_v5.json, so the glob should catch them.

if __name__ == '__main__':
    analyze_v5_history()
