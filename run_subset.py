from profile_wallets import profile_wallet, REPORT_FILE, RANKED_FILE
import json

with open('profile_wallets_subset.json') as f:
    wallets = json.load(f)

summaries = []
slug_cache = {}
for w in wallets:
    s = profile_wallet(w, limit=100, slug_cache=slug_cache, max_resolve_trades=50)
    if s:
        summaries.append(s)

ranked = sorted(
    summaries,
    key=lambda s: s.get("performance", {}).get("stability_score", -9999),
    reverse=True,
)

print('\n=== TOP 5 WALLETS (SUBSET FAST RUN) ===')
for i, s in enumerate(ranked[:5], start=1):
    p = s["performance"]
    print(
        f"{i:>2}. {s['wallet']} | WR {p['win_rate']:.1f}% | "
        f"PF {p['profit_factor']:.2f} | DD {p['max_drawdown_1usd']:.2f} | "
        f"Stab {p['stability_score']:.2f} | PnL {p['pnl_1usd_stake']:+.2f} | "
        f"resolved {p['resolved_trades']} | {s['diagnostic']}"
    )
