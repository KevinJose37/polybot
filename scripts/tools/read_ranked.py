import json
import os

if os.path.exists('profile_wallets_ranked.json'):
    with open('profile_wallets_ranked.json') as f:
        ranked = json.load(f)
    print(f'Found {len(ranked)} ranked wallets.')
    for i, s in enumerate(ranked[:10], start=1):
        p = s['performance']
        print(
            f"{i:>2}. {s['wallet']} | WR {p['win_rate']:.1f}% | "
            f"PF {p['profit_factor']:.2f} | DD {p['max_drawdown_1usd']:.2f} | "
            f"Stab {p['stability_score']:.2f} | PnL {p['pnl_1usd_stake']:+.2f} | "
            f"resolved {p['resolved_trades']}"
        )
else:
    print('profile_wallets_ranked.json not found.')
