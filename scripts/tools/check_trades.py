"""Check trade modes in copy data."""
import json, glob

for f in glob.glob("data/trades/copy_*[!seen].json"):
    with open(f) as fh:
        data = json.load(fh)
    if not data:
        continue
    print(f"\n{f} ({len(data)} trades):")
    for t in data[-15:]:
        mode = t.get("mode", "?")
        status = t.get("status", "?")
        side = t.get("side", "?")
        price = t.get("entry_price", 0)
        stake = t.get("stake", 0)
        q = t.get("question", "?")[:45]
        print(f"  {status:6} | mode={mode:5} | {side:4} @ ${price:.2f} | ${stake:.0f} | {q}")
