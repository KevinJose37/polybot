"""
Analyze rankings.json to find the best wallets to copy.
Focus: NON-crypto-5m markets, few positions, high WR, high PnL.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
RANKINGS_FILE = PROJECT_ROOT / "rankings.json"


def format_usd(val):
    """Format large USDC values (stored as micro-units) to readable USD."""
    # Values appear to be in micro-USDC (6 decimals)
    usd = val / 1e6 if abs(val) > 1e9 else val / 1e3
    if abs(usd) >= 1e6:
        return f"${usd/1e6:,.1f}M"
    elif abs(usd) >= 1e3:
        return f"${usd/1e3:,.1f}K"
    else:
        return f"${usd:,.0f}"


def main():
    with open(RANKINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    rankings = data.get("rankings", [])
    print(f"Total wallets in rankings: {len(rankings)}\n")

    scored = []
    for r in rankings:
        addr = r.get("user_address", "?")
        wr = r.get("win_rate", 0)
        total_pnl = r.get("total_pnl", 0)
        realized_pnl = r.get("realized_pnl", 0)
        positions = r.get("positions_count", 0)
        resolved = r.get("resolved_positions_count", 0)
        trades = r.get("total_trades_count", 0)
        roi = r.get("roi", 0)
        pf = r.get("profit_factor", 0)
        sharpe = r.get("sharpe_ratio", 0)
        consistency = r.get("consistency_score", 0) or 0
        primary_cat = r.get("primary_category", "?")
        labels = r.get("category_labels", [])
        l1_title = r.get("l1_title", "?")
        l2_title = r.get("l2_title", "?")
        name = ""
        pub = r.get("public_profile", {})
        if pub:
            name = pub.get("name", "") or pub.get("pseudonym", "") or ""

        # Category breakdown
        cat_pos = r.get("category_positions", {})

        # FILTER OUT: crypto-5m dominant wallets
        crypto_volume = 0
        total_volume = 0
        cat_summary = {}
        for cat, info in cat_pos.items():
            c = info.get("c", 0)
            v = info.get("v", 0)
            total_volume += v
            cat_summary[cat] = {"positions": c, "volume": v}
            if cat in ("crypto", "crypto_fdv"):
                crypto_volume += v

        # Skip if crypto is >80% of volume (likely 5m scalper)
        crypto_pct = crypto_volume / total_volume * 100 if total_volume > 0 else 0

        # FILTER: must have resolved positions
        if resolved < 5:
            continue

        # Normalize PnL for display (values are in micro-USDC)
        pnl_display = total_pnl / 1e6

        # SCORING: what makes a good wallet to COPY?
        # 1. High WR (more predictable)
        # 2. Positive ROI (actually profitable)
        # 3. Few positions (low capital needed)
        # 4. High profit factor
        # 5. Good consistency

        # Penalty for too many positions (we want capital-efficient)
        pos_penalty = 1.0
        if positions > 100:
            pos_penalty = 100 / positions  # Diminishing returns
        elif positions < 10:
            pos_penalty = 1.5  # Bonus for few positions

        # Penalty for crypto-heavy (hard to copy 5m)
        crypto_penalty = 1.0
        if crypto_pct > 50:
            crypto_penalty = 0.3
        elif crypto_pct > 20:
            crypto_penalty = 0.7

        # Combined score
        wr_score = max(0, (wr - 30) / 70)  # 30-100% mapped to 0-1
        roi_score = max(0, min(1, roi * 2))  # 0-50% ROI mapped to 0-1
        pf_score = max(0, min(1, (pf - 1) / 3))  # PF 1-4 mapped to 0-1
        consistency_score = consistency

        combined = (
            wr_score * 0.30 +
            roi_score * 0.25 +
            pf_score * 0.20 +
            consistency_score * 0.15 +
            (1 if pnl_display > 0 else 0) * 0.10
        ) * pos_penalty * crypto_penalty

        # Top categories by volume
        top_cats = sorted(cat_summary.items(), key=lambda x: -x[1]["volume"])[:4]
        cats_str = " | ".join(f"{k}({v['positions']})" for k, v in top_cats if v["positions"] > 0)

        scored.append({
            "addr": addr,
            "name": name[:20],
            "wr": wr,
            "roi": roi,
            "pnl": pnl_display,
            "realized_pnl": realized_pnl / 1e6,
            "positions": positions,
            "resolved": resolved,
            "trades": trades,
            "pf": pf,
            "sharpe": sharpe,
            "consistency": consistency,
            "primary_cat": primary_cat,
            "crypto_pct": crypto_pct,
            "cats": cats_str,
            "labels": labels,
            "l1": l1_title,
            "l2": l2_title,
            "score": combined,
        })

    # Sort by combined score
    scored.sort(key=lambda x: -x["score"])

    # Display top results
    print("=" * 120)
    print(f"{'#':>3} {'WALLET':>14} {'NAME':>20} {'WR%':>6} {'ROI%':>7} {'PnL':>10} {'POS':>5} {'PF':>5} {'CONS':>5} {'CRYPTO%':>7} {'SCORE':>6} {'PRIMARY':>12}")
    print("=" * 120)

    for i, w in enumerate(scored[:30], 1):
        pnl_str = format_usd(w["pnl"] * 1e6)
        icon = "✅" if w["pnl"] > 0 else "❌"
        crypto_flag = "⚠️" if w["crypto_pct"] > 30 else "  "
        print(
            f"{i:>3} {w['addr'][:14]:>14} {w['name']:>20} "
            f"{w['wr']:>5.1f}% {w['roi']*100:>6.1f}% {pnl_str:>10} "
            f"{w['positions']:>5} {w['pf']:>5.2f} {w['consistency']:>5.2f} "
            f"{w['crypto_pct']:>5.1f}% {crypto_flag} {w['score']:>5.3f} {w['primary_cat']:>12}"
        )

    # Detailed breakdown of top 10
    print("\n\n" + "=" * 120)
    print("DETAILED ANALYSIS — TOP 10 COPYABLE WALLETS")
    print("=" * 120)

    for i, w in enumerate(scored[:10], 1):
        pnl_str = format_usd(w["pnl"] * 1e6)
        print(f"\n{'─' * 80}")
        print(f"#{i} — {w['addr']}")
        print(f"   Name: {w['name']} | Type: {w['l1']} > {w['l2']}")
        print(f"   WR: {w['wr']:.1f}% | ROI: {w['roi']*100:.1f}% | PnL: {pnl_str} | PF: {w['pf']:.2f}")
        print(f"   Positions: {w['positions']} | Resolved: {w['resolved']} | Trades: {w['trades']}")
        print(f"   Consistency: {w['consistency']:.2f} | Sharpe: {w['sharpe']:.3f} | Crypto%: {w['crypto_pct']:.1f}%")
        print(f"   Categories: {w['cats']}")
        print(f"   Labels: {', '.join(w['labels'])}")

        # Copyability assessment
        copyable = True
        reasons = []
        if w["positions"] < 30:
            reasons.append(f"✅ Low capital needed ({w['positions']} positions)")
        else:
            reasons.append(f"⚠️ Many positions ({w['positions']})")
        if w["wr"] > 50:
            reasons.append(f"✅ High WR ({w['wr']:.1f}%)")
        else:
            reasons.append(f"⚠️ Low WR ({w['wr']:.1f}%) — relies on big wins")
        if w["pf"] > 1.5:
            reasons.append(f"✅ Great profit factor ({w['pf']:.2f})")
        elif w["pf"] > 1.0:
            reasons.append(f"⚠️ Thin edge ({w['pf']:.2f})")
        else:
            reasons.append(f"❌ Losing money (PF={w['pf']:.2f})")
            copyable = False
        if w["crypto_pct"] > 30:
            reasons.append(f"⚠️ {w['crypto_pct']:.0f}% crypto — may include 5m markets")
        else:
            reasons.append(f"✅ Non-crypto focused ({w['crypto_pct']:.0f}% crypto)")
        if w["consistency"] > 0.5:
            reasons.append(f"✅ Consistent ({w['consistency']:.2f})")
        else:
            reasons.append(f"⚠️ Inconsistent ({w['consistency']:.2f})")

        for r in reasons:
            print(f"   {r}")

        verdict = "🏆 COPY" if copyable and w["pnl"] > 0 else "⛔ SKIP"
        print(f"   → Verdict: {verdict}")


if __name__ == "__main__":
    main()
