"""Quick slug test"""
from sniper_bot.scanner import _slug_for_asset, _fetch_event_by_slug, _compute_market_slots
from sniper_bot.config import SniperConfig
from datetime import datetime, timezone

cfg = SniperConfig()
now = datetime.now(timezone.utc)
slots = _compute_market_slots(now, 5, count=6)

print("UTC:", now.isoformat())
print("Slots:", slots)
print()

for asset in ["BTC", "ETH", "XRP", "SOL"]:
    found = False
    for ts in slots:
        slug = _slug_for_asset(asset, ts, "5m")
        event = _fetch_event_by_slug(slug, cfg.gamma_api_base)
        if event:
            markets = event.get("markets", [])
            if markets:
                m = markets[0]
                q = m.get("question", "?")[:60]
                ao = m.get("acceptingOrders", False)
                cl = m.get("closed", False)
                print(f"  {asset}: FOUND | {q}")
                print(f"    slug: {slug}")
                print(f"    accepting: {ao} | closed: {cl}")
            found = True
            break
    if not found:
        print(f"  {asset}: NOT FOUND with slug pattern")
        # Test alternative patterns
        ts = slots[2]
        alternatives = [
            f"{asset.lower()}-up-or-down-5m-{ts}",
            f"{asset.lower()}-5min-{ts}",
        ]
        for alt in alternatives:
            ev = _fetch_event_by_slug(alt, cfg.gamma_api_base)
            if ev:
                print(f"    FOUND with alt: {alt}")
                break
        else:
            print(f"    No alt patterns worked either")
