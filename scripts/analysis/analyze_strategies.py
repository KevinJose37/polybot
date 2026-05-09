import json, os

files = [
    ("V1", "hft_trades.json"),
    ("V1-OPT", "hft_trades_v1opt.json"),
    ("V2-OPT", "hft_trades_v2opt.json"),
    ("V3", "hft_trades_v3.json"),
    ("V4", "hft_trades_v4.json"),
]

for label, fname in files:
    path = os.path.join(os.getcwd(), fname)
    if not os.path.exists(path):
        print(f"{label}: FILE NOT FOUND ({fname})")
        continue
    with open(path, "r") as f:
        trades = json.load(f)

    total = len(trades)
    resolved = [t for t in trades if t.get("status") in ("won", "lost", "sold")]
    open_t = [t for t in trades if t.get("status") == "open"]
    wins = [t for t in resolved if t.get("pnl", 0) > 0]
    losses = [t for t in resolved if t.get("pnl", 0) <= 0]
    total_pnl = sum(t.get("pnl", 0) for t in resolved)
    total_staked = sum(t.get("stake", 0) for t in resolved)

    # Per-asset breakdown
    assets = {}
    for t in resolved:
        a = t.get("asset", "?")
        if a not in assets:
            assets[a] = {"w": 0, "l": 0, "pnl": 0.0, "staked": 0.0}
        assets[a]["pnl"] += t.get("pnl", 0)
        assets[a]["staked"] += t.get("stake", 0)
        if t.get("pnl", 0) > 0:
            assets[a]["w"] += 1
        else:
            assets[a]["l"] += 1

    # Side breakdown
    sides = {}
    for t in resolved:
        s = t.get("side", "?")
        if s not in sides:
            sides[s] = {"w": 0, "l": 0, "pnl": 0.0}
        sides[s]["pnl"] += t.get("pnl", 0)
        if t.get("pnl", 0) > 0:
            sides[s]["w"] += 1
        else:
            sides[s]["l"] += 1

    wr = len(wins) / len(resolved) * 100 if resolved else 0
    roi = total_pnl / total_staked * 100 if total_staked > 0 else 0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

    print(f"=== {label} ({fname}) ===")
    print(f"  Trades: {total} total | {len(resolved)} resolved | {len(open_t)} open")
    print(f"  W/L: {len(wins)}W/{len(losses)}L | WR: {wr:.1f}%")
    print(f"  P&L: ${total_pnl:+.2f} | Staked: ${total_staked:.2f} | ROI: {roi:+.1f}%")
    print(f"  Avg Win: ${avg_win:+.2f} | Avg Loss: ${avg_loss:+.2f}")

    print(f"  --- Per Asset ---")
    for a, d in sorted(assets.items()):
        awr = d["w"] / (d["w"] + d["l"]) * 100 if (d["w"] + d["l"]) > 0 else 0
        aroi = d["pnl"] / d["staked"] * 100 if d["staked"] > 0 else 0
        print(f"    {a}: {d['w']}W/{d['l']}L ({awr:.0f}%) | P&L ${d['pnl']:+.2f} | ROI {aroi:+.1f}%")

    print(f"  --- Per Side ---")
    for s, d in sorted(sides.items()):
        swr = d["w"] / (d["w"] + d["l"]) * 100 if (d["w"] + d["l"]) > 0 else 0
        print(f"    {s}: {d['w']}W/{d['l']}L ({swr:.0f}%) | P&L ${d['pnl']:+.2f}")

    # Entry price distribution
    entry_prices = [t.get("entry_price", 0) for t in resolved]
    avg_entry = sum(entry_prices) / len(entry_prices) if entry_prices else 0
    print(f"  --- Entry Price ---")
    print(f"    Avg: ${avg_entry:.4f} | Min: ${min(entry_prices):.4f} | Max: ${max(entry_prices):.4f}")

    # Streak analysis
    streak_current = 0
    max_win_streak = 0
    max_loss_streak = 0
    cur_streak_type = None
    cur_streak = 0
    for t in sorted(resolved, key=lambda x: x.get("entry_time", "")):
        w = t.get("pnl", 0) > 0
        if cur_streak_type == w:
            cur_streak += 1
        else:
            cur_streak_type = w
            cur_streak = 1
        if w and cur_streak > max_win_streak:
            max_win_streak = cur_streak
        if not w and cur_streak > max_loss_streak:
            max_loss_streak = cur_streak

    print(f"  --- Streaks ---")
    print(f"    Max win streak: {max_win_streak} | Max loss streak: {max_loss_streak}")
    print()
