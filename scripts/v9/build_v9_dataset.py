"""
build_v9_dataset.py — Consolida todos los trades históricos en un dataset unificado.

Lee todos los backup_trades_* directories y extrae trades resueltos,
normalizándolos con columnas de contexto para el lookup table de V9.

Output: analysis/v9_training_data.json
"""
import json
import os
import glob
from datetime import datetime


# ── Mapeo de archivo → estrategia ──────────────────────────────
FILE_TO_STRATEGY = {
    "hft_trades.json": "V1",
    "hft_trades_v2.json": "V2",
    "hft_trades_v2opt.json": "V2opt",
    "hft_trades_v2opt2.json": "V2opt2",
    "hft_trades_v2opt3.json": "V2opt3",
    "hft_trades_v3.json": "V3",
    "hft_trades_v3_1.json": "V3",
    "hft_trades_v4.json": "V4",
    "hft_trades_v5.json": "V5",
    "hft_trades_v6.json": "V6",
    "hft_trades_v7.json": "V7",
    "hft_trades_v8.json": "V8",
    "hft_trades_v1opt.json": "V1opt",
    "hft_trades_1pm_6pm.json": "V1",  # early session file
}

# ── Mapeo de backup dir → sesión legible ───────────────────────
DIR_TO_SESSION = {
    "backup_trades_20260504_morning": "may04_morning",
    "backup_trades_2026-05-06_1pm_6pm": "may06_afternoon",
    "backup_trades_2026-05-07_12pm_6pm": "may07_afternoon",
    "backup_trades_2026-05-07_night": "may07_night",
    "backup_trades_2026-05-08_overnight": "may08_overnight",
    "backup_trades_2026-05-08_morning": "may08_morning",
    "backup_trades_2026-05-08_afternoon": "may08_afternoon",
}


def classify_time_of_day(utc_hour: int) -> str:
    """
    Clasifica la hora UTC en bucket de tiempo del día (CDT-based).
    
    UTC 5-13  → overnight  (12AM-8AM CDT)
    UTC 13-17 → morning    (8AM-12PM CDT)
    UTC 17-23 → afternoon  (12PM-6PM CDT)
    UTC 23-5  → evening    (6PM-12AM CDT)
    """
    if 5 <= utc_hour < 13:
        return "overnight"
    elif 13 <= utc_hour < 17:
        return "morning"
    elif 17 <= utc_hour < 23:
        return "afternoon"
    else:
        return "evening"


def classify_price(price: float) -> str:
    """Clasifica el entry price en buckets."""
    if price < 0.30:
        return "extreme_low"
    elif price < 0.46:
        return "low"
    elif price <= 0.54:
        return "golden"
    elif price <= 0.65:
        return "high"
    else:
        return "extreme_high"


def classify_signal_strength(score: float) -> str:
    """Clasifica la fuerza de la señal."""
    abs_score = abs(score)
    if abs_score < 0.30:
        return "weak"
    elif abs_score < 0.55:
        return "medium"
    else:
        return "strong"


def parse_entry_time(entry_time_str: str) -> datetime | None:
    """Parse ISO entry_time string to datetime."""
    if not entry_time_str:
        return None
    try:
        # Handle both +00:00 and Z formats
        s = entry_time_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def process_trade(trade: dict, strategy: str, session: str) -> dict | None:
    """Convert a raw trade dict into a normalized training record."""
    # Only resolved trades
    status = trade.get("status", "")
    if status not in ("won", "lost", "sold"):
        return None

    pnl = trade.get("pnl")
    if pnl is None:
        return None

    entry_price = trade.get("entry_price", 0)
    signal_score = trade.get("signal_score", 0) or 0
    entry_time_str = trade.get("entry_time", "")
    
    dt = parse_entry_time(entry_time_str)
    if dt is None:
        return None

    utc_hour = dt.hour
    time_of_day = classify_time_of_day(utc_hour)
    price_bucket = classify_price(entry_price)
    signal_strength = classify_signal_strength(signal_score)

    # Determine win/loss
    result = "won" if pnl > 0 else "lost"

    return {
        "session": session,
        "time_of_day": time_of_day,
        "utc_hour": utc_hour,
        "asset": trade.get("asset", "?"),
        "direction": trade.get("side", "?"),
        "entry_price": round(entry_price, 4),
        "price_bucket": price_bucket,
        "signal_score": round(signal_score, 4),
        "signal_strength": signal_strength,
        "strategy": strategy,
        "result": result,
        "pnl": round(pnl, 4),
        "exit_reason": trade.get("exit_reason", "resolution"),
        "entry_time": entry_time_str,
    }


