"""
Gravia Analytics — Multi-dimensional wallet ranking analysis.
Pulls data from gravia.trade API and scores wallets for copy-trading viability.
"""
import requests
import json
import time
from pathlib import Path

# Gravia API
BASE_URL = "https://gravia.trade/api/analytics/rankings"

# Auth cookies from browser session
COOKIES = {
    "access_granted": "Fe26.2*1*ee8a3a2678eee31de1c243bf5e8c6b0b29294f168f09a80843423ac27b05cb04*K3sx7kQrBcMjisfcnVEoHQ*GmXs-HaXXKHovmQGGB_9IMZ8M7nzTJ-BAs2Bz37BOs0CG_BbBnxwl71DedKN1N0Ye3XhEw-T-IJCzCbMaviquBEcL9aOj6w5kRWNC62XW15GEcugKczDy18bAoJvcylZ8XtOpXiwyMC9JsCtAc4Vfg*1779425251735*8afab2f4f27b2d4b517d7dbf9ebdd4c09fa285374caa7d48f90899ae3b5531d0*BzxDPqVXhzwfRnvqhQvuiAKntPyoOO8nHu-ArOjKY50~2",
    "auth_session": "Fe26.2*1*05ca9ca8560602b9e0749a9f6ae1213d16f2162258176fac092d6fb1357c3405*BJGHmnOvB0ii_cFbHxzGjw*qGYx4CbUCOd3DaEygS2vjpkuRUSTJWBS5r57nFJZsVsiPSMdxP-LQJAGnwtBFTBn04KHlhbzgFIWvQ_IOREb1Zyc78SZJYPvTkuOJaCwQ1MLQMMncWO51YFKtgxnT1HLz5yRYCiRwq8_YXPx1zFdBraRGBRmvD-4UPYjQCcq_7U7VLIu0X8vPySoL56F3jluen9a7YaxOO7Yzy0nNQ9Se2ngp9_qubklugLFW5fRV-1CVUgG_No_8ID3zmNJLU3GNAUowaQOeBx1tbnFcfUhe18_C95IfxRDalPJrD-8_p2M25XHJrSKHyGv6p5j0wBhr9xZTqkddAWs6YJD4QCb52gQElmLnRG5F_p7Go1l_Vhsgz20dMH1DhbkyRSEzhcG_8eh0bJMXYZ-NFJQHDbrMlJVjZhLOEEtzPD0x_sUBZwu1Hveua2DM0c15Ai5I_WR6ceKXHl27oEFNx5nNy0XqcMHOennfIiyPz1Ma4tZC9WTEmEQLzD0Q4V8LP39q5ndagWtezUbTEGaXVF2Luio79Vu-5lxphIngKt3igQnWMJM5cOJo24Rz8VXLzluli4VGeawrWPr7NiZg2XEPNmmFJslPI_sbaiRems8RaJFOX9Hbn_2vCqcGWHm7GtJD8y2-tCFRch27IwUuFoGNvUpiZo7fmBu9L3ANxQ2Go8kXyU3rRtYcfWUv6kaeBSsBa4we29Txm-Irc3wxMvAbWxNAmssUwFAhbI8_PYRpaZtJ3M06uiPqrhpCWE_9y5IMqtl9OwZgpiV11JfiCSUhV6A9D4ThMBTMPNNT07tT0-_o625fZuQe0RBRjZjp3tfCeTAJeh_ZUN4_QZsatn_XWWaQRsCynSMeq9ZZLfVjBWB_489nfRmIW58a0wA3--DohLUPLC_1aaIIaXafjOOMVFzyAFzqCRpgNcPI8kmC8-GEKcpAJchGzyEOm9uNwBht-dljb_eeVmYgLGRiJNPn0rGGYFI_CP9pTKlLa1UKEzjrFYG1EyM69ViHghPHF-y-rts7RVKhQ_kH4aK4j1-sxKS5OCkC-FOPsY6_gTas_CoSiQSubMyQEGanSMBExSshQcKSMYXoyhN2fvVHF7IEue28A1Fn5hLHlgMlauEfdFjs92g8yEGXrST2L72BDDrxljuuWrZP8sUXaacZsoLe5rtb-4BcsqrlO2wLPXBdE9kp9M5XmV_5239RnJSFab0ylPmpnlbZScvGCz8chlWfZLTtMjjHDU49IZTVhVpSzfs6I-me7U7mrKJRUerr_YsVeSpXU96832vOP9juPQLa2xeU4ND7n1dTvvQ4o2MZvB2ciMIGqx1k3lBXio-QjCd10HEa_7Rmff5DPLOL_h6WF-eTOjM50EOyrqaZCRuzyN17F0ebhzExfISs848NKn1Wy7e_zYhrlTjigN87d5J6CztV_npo181Lq3kBLZekKxJ4UcfFHS1_Z1Cne9UhCV-lVK-EHdUK1mj1nJYiA7iXpuK_ok-t6Xqonh2t93eT-p84ThGBFxBpllztI0JWMHqu2dKv4h1t9P3hfHaHvxCPKo0uVwAXS3aZ7fPFX-O9l5LsbP9JNrjy0Tl2GyxLIr34VLxNTyXrpmIV4TOboRy2T1UTWGWNc01mMvmao6RE2gs2Gxtk8V9DSZd9dgivkbOpjF5jpL5yhFLQ6K3ZowDyPd7rhMmhX8Sq80PuLby5FalwQVJfQDs18UUvZaKPN8XdOU8uZA3rTHFyPR1WFLyC5w5ZJSUChS34lPSMkdFUlvNY6s8YG1ZvOCObmoI95m-UEn5J3545bMbt7pUtbNPVaHPhqz9IhXrOfUsZehdQoKtA-rYwqhrhwQ_FJpL-4zi-6hC8rgxehlKzz68U2sflUc_FdxSuQ0aSpzo6zTR1S1ZnDKd4E33naEIkOuodebjVZF1WexKWseTZK2txnNlLJoJoOmQz_tm8ex7K18QAkuzt06d5x0tb7SKuQXfJOt9FSupeK7Elt67Ml86rJ_o7CEuXzjUoMMhfeH5U2fk9PlRlq-6a0v2BcF8_7nByh8zdUfI9dzuEG5HqcX-Dzel2LRxDdkLYJtoUdgB6bkYfYNlfpyAR8CuGpPn-O8w7qjXNFHXcuRrmc8mJ0PaCZ_cDFyyFBzPR0Xj6nEpgCQKKvoa35Qcucg_ZUqBeYeqwXb8-ZADX3MbhRXRqTe2Ue3BKnjc4X43DjNmfDCKA1VymhzRekQTOChaO0v6m7cZZKWJ5YpDd5E5CtbDKNgmTEo3tOEvHUlTbT3WMPDl3C6I122oUwag6OQ31kj8pwVpEDddEQottqjWeT-L6zHPlLjP2hFpbEym_8B6M3esDMB84dt0io7uoY8z5AcOts5FzHnpd7FgnoRcHeVu8g7zsLIgYOuQ9YXc2Swph8SM0qgyV1Y1bCh_UDft-XggZSiA52cwWEoCe7TYGt-fDmg_WNwmbv_4bTajxa2DqboraCxqiM81NV_ephndWTvKf2Ul1h0Tbw-3IyuFS-pF82RVc7Vz85iuQrP2R_h14sIXjaqgkRapecI740ogZIa4yZzvEcVYuVRGovpxCfAdfTzmg6M9LEk1OTAbVUVyJCrmS1tgWv1mxt65_6DZNbJnSzoRWYlhRJuMbRCSXjkz2ATVTua7JviiffaGVV0SeEZ2kXXhGPzVnB5J_SYhYDB52Vzr-i40QM0_u6faODxQJyqHktY4rsG-VTwyc4rvow7p5b6GPNwZeznt5oqIsjc6MkHnJWEJjQtfsx7uclt4l8u-eIa36g5X2SkYsPtahntoyWILYxjLzzQuI2Ity4RMHQX8VmVcabvFSHqyKp80GkJ6LLzGwXRj0SWRi6LDob0H3-EQloSeJJTOVVd9EEcqq1CXSUKzaMfECnEw2i4blGbUzDLPYpRX2x46-UuaAL27z4UA3btoEiQW3xwfst5JYHbvSGW8RC6j4j9u4lUXQICcOCI-dpuW0H_VrGb9O0MV-z0_jSNuh01FKprvxe_MYoB1trrj6_tkpJeWxb4cxMBNBduppJ0rKAu924L0DKaahM4hrJWCwy4WxrRopXec_VP2JWDoWITJk0j2XVGyEseWSziwb6P0cD087l4x8XxHYXr04x9wxfjdDSCNiBai-ASM7q98ifpaXgUhEDX8zmfME9ibt4SWmg*1779520354745*52f711f0bb541fce35a02a0f78b2fb4660f9ecced138c5e09f9520efa0e8c59a*g9JWJA9OHcD4HnlgmYpUjPI7W-fKfbfyVuT4QDpY3Ho~2",
    "privy-session": "t",
    "cf_clearance": "MHvIkfwxBFFbDKBvEI8bpqnY5AlKNuWNIrLfaJWOHdE-1778341616-1.2.1.1-ikrjomVwIFG.FSKkhkQgBO635xO9nZOkO.aGAlMBQdAR51ksHDW.Oww.CDOKTwEDT.Q_tb871fg3g9H9fyDiw_KPLB9wHmlo0UQuPiFDybBpxN2N5C8RyIIyoNAfxr7sg132rRIgIK7pf1bAvpXkTmOAn2KhjvjGw4WJ_MOAZFIxWA7X.QeMHZkHwJvGS68Ceuf6p0r7syZfjXkOhBB2aW3byInkTNCd3VU3G.oc2valj2eLRuQPxERPdeOf8iZdSGdhANGJ.bs76ZHeCzJgHHTBCwQO5cKuZAovM3eS8eeTs3d9vKn765b2Pm2hPXBZr5K_YE5qAr_gLg_0gqJ3GQ",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    "Accept": "*/*",
    "Referer": "https://gravia.trade/traders",
    "sec-ch-ua": '"Microsoft Edge";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

OUT_DIR = Path("data")
OUT_DIR.mkdir(exist_ok=True)


def fetch_rankings(order_by: str, limit: int = 100) -> list:
    """Fetch rankings from Gravia API."""
    params = {"order_by": order_by, "order_dir": "desc", "limit": limit}
    try:
        r = requests.get(BASE_URL, params=params, cookies=COOKIES, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return data.get("rankings", [])
            return data
        else:
            print(f"  Error {r.status_code} for order_by={order_by}: {r.text[:100]}")
            return []
    except Exception as e:
        print(f"  Exception for {order_by}: {e}")
        return []


# ═══════════════════════════════════════════════════════════
#  FETCH DATA FROM MULTIPLE ORDERINGS
# ═══════════════════════════════════════════════════════════
ORDERINGS = ["pnl_1d", "pnl_7d", "pnl_30d", "win_rate", "roi", "profit_factor"]

print("=" * 90)
print("  GRAVIA ANALYTICS — WALLET INTELLIGENCE FOR COPY TRADING")
print("=" * 90)

all_wallets = {}  # address -> merged data

for order in ORDERINGS:
    print(f"\n  Fetching order_by={order}...")
    data = fetch_rankings(order)
    print(f"  Got {len(data)} wallets")
    
    for w in data:
        addr = w.get("user_address", "")
        if not addr:
            continue
        if addr not in all_wallets:
            all_wallets[addr] = w
        # Track which rankings this wallet appears in
        if "_rankings_appeared" not in all_wallets[addr]:
            all_wallets[addr]["_rankings_appeared"] = []
        all_wallets[addr]["_rankings_appeared"].append(order)
    
    time.sleep(1)  # Rate limit

# Save raw data
raw_file = OUT_DIR / "gravia_raw.json"
with open(raw_file, "w") as f:
    json.dump(list(all_wallets.values()), f, indent=2)
print(f"\n  Saved {len(all_wallets)} unique wallets to {raw_file}")

# ═══════════════════════════════════════════════════════════
#  SCORE WALLETS FOR COPY-TRADING VIABILITY
# ═══════════════════════════════════════════════════════════
#
# What makes a good copy target for us ($40 capital):
# 1. HIGH frequency (trades often → we get more data faster)
# 2. GOOD win rate (>50% minimum)
# 3. PROFITABLE (positive PnL 1d, 7d)
# 4. LOW positions count (we have limited capital)
# 5. NOT high-frequency/bot (we can't match ms execution)
# 6. Active recently (last_activity within 24h)
# 7. Categories we can copy (NOT crypto 5min — too fast)

print("\n\n" + "=" * 90)
print("  COPY-TRADING VIABILITY SCORING")
print("=" * 90)

scored = []
now = time.time()

for addr, w in all_wallets.items():
    # Extract fields (PnL values are in micro-units, divide by 1e6)
    name = ""
    profile = w.get("public_profile", {})
    if profile:
        name = profile.get("name", "") or profile.get("pseudonym", "") or ""
    
    wr = w.get("win_rate", 0) or 0
    pnl_1d = (w.get("pnl_1d", 0) or 0) / 1e6  # Convert to USD
    pnl_7d = (w.get("pnl_7d", 0) or 0) / 1e6
    pnl_30d = (w.get("pnl_30d", 0) or 0) / 1e6
    total_pnl = (w.get("total_pnl", 0) or 0) / 1e6
    roi = (w.get("roi", 0) or 0) * 100  # Convert to %
    pf = w.get("profit_factor", 0) or 0
    positions = w.get("positions_count", 0) or 0
    resolved = w.get("resolved_positions_count", 0) or 0
    total_trades = w.get("total_trades_count", 0) or 0
    consistency = w.get("consistency_score", 0) or 0
    primary_cat = w.get("primary_category", "?")
    l1_title = w.get("l1_title", "?")
    l2_title = w.get("l2_title", "?")
    categories = w.get("category_labels", []) or []
    hold_time = w.get("holding_time", 0) or 0  # seconds
    hold_hours = hold_time / 3600
    
    # Check last activity
    last_act = w.get("last_activity", "")
    
    # ── FILTERS ──
    # Skip bots/HFT (we can't match their speed)
    is_hft = "high_frequency" in categories and "bot" in categories
    
    # Skip if no data
    if wr == 0 and total_pnl == 0:
        continue
    
    # ── SCORING ──
    score = 0
    flags = []
    
    # Win rate (0-25 points)
    if wr >= 70:
        score += 25
        flags.append("WR70+")
    elif wr >= 55:
        score += 15
    elif wr >= 45:
        score += 5
    
    # PnL 1d positive (0-20 points)
    if pnl_1d > 1000:
        score += 20
        flags.append("PnL1d>$1K")
    elif pnl_1d > 100:
        score += 15
    elif pnl_1d > 0:
        score += 5
    
    # PnL 7d positive (0-15 points)
    if pnl_7d > 1000:
        score += 15
    elif pnl_7d > 0:
        score += 8
    
    # Profit factor (0-15 points)
    if pf > 5:
        score += 15
        flags.append("PF>5")
    elif pf > 2:
        score += 10
    elif pf > 1.5:
        score += 5
    
    # Low position count = good for us (0-10 points)
    if 5 <= positions <= 30:
        score += 10
        flags.append("FewPos")
    elif positions <= 50:
        score += 5
    
    # Consistency (0-10 points)
    if consistency > 0.6:
        score += 10
    elif consistency > 0.3:
        score += 5
    
    # ROI (0-5 points)
    if roi > 50:
        score += 5
    
    # ── PENALTIES ──
    if is_hft:
        score -= 15
        flags.append("HFT-penalty")
    
    # Crypto-only penalty (hard to copy due to speed)
    cat_pos = w.get("category_positions", {}) or {}
    crypto_count = 0
    total_cat_count = 0
    for cat_name, cat_data in cat_pos.items():
        c = cat_data.get("c", 0) if isinstance(cat_data, dict) else 0
        total_cat_count += c
        if cat_name in ("crypto",):
            crypto_count += c
    
    crypto_pct = (crypto_count / total_cat_count * 100) if total_cat_count > 0 else 0
    if crypto_pct > 80:
        score -= 10
        flags.append("Crypto80%+")
    
    # Very long hold penalty (>30 days avg = too slow)
    if hold_hours > 720:
        score -= 5
    
    scored.append({
        "address": addr,
        "name": name[:16],
        "score": score,
        "wr": wr,
        "pnl_1d": pnl_1d,
        "pnl_7d": pnl_7d,
        "pnl_30d": pnl_30d,
        "total_pnl": total_pnl,
        "roi": roi,
        "pf": pf,
        "positions": positions,
        "resolved": resolved,
        "trades": total_trades,
        "consistency": consistency,
        "category": primary_cat,
        "l1": l1_title,
        "l2": l2_title,
        "hold_hours": hold_hours,
        "crypto_pct": crypto_pct,
        "flags": flags,
        "rankings": len(w.get("_rankings_appeared", [])),
        "last_activity": last_act,
    })

# Sort by score
scored.sort(key=lambda x: x["score"], reverse=True)

# ═══════════════════════════════════════════════════════════
#  TOP 25 LEADERBOARD
# ═══════════════════════════════════════════════════════════
print(f"\n  TOP 25 WALLETS FOR COPY-TRADING (of {len(scored)} analyzed)")
print("-" * 90)
print(f"  {'#':>2} {'NAME':<16} {'SCORE':>5} {'WR':>5} {'PnL 1d':>10} {'PnL 7d':>10} "
      f"{'PF':>6} {'POS':>4} {'HOLD':>6} {'CAT':<12} {'FLAGS'}")
print("-" * 90)

for i, w in enumerate(scored[:25], 1):
    hold_str = f"{w['hold_hours']:.0f}h" if w['hold_hours'] < 720 else f"{w['hold_hours']/24:.0f}d"
    flags_str = ", ".join(w["flags"]) if w["flags"] else ""
    pnl1d = f"${w['pnl_1d']:>+,.0f}" if abs(w['pnl_1d']) >= 1 else f"${w['pnl_1d']:>+.1f}"
    pnl7d = f"${w['pnl_7d']:>+,.0f}" if abs(w['pnl_7d']) >= 1 else f"${w['pnl_7d']:>+.1f}"
    
    print(
        f"  {i:>2} {w['name']:<16} {w['score']:>5} {w['wr']:>4.0f}% {pnl1d:>10} {pnl7d:>10} "
        f"{w['pf']:>6.1f} {w['positions']:>4} {hold_str:>6} {w['category']:<12} {flags_str}"
    )

# ═══════════════════════════════════════════════════════════
#  DETAILED TOP 10
# ═══════════════════════════════════════════════════════════
print(f"\n\n  DETAILED TOP 10")
print("=" * 90)
for i, w in enumerate(scored[:10], 1):
    print(f"\n  #{i} {w['name']} ({w['address'][:14]}...)")
    print(f"     Score: {w['score']} | WR: {w['wr']:.1f}% | PF: {w['pf']:.1f} | ROI: {w['roi']:.0f}%")
    print(f"     PnL: 1d=${w['pnl_1d']:+,.0f} | 7d=${w['pnl_7d']:+,.0f} | 30d=${w['pnl_30d']:+,.0f} | Total=${w['total_pnl']:+,.0f}")
    print(f"     Positions: {w['positions']} (resolved={w['resolved']}) | Trades: {w['trades']} | Hold: {w['hold_hours']:.0f}h")
    print(f"     Category: {w['category']} | Type: {w['l1']} / {w['l2']}")
    print(f"     Crypto%: {w['crypto_pct']:.0f}% | Consistency: {w['consistency']:.2f}")
    print(f"     Last activity: {w['last_activity']}")

# Save scored results
scored_file = OUT_DIR / "gravia_scored.json"
with open(scored_file, "w") as f:
    json.dump(scored, f, indent=2)
print(f"\n\nSaved {len(scored)} scored wallets to {scored_file}")
print("=" * 90)
