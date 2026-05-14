import json
import sys

with open(r'd:\Proyectos\polystudio\polystudio\data\trades\archive_v3_desync\copy_89b5cdaa_live.json') as f:
    live = json.load(f)
with open(r'd:\Proyectos\polystudio\polystudio\data\trades\archive_v3_desync\copy_89b5cdaa_paper.json') as f:
    paper = json.load(f)

print("=== LIVE TRADES ===")
print("Total trades:", len(live))
total_pnl_live = 0
total_stake_live = 0
wins = losses = sold = opn = 0
for t in live:
    if t['pnl'] is not None:
        total_pnl_live += t['pnl']
    total_stake_live += t.get('stake', 0)
    if t['status'] == 'won': wins += 1
    elif t['status'] == 'lost': losses += 1
    elif t['status'] == 'sold': sold += 1
    elif t['status'] == 'open': opn += 1
print("Won: %d, Lost: %d, Sold: %d, Open: %d" % (wins, losses, sold, opn))
print("Total PnL (live): %.4f" % total_pnl_live)
print("Total Stake: %.2f" % total_stake_live)

print()
print("=== PAPER TRADES ===")
print("Total trades:", len(paper))
total_pnl_paper = 0
total_stake_paper = 0
pwins = plosses = psold = popn = 0
for t in paper:
    if t['pnl'] is not None:
        total_pnl_paper += t['pnl']
    total_stake_paper += t.get('stake', 0)
    if t['status'] == 'won': pwins += 1
    elif t['status'] == 'lost': plosses += 1
    elif t['status'] == 'sold': psold += 1
    elif t['status'] == 'open': popn += 1
print("Won: %d, Lost: %d, Sold: %d, Open: %d" % (pwins, plosses, psold, popn))
print("Total PnL (paper): %.4f" % total_pnl_paper)
print("Total Stake: %.2f" % total_stake_paper)

print()
print("=== GHOST TRADES (shares=0, not open) in LIVE ===")
for t in live:
    if t.get('shares', 1) == 0 and t['status'] != 'open':
        src = t.get('entry_source', '')[:40]
        print("  %s | %s | stake=%.2f shares=%.1f pnl=%s | exit=%.4f | %s" % (
            t['id'], t['slug'][:40], t['stake'], t['shares'], t['pnl'], t.get('exit_price', 0) or 0, src))

print()
print("=== NULL fill_meta TRADES in LIVE ===")
for t in live:
    if t.get('fill_meta') is None:
        src = t.get('entry_source', '')[:50]
        print("  %s | %s | stake=%.2f shares=%.1f pnl=%s | exit=%.4f | %s" % (
            t['id'], t['slug'][:45], t['stake'], t['shares'], t['pnl'], t.get('exit_price', 0) or 0, src))

print()
print("=== EXIT PRICE > 1.0 ANOMALIES (live) ===")
for t in live:
    ep = t.get('exit_price')
    if ep is not None and ep > 1.01:
        print("  %s | exit_price=%.4f | pnl=%.2f | entry=%.4f | shares=%.4f | status=%s | %s" % (
            t['id'], ep, t['pnl'], t['entry_price'], t['shares'], t['status'], t['slug'][:40]))

print()
print("=== ENTRY PRICE vs VWAP DISCREPANCY > 0.02 (live) ===")
for t in live:
    fm = t.get('fill_meta')
    if fm and fm.get('vwap'):
        vwap = fm['vwap']
        entry = t['entry_price']
        diff = abs(entry - vwap)
        if diff > 0.02:
            print("  %s | entry=%.4f vwap=%.4f diff=%.4f | shares=%.4f filled=%.4f | pnl=%s | %s" % (
                t['id'], entry, vwap, diff, t['shares'], fm.get('filled_shares', 0), t['pnl'], t['slug'][:35]))

print()
print("=== PnL MISMATCH: Reported pnl vs Calculated from (exit-entry)*shares ===")
print("--- LIVE ---")
total_calc_pnl = 0
for t in live:
    if t['pnl'] is None or t['status'] == 'open':
        continue
    ep = t.get('exit_price', 0) or 0
    entry = t['entry_price']
    shares = t['shares']
    stake = t['stake']
    if t['status'] in ('won',):
        calc = (ep * shares) - stake
    elif t['status'] == 'lost':
        calc = (ep * shares) - stake
    elif t['status'] == 'sold':
        calc = (ep * shares) - stake
    else:
        calc = t['pnl']
    reported = t['pnl']
    diff = abs(reported - calc)
    total_calc_pnl += calc
    if diff > 0.05:
        print("  %s | rep=%.2f calc=%.2f diff=%.2f | entry=%.4f exit=%.4f shares=%.4f stake=%.2f | %s | %s" % (
            t['id'], reported, calc, diff, entry, ep, shares, stake, t['status'], t['slug'][:30]))

print()
print("Total calc PnL (live): %.4f" % total_calc_pnl)
print("Total reported PnL (live): %.4f" % total_pnl_live)
print("Discrepancy: %.4f" % (total_pnl_live - total_calc_pnl))

print()
print("=== FILLED SHARES vs REPORTED SHARES (live) ===")
for t in live:
    fm = t.get('fill_meta')
    if fm and fm.get('filled_shares'):
        fs = fm['filled_shares']
        rs = t['shares']
        diff = abs(fs - rs)
        if diff > 0.01:
            print("  %s | reported_shares=%.4f filled_shares=%.4f diff=%.4f | entry=%.4f | %s" % (
                t['id'], rs, fs, diff, t['entry_price'], t['slug'][:35]))

print()
print("=== TRADE-BY-TRADE PnL BREAKDOWN (live) ===")
for t in live:
    if t['pnl'] is None:
        continue
    fm = t.get('fill_meta')
    filled = fm['filled_shares'] if fm and fm.get('filled_shares') else t['shares']
    ep = t.get('exit_price', 0) or 0
    real_pnl = (ep * filled) - t['stake']
    reported = t['pnl']
    inflation = reported - real_pnl
    if abs(inflation) > 0.01:
        print("  %s | reported=%.2f real=%.2f inflation=%.2f | entry=%.4f exit=%.4f | report_shares=%.4f filled=%.4f | %s" % (
            t['id'], reported, real_pnl, inflation, t['entry_price'], ep, t['shares'], filled, t['slug'][:30]))
