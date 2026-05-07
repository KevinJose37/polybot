"""Analyze SOL vs XRP across all archived trade JSONs."""
import json
import os
import glob
from collections import defaultdict

def analyze_assets():
    files = glob.glob('archive/**/*.json', recursive=True)
    files.extend(glob.glob('backup_trades_*/**/*.json', recursive=True))
    
    # asset -> { total_pnl, wins, losses, sold, total_trades, held_pnl, held_wins, held_losses }
    stats = defaultdict(lambda: {
        'total_pnl': 0.0,
        'wins': 0,
        'losses': 0,
        'sold': 0,
        'total_trades': 0,
        'held_pnl': 0.0,
        'held_wins': 0,
        'held_losses': 0,
    })
    
    total_files_read = 0
    total_trades_read = 0
    
    for fpath in files:
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                trades = json.load(f)
                if not isinstance(trades, list):
                    continue
                    
                total_files_read += 1
                total_trades_read += len(trades)
                
                for t in trades:
                    asset = t.get('asset')
                    if asset not in ['SOL', 'XRP']:
                        continue
                        
                    status = t.get('status')
                    if status not in ['won', 'lost', 'sold']:
                        continue
                        
                    s = stats[asset]
                    s['total_trades'] += 1
                    
                    pnl = t.get('pnl', 0) or 0
                    s['total_pnl'] += pnl
                    
                    if status == 'won':
                        s['wins'] += 1
                    elif status == 'lost':
                        s['losses'] += 1
                    elif status == 'sold':
                        s['sold'] += 1
                        
                    # Hindsight calculations
                    hs = t.get('hindsight', {})
                    if status == 'sold' and hs:
                        s['held_pnl'] += hs.get('held_pnl', 0) or 0
                        if hs.get('would_have_won'):
                            s['held_wins'] += 1
                        else:
                            s['held_losses'] += 1
                    else:
                        # If it wasn't sold, held_pnl is just actual pnl
                        s['held_pnl'] += pnl
                        if status == 'won':
                            s['held_wins'] += 1
                        elif status == 'lost':
                            s['held_losses'] += 1

        except Exception as e:
            pass

    print(f"Scanned {total_files_read} files, found {total_trades_read} total trades.")
    print("=" * 60)
    print("SOL vs XRP Comparison (All historical data)")
    print("=" * 60)
    
    for asset in ['SOL', 'XRP']:
        s = stats[asset]
        if s['total_trades'] == 0:
            print(f"{asset}: No data found.")
            continue
            
        print(f"[{asset}]")
        print(f"  Total Trades:  {s['total_trades']}")
        print(f"  Actual PnL:    ${s['total_pnl']:+.2f}")
        
        # Win rate (actual)
        actual_resolved = s['wins'] + s['losses']
        if actual_resolved > 0:
            wr = s['wins'] / actual_resolved * 100
            print(f"  Win Rate(res): {wr:.1f}% ({s['wins']}W / {s['losses']}L)")
        
        print(f"  Sold early:    {s['sold']} trades")
        
        # Held PnL
        print(f"  Held PnL:      ${s['held_pnl']:+.2f}")
        held_resolved = s['held_wins'] + s['held_losses']
        if held_resolved > 0:
            h_wr = s['held_wins'] / held_resolved * 100
            print(f"  Held Win Rate: {h_wr:.1f}% ({s['held_wins']}W / {s['held_losses']}L)")
            
        print("-" * 60)

if __name__ == '__main__':
    analyze_assets()