def main():
    dataset = []
    stats = {
        "total_files_scanned": 0,
        "total_trades_raw": 0,
        "total_trades_resolved": 0,
        "by_strategy": {},
        "by_session": {},
        "by_time_of_day": {},
        "by_asset": {},
    }

    backup_dirs = sorted(glob.glob("backup_trades_*"))
    print(f"Found {len(backup_dirs)} backup directories\n")

    for backup_dir in backup_dirs:
        session = DIR_TO_SESSION.get(os.path.basename(backup_dir), os.path.basename(backup_dir))
        
        trade_files = glob.glob(os.path.join(backup_dir, "hft_trades*.json"))
        
        for filepath in trade_files:
            filename = os.path.basename(filepath)
            strategy = FILE_TO_STRATEGY.get(filename)
            if strategy is None:
                continue  # skip unknown files
            
            stats["total_files_scanned"] += 1
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    trades = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            
            if not isinstance(trades, list):
                continue
            
            for trade in trades:
                if not isinstance(trade, dict):
                    continue
                stats["total_trades_raw"] += 1
                
                record = process_trade(trade, strategy, session)
                if record:
                    dataset.append(record)
                    stats["total_trades_resolved"] += 1
                    
                    # Update stats
                    stats["by_strategy"][strategy] = stats["by_strategy"].get(strategy, 0) + 1
                    stats["by_session"][session] = stats["by_session"].get(session, 0) + 1
                    stats["by_time_of_day"][record["time_of_day"]] = stats["by_time_of_day"].get(record["time_of_day"], 0) + 1
                    stats["by_asset"][record["asset"]] = stats["by_asset"].get(record["asset"], 0) + 1

    # Sort by entry_time
    dataset.sort(key=lambda x: x.get("entry_time", ""))

    # Save
    os.makedirs("analysis", exist_ok=True)
    output_path = "analysis/v9_training_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "trades": dataset}, f, indent=2, ensure_ascii=False)

    # Print summary
    print("=" * 70)
    print("  V9 DATASET BUILDER — RESULTS")
    print("=" * 70)
    print(f"\n  Files scanned:    {stats['total_files_scanned']}")
    print(f"  Raw trades:       {stats['total_trades_raw']}")
    print(f"  Resolved trades:  {stats['total_trades_resolved']}")
    
    print(f"\n  By Strategy:")
    for s, n in sorted(stats["by_strategy"].items(), key=lambda x: -x[1]):
        wins = sum(1 for r in dataset if r["strategy"] == s and r["result"] == "won")
        wr = round(wins / n * 100, 1) if n > 0 else 0
        print(f"    {s:<10} {n:>4} trades  WR={wr:.1f}%")
    
    print(f"\n  By Session:")
    for s, n in sorted(stats["by_session"].items()):
        print(f"    {s:<25} {n:>4} trades")
    
    print(f"\n  By Time of Day:")
    for t, n in sorted(stats["by_time_of_day"].items()):
        print(f"    {t:<15} {n:>4} trades")
    
    print(f"\n  By Asset:")
    for a, n in sorted(stats["by_asset"].items()):
        wins = sum(1 for r in dataset if r["asset"] == a and r["result"] == "won")
        wr = round(wins / n * 100, 1) if n > 0 else 0
        print(f"    {a:<5} {n:>4} trades  WR={wr:.1f}%")
    
    print(f"\n  Output saved to: {output_path}")


if __name__ == "__main__":
    main()
