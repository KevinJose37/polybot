import json

with open('profile_wallets_report.json') as f:
    summaries = json.load(f)

ranked = sorted(
    summaries,
    key=lambda s: s.get('performance', {}).get('stability_score', -9999),
    reverse=True
)

print(f'Total wallets in report: {len(ranked)}')

print('\nTop 5 por Stability Score:')
for i, s in enumerate(ranked[:5], 1):
    p = s.get('performance', {})
    if not p:
        continue
    print(f"{i}. {s['wallet']} | WR {p.get('win_rate', 0):.1f}% | Stab {p.get('stability_score', 0):.2f} | PnL {p.get('pnl_1usd_stake', 0):+.2f} | {s.get('diagnostic', '')}")

ranked_wr = sorted(
    [s for s in summaries if s.get('performance', {}).get('resolved_trades', 0) >= 10],
    key=lambda s: s.get('performance', {}).get('win_rate', 0),
    reverse=True
)
print('\nTop 5 por Win Rate (min 10 resolved):')
for i, s in enumerate(ranked_wr[:5], 1):
    p = s.get('performance', {})
    print(f"{i}. {s['wallet']} | WR {p.get('win_rate', 0):.1f}% | Stab {p.get('stability_score', 0):.2f} | PnL {p.get('pnl_1usd_stake', 0):+.2f} | {s.get('diagnostic', '')}")

