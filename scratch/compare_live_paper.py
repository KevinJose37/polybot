import json
from collections import defaultdict

def load_trades(path):
    with open(path, 'r', encoding='utf-8') as f:
        return {t['slug']: t for t in json.load(f)}

live = load_trades('data/trades/copy_89b5cdaa_live.json')
paper = load_trades('data/trades/copy_89b5cdaa_paper.json')

common_slugs = set(live.keys()) & set(paper.keys())

print(f"Total Live trades: {len(live)}")
print(f"Total Paper trades: {len(paper)}")
print(f"Common trades: {len(common_slugs)}")

print("\n--- PRICE COMPARISON (Paper vs Live Entry) ---")
entry_diffs = []
for slug in common_slugs:
    l_t = live[slug]
    p_t = paper[slug]
    
    if l_t['status'] == 'ghost':
        continue # Not executed in live
        
    l_entry = l_t.get('entry_price', 0)
    p_entry = p_t.get('entry_price', 0)
    
    if l_entry > 0 and p_entry > 0:
        diff = l_entry - p_entry
        entry_diffs.append((slug, p_entry, l_entry, diff))

entry_diffs.sort(key=lambda x: x[3], reverse=True)
for slug, p_px, l_px, diff in entry_diffs[:10]:
    print(f"{slug[:30]:<30} | Paper: ${p_px:.4f} | Live: ${l_px:.4f} | Diff: +${diff:.4f}")

print("\n--- TP COMPARISON (Paper vs Live Exit) ---")
tp_diffs = []
for slug in common_slugs:
    l_t = live[slug]
    p_t = paper[slug]
    
    if l_t['status'] == 'sold' and p_t['status'] == 'sold':
        l_exit = l_t.get('exit_price', 0)
        p_exit = p_t.get('exit_price', 0)
        
        if l_exit and p_exit:
            diff = p_exit - l_exit
            tp_diffs.append((slug, p_exit, l_exit, diff))

tp_diffs.sort(key=lambda x: x[3], reverse=True)
for slug, p_px, l_px, diff in tp_diffs[:10]:
    print(f"{slug[:30]:<30} | Paper TP: ${p_px:.4f} | Live TP: ${l_px:.4f} | Paper advantage: +${diff:.4f}")

print("\n--- GHOSTS & FAILED ENTRIES ---")
ghosts = [t for t in live.values() if t['status'] == 'ghost']
print(f"Live failed entries (Ghosts): {len(ghosts)}")
for t in ghosts[:5]:
    print(f"  {t['slug']} - {t.get('exit_reason', 'unknown')}")

print("\n--- PAPER FAKE WINS ---")
# Trades where paper won but live lost or ghosted
for slug in common_slugs:
    if paper[slug].get('status') in ['won', 'sold'] and paper[slug].get('pnl', 0) > 0:
        if live[slug].get('pnl', 0) <= 0:
            print(f"Fake Win: {slug[:30]} | Paper PnL: ${paper[slug].get('pnl'):+.2f} | Live PnL: ${live[slug].get('pnl'):+.2f} (Status: {live[slug].get('status')})")
