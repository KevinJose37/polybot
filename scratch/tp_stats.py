import json
try:
    with open('data/trades/copy_89b5cdaa.json', encoding='utf-8') as f:
        t = [x for x in json.load(f) if 'TP' in (x.get('exit_reason') or '')]
    avg_entry = sum(x["entry_price"] for x in t) / len(t)
    avg_exit = sum(x["exit_price"] for x in t) / len(t)
    print(f"Avg entry: {avg_entry:.2f}, Avg Exit: {avg_exit:.2f}")
except Exception as e:
    print(e)
