import json
with open("data/trades/copy_89b5cdaa.json") as f:
    data = json.load(f)
# Show first 10 trades with $4 stake
four_dollar = [t for t in data if t.get("stake") == 4.0]
for t in four_dollar[:5]:
    print(f"  ${t['stake']} | {t['side']:4} | {t['slug'][:30]} | shares={t.get('shares',0):.2f} | orig_size=${t.get('original_size',0):.2f}")
print(f"\nTotal $4 trades: {len(four_dollar)}")
print(f"Total $1 trades: {len([t for t in data if t.get('stake') == 1.0])}")
