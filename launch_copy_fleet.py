"""
launch_copy_fleet.py — Launch multiple copy bots simultaneously.

Total capital: $40 split across 5 wallets ($8 each)
Stake: $2 per trade → max 4 concurrent positions per wallet

Usage:
  python launch_copy_fleet.py
  python launch_copy_fleet.py --dry-run     # show commands without executing
  python launch_copy_fleet.py --capital 40   # override total capital
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

# ── TOP 5 WALLETS TO COPY ────────────────────────────────────────
# Selected from rankings.json analysis (non-crypto, high WR/PnL)
WALLETS = [
    {
        "address": "0x5d0f03cf1243a3e21262d6cf844795afd9fff0ad",
        "name": "EB99999",
        "category": "geopolitics",
        "wr": 94.1,
        "note": "94% WR, 21 positions, PF=20.58, $326K PnL",
    },
    {
        "address": "0xdb15fbbcc1a8d1cbe112f7a2d74f6f752f2314f1",
        "name": "memain",
        "category": "sports",
        "wr": 85.7,
        "note": "86% WR, 21 positions, sports specialist",
    },
    {
        "address": "0xe7348e92f76c26e879a9d0c1ff37cdbc4a926a78",
        "name": "bobthetradoor",
        "category": "geopolitics",
        "wr": 41.7,
        "note": "333% ROI, 12 positions, longshot king PF=114",
    },
    {
        "address": "0xd7f85d0eb0fe0732ca38d9107ad0d4d01b1289e4",
        "name": "tdrhrhhd",
        "category": "politics",
        "wr": 39.7,
        "note": "$2.4M PnL, 71 pos, PF=13.79, politics/geo",
    },
    {
        "address": "0xf989bd9c62b1eae2c388515fcc766527a8b147cc",
        "name": "vovatoxic",
        "category": "geopolitics",
        "wr": 61.4,
        "note": "61% WR, 99 pos, PF=3.32, diversified",
    },
]


def main():
    parser = argparse.ArgumentParser(description="Launch Copy Bot Fleet")
    parser.add_argument("--capital", type=float, default=40.0, help="Total capital across all wallets")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    parser.add_argument("--wallets", type=int, default=len(WALLETS), help="Number of wallets to launch (1-5)")
    parser.add_argument("--tp", type=float, default=0.50, help="Take profit %% (default: 50%%)")
    parser.add_argument("--sl", type=float, default=0.25, help="Stop loss %% (default: 25%%)")
    parser.add_argument("--poll", type=float, default=30, help="Poll interval (seconds)")
    args = parser.parse_args()

    n_wallets = min(args.wallets, len(WALLETS))
    selected = WALLETS[:n_wallets]
    per_wallet_capital = args.capital / n_wallets

    # Stake sizing:
    # With $8/wallet, we want to cover ~4 positions comfortably
    # $8 / 4 = $2 per trade is the sweet spot
    # Higher wallets with more positions (tdrhrhhd=71, vovatoxic=99) 
    # will naturally need lower stakes to avoid over-exposure
    stake = max(1.0, round(per_wallet_capital / 4, 1))

    print("=" * 70)
    print("  🚀 COPY BOT FLEET — POLYMARKET WALLET TRACKER")
    print("=" * 70)
    print(f"  Total capital:  ${args.capital:.2f}")
    print(f"  Wallets:        {n_wallets}")
    print(f"  Per wallet:     ${per_wallet_capital:.2f}")
    print(f"  Stake/trade:    ${stake:.2f}")
    print(f"  Max positions:  {int(per_wallet_capital / stake)} per wallet")
    print(f"  TP/SL:          {args.tp:.0%} / {args.sl:.0%}")
    print(f"  Poll interval:  {args.poll}s")
    print("=" * 70)

    max_pos = int(per_wallet_capital / stake)
    commands = []

    for w in selected:
        cmd = (
            f"python copy_wallet.py "
            f"--target {w['address']} "
            f"--stake {stake} "
            f"--capital {per_wallet_capital} "
            f"--max-positions {max_pos} "
            f"--tp {args.tp} "
            f"--sl {args.sl} "
            f"--poll {args.poll}"
        )
        commands.append((w, cmd))

    print()
    for i, (w, cmd) in enumerate(commands, 1):
        print(f"  [{i}] {w['name']:<20} | {w['category']:<13} | WR: {w['wr']:.1f}%")
        print(f"      {w['note']}")
        print(f"      $ {cmd}")
        print()

    if args.dry_run:
        print("  ⚠️  DRY RUN — commands not executed")
        return

    print(f"  Starting {len(commands)} copy bots...\n")
    processes = []
    for w, cmd in commands:
        print(f"  ▶️  Launching {w['name']}...")
        proc = subprocess.Popen(
            cmd.split(),
            cwd=str(Path(__file__).parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        processes.append((w["name"], proc))
        time.sleep(1)  # Stagger starts

    print(f"\n  ✅ All {len(processes)} bots launched!\n")
    print("  Press Ctrl+C to stop all bots.\n")
    print("=" * 70)

    # Tail output from all processes
    try:
        while True:
            for name, proc in processes:
                if proc.poll() is not None:
                    print(f"  ⛔ {name} exited with code {proc.returncode}")
                    continue
                line = proc.stdout.readline()
                if line:
                    print(f"  [{name}] {line.rstrip()}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print(f"\n  🛑 Stopping all {len(processes)} bots...")
        for name, proc in processes:
            proc.terminate()
            proc.wait(timeout=5)
        print("  ✅ All bots stopped.\n")


if __name__ == "__main__":
    main()
