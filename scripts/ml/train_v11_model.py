"""
train_v11_model.py — Pipeline completo: ETL → Features → Labels → XGBoost
Procesa parquets diarios de Polymarket, entrena modelo Walk-Forward.

Uso:
    python scripts/ml/train_v11_model.py
"""
import os
import sys
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.stdout.reconfigure(encoding='utf-8', errors='replace') if hasattr(sys.stdout, 'reconfigure') else None
import json
import time
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import duckdb

try:
    import xgboost as xgb
    from sklearn.metrics import accuracy_score, precision_score, recall_score, classification_report
except ImportError:
    print("Instala dependencias: pip install xgboost scikit-learn")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════
BASE_DIR = Path(__file__).resolve().parent.parent.parent
PARQUET_DIR = BASE_DIR / "data" / "parquet" / "filtered_daily"
CACHE_FILE = BASE_DIR / "data" / "parquet" / "known_crypto_markets.json"
OUTPUT_DIR = BASE_DIR / "data" / "ml_models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FEATURE_CUTOFF_S = 30   # Snapshot features at T=30s (when V11 enters)
LABEL_TARGET_S = 290    # Measure outcome at T=290s (near market close)
MIN_MARKET_DURATION_S = 250
RESAMPLE_INTERVAL = "5s"

FEATURES = [
    'spread', 'bsi', 'ofi_zscore', 'ofi_ewma_short', 'ofi_ewma_long',
    'gravity_imbalance', 'gravity_ewma', 'ret_short', 'ret_long',
    'spread_ewma', 'tick_rate', 'depth_ratio', 'early_momentum',
]

ASSETS_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "XRP": ["xrp", "ripple"],
    "SOL": ["solana", "sol"],
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("v11_train")


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def load_crypto_cids() -> dict[str, str]:
    """Load known crypto market CIDs → asset mapping."""
    if not CACHE_FILE.exists():
        log.error("Cache file not found: %s", CACHE_FILE)
        sys.exit(1)
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)

    cid_to_asset = {}
    for cid, info in cache.items():
        if not info.get("is_crypto"):
            continue
        q = info.get("question", "").lower()
        for asset, keywords in ASSETS_KEYWORDS.items():
            if any(kw in q for kw in keywords):
                cid_to_asset[cid] = asset
                break
    return cid_to_asset


def get_parquet_files() -> list[Path]:
    """Get sorted list of daily parquet files."""
    files = sorted(PARQUET_DIR.glob("crypto_2026-*.parquet"))
    return files


def fmt_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def fmt_size(nbytes: int) -> str:
    return f"{nbytes / (1024**2):.1f} MB"


# ═══════════════════════════════════════════════════════════════
# Phase 1: ETL — Load + filter parquet via DuckDB
# ═══════════════════════════════════════════════════════════════

def etl_load_day_chunk(parquet_path: Path, cids: list[str]) -> pd.DataFrame:
    """Load one day's parquet for a specific chunk of CIDs."""
    con = duckdb.connect()
    cid_df = pd.DataFrame({"cid": cids})
    con.register("chunk_cids", cid_df)

    query = f"""
        SELECT
            CAST(market AS VARCHAR) as cid,
            timestamp,
            CAST(best_bid AS DOUBLE) as best_bid,
            CAST(best_ask AS DOUBLE) as best_ask,
            CAST(price AS DOUBLE) as price,
            CAST(size AS DOUBLE) as size,
            side
        FROM read_parquet('{parquet_path}')
        WHERE event_type = 'price_change'
          AND CAST(market AS VARCHAR) IN (SELECT cid FROM chunk_cids)
        ORDER BY cid, timestamp ASC
    """
    df = con.execute(query).df()
    con.close()
    return df

def get_valid_cids_for_day(parquet_path: Path, cid_to_asset: dict) -> list[str]:
    """Find which of our crypto CIDs actually have events in this parquet."""
    cids = list(cid_to_asset.keys())
    con = duckdb.connect()
    cid_df = pd.DataFrame({"cid": cids})
    con.register("crypto_cids", cid_df)
    
    query = f"""
        SELECT DISTINCT CAST(market AS VARCHAR) as cid
        FROM read_parquet('{parquet_path}')
        WHERE CAST(market AS VARCHAR) IN (SELECT cid FROM crypto_cids)
    """
    valid_df = con.execute(query).df()
    con.close()
    return valid_df["cid"].tolist()


# ═══════════════════════════════════════════════════════════════
# Phase 1b: Reconstruct L5 orderbook sizes
# ═══════════════════════════════════════════════════════════════

def reconstruct_l5(df: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct L5 bid/ask sizes from price_change deltas."""
    records = []
    books = defaultdict(lambda: defaultdict(float))

    for row in df.itertuples(index=False):
        cid = row.cid
        if pd.notna(row.price) and pd.notna(row.size):
            books[cid][round(float(row.price), 4)] = float(row.size)

        bid_l5 = ask_l5 = 0.0
        bb_size = np.nan
        ba_size = np.nan

        if pd.notna(row.best_bid):
            bb = round(float(row.best_bid), 4)
            bids = sorted([p for p in books[cid] if p <= bb and books[cid][p] > 0], reverse=True)[:5]
            bid_l5 = sum(books[cid][p] for p in bids)
            bb_size = books[cid].get(bb, 0.0)

        if pd.notna(row.best_ask):
            ba = round(float(row.best_ask), 4)
            asks = sorted([p for p in books[cid] if p >= ba and books[cid][p] > 0])[:5]
            ask_l5 = sum(books[cid][p] for p in asks)
            ba_size = books[cid].get(ba, 0.0)

        mid = np.nan
        if pd.notna(row.best_bid) and pd.notna(row.best_ask):
            mid = (float(row.best_bid) + float(row.best_ask)) / 2.0

        records.append({
            "cid": cid, "asset": row.asset, "timestamp": row.timestamp,
            "best_bid": row.best_bid, "best_ask": row.best_ask,
            "best_bid_size": bb_size, "best_ask_size": ba_size,
            "bid_size_l5": bid_l5, "ask_size_l5": ask_l5,
            "mid_price": mid,
        })

    return pd.DataFrame(records)


# ═══════════════════════════════════════════════════════════════
# Phase 1c: Resample to 5s candles
# ═══════════════════════════════════════════════════════════════

def resample_to_5s(df: pd.DataFrame) -> pd.DataFrame:
    """Resample L5 data to 5-second candles per market."""
    df = df.dropna(subset=["best_bid", "best_ask"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df.set_index("timestamp", inplace=True)

    parts = []
    for cid, grp in df.groupby("cid"):
        res = grp.resample(RESAMPLE_INTERVAL).last()
        ticks = grp.resample(RESAMPLE_INTERVAL).size()
        res = res.ffill().dropna(subset=["mid_price"])
        if len(res) == 0:
            continue
        res["cid"] = cid
        res["asset"] = grp["asset"].iloc[0]
        res["tick_count"] = ticks
        res["seconds_since_start"] = (res.index - res.index[0]).total_seconds()
        parts.append(res)

    if not parts:
        return pd.DataFrame()
    result = pd.concat(parts).reset_index()
    return result


# ═══════════════════════════════════════════════════════════════
# Phase 2: Feature Engineering
# ═══════════════════════════════════════════════════════════════

def calculate_ofi(group: pd.DataFrame) -> np.ndarray:
    """Order Flow Imbalance (Cont et al.)."""
    prev_bid = group["best_bid"].shift(1)
    prev_bid_sz = group["best_bid_size"].shift(1)
    prev_ask = group["best_ask"].shift(1)
    prev_ask_sz = group["best_ask_size"].shift(1)

    bid_ofi = np.where(group["best_bid"] > prev_bid, group["best_bid_size"],
                np.where(group["best_bid"] == prev_bid,
                         group["best_bid_size"] - prev_bid_sz, -prev_bid_sz))
    ask_ofi = np.where(group["best_ask"] < prev_ask, group["best_ask_size"],
                np.where(group["best_ask"] == prev_ask,
                         group["best_ask_size"] - prev_ask_sz, -prev_ask_sz))
    return np.nan_to_num(bid_ofi - ask_ofi, nan=0.0)


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all microstructure features."""
    df["spread"] = df["best_ask"] - df["best_bid"]
    total_sz = df["best_bid_size"] + df["best_ask_size"]
    df["bsi"] = np.where(total_sz > 0, df["best_bid_size"] / total_sz, 0.5)
    total_l5 = df["bid_size_l5"] + df["ask_size_l5"]
    df["gravity_imbalance"] = np.where(total_l5 > 0, (df["bid_size_l5"] - df["ask_size_l5"]) / total_l5, 0.0)
    df["depth_ratio"] = np.where(df["ask_size_l5"] > 0, df["bid_size_l5"] / df["ask_size_l5"], 1.0)
    df["depth_ratio"] = np.clip(df["depth_ratio"], 0.01, 100.0)

    df["ofi_raw"] = 0.0
    parts = []
    for cid, grp in df.groupby("cid"):
        grp = grp.copy()
        grp["ofi_raw"] = calculate_ofi(grp)

        ofi_std = grp["ofi_raw"].std()
        ofi_mean = grp["ofi_raw"].mean()
        grp["ofi_zscore"] = ((grp["ofi_raw"] - ofi_mean) / ofi_std) if ofi_std > 0 else 0.0
        grp["ofi_zscore"] = np.clip(grp["ofi_zscore"], -5.0, 5.0)

        span_short, span_long = 6, 18  # 30s and 90s at 5s intervals
        grp["ofi_ewma_short"] = grp["ofi_zscore"].ewm(span=span_short, adjust=False).mean()
        grp["ofi_ewma_long"] = grp["ofi_zscore"].ewm(span=span_long, adjust=False).mean()
        grp["gravity_ewma"] = grp["gravity_imbalance"].ewm(span=span_short, adjust=False).mean()
        grp["ret_short"] = grp["mid_price"].pct_change(span_short).fillna(0)
        grp["ret_long"] = grp["mid_price"].pct_change(span_long).fillna(0)
        grp["spread_ewma"] = grp["spread"].ewm(span=span_short, adjust=False).mean()
        grp["tick_rate"] = grp["tick_count"].ewm(span=span_short, adjust=False).mean()
        grp["early_momentum"] = grp["mid_price"].pct_change(6).fillna(0)  # 30s momentum

        parts.append(grp)

    return pd.concat(parts)


# ═══════════════════════════════════════════════════════════════
# Phase 3: Label Generation
# ═══════════════════════════════════════════════════════════════

def generate_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Generate labels: at T=30s, will price be higher at T=290s?"""
    records = []
    for cid, grp in df.groupby("cid"):
        feat_df = grp[grp["seconds_since_start"] <= FEATURE_CUTOFF_S]
        if len(feat_df) == 0:
            continue
        feat_row = feat_df.iloc[-1]

        target_df = grp[grp["seconds_since_start"] <= LABEL_TARGET_S]
        if len(target_df) == 0:
            continue
        target_row = target_df.iloc[-1]
        if target_row["seconds_since_start"] < MIN_MARKET_DURATION_S:
            continue

        cur_price = feat_row["mid_price"]
        fut_price = target_row["mid_price"]
        if cur_price <= 0 or pd.isna(cur_price) or pd.isna(fut_price):
            continue

        label = 1 if fut_price > cur_price else 0
        record = {
            "cid": cid,
            "asset": feat_row["asset"],
            "current_price": cur_price,
            "future_price": fut_price,
            "label": label,
        }
        for f in FEATURES:
            record[f] = feat_row.get(f, 0.0)
        records.append(record)

    return pd.DataFrame(records)


# ═══════════════════════════════════════════════════════════════
# Phase 4: Walk-Forward XGBoost
# ═══════════════════════════════════════════════════════════════

def train_walk_forward(daily_datasets: list[tuple[str, pd.DataFrame]]):
    """Walk-forward: train on days 1..N, test on day N+1."""
    import math

    if len(daily_datasets) < 3:
        log.warning("Need at least 3 days for walk-forward. Got %d.", len(daily_datasets))
        return

    all_results = []
    min_train_days = max(2, len(daily_datasets) // 3)

    print("\n" + "=" * 70)
    print("  WALK-FORWARD TRAINING")
    print("=" * 70)

    for i in range(min_train_days, len(daily_datasets)):
        train_parts = [ds for _, ds in daily_datasets[:i] if len(ds) > 0]
        test_label, test_ds = daily_datasets[i]

        if len(test_ds) < 5 or not train_parts:
            continue

        train_df = pd.concat(train_parts)
        X_train = train_df[FEATURES].fillna(0)
        y_train = train_df["label"]
        X_test = test_ds[FEATURES].fillna(0)
        y_test = test_ds["label"]

        pos = int(y_train.sum())
        neg = len(y_train) - pos
        scale_w = neg / pos if pos > 0 else 1.0

        clf = xgb.XGBClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=scale_w, eval_metric="logloss",
            random_state=42, verbosity=0,
        )
        clf.fit(X_train, y_train, verbose=False)

        y_pred = clf.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, zero_division=0)
        rec = recall_score(y_test, y_pred, zero_division=0)

        # P-value (Z-test vs 50% baseline)
        n = len(y_test)
        wr = acc
        z = (wr - 0.5) / math.sqrt(0.5 * 0.5 / n) if n > 0 else 0
        p_val = 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

        sig = "SÍ" if p_val < 0.05 else "NO"
        result = {
            "fold": f"Train[1..{i}] → Test[{test_label}]",
            "train_n": len(train_df), "test_n": n,
            "accuracy": acc, "precision": prec, "recall": rec,
            "p_value": p_val, "significant": sig,
        }
        all_results.append(result)

        print(f"\n  Fold: Train días 1-{i} ({len(train_df)} samples) → Test {test_label} ({n} samples)")
        print(f"    WR: {acc*100:.1f}% | Precision: {prec*100:.1f}% | Recall: {rec*100:.1f}%")
        print(f"    p-value: {p_val:.4f} | Significativo (p<0.05): {sig}")

    # Final model: train on ALL data
    if all_results:
        print("\n" + "-" * 70)
        print("  MODELO FINAL (entrenado con TODOS los días)")
        print("-" * 70)

        all_train = pd.concat([ds for _, ds in daily_datasets if len(ds) > 0])
        X_all = all_train[FEATURES].fillna(0)
        y_all = all_train["label"]
        pos = int(y_all.sum())
        neg = len(y_all) - pos
        scale_w = neg / pos if pos > 0 else 1.0

        final_clf = xgb.XGBClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=scale_w, eval_metric="logloss",
            random_state=42, verbosity=0,
        )
        final_clf.fit(X_all, y_all, verbose=False)

        model_path = OUTPUT_DIR / "v11_xgboost_model.json"
        final_clf.save_model(str(model_path))
        print(f"  Modelo guardado: {model_path}")
        print(f"  Total muestras entrenadas: {len(all_train)}")
        print(f"  Balance: {pos} UP ({pos/len(all_train)*100:.1f}%) / {neg} DOWN ({neg/len(all_train)*100:.1f}%)")

        # Feature importance
        imp = final_clf.feature_importances_
        print("\n  Feature Importance (Gain):")
        for feat, score in sorted(zip(FEATURES, imp), key=lambda x: x[1], reverse=True):
            bar = "#" * int(score * 50)
            print(f"    {feat:<20} {score:.4f} {bar}")

    # Summary table
    print("\n" + "=" * 70)
    print("  RESUMEN WALK-FORWARD")
    print("=" * 70)
    avg_acc = np.mean([r["accuracy"] for r in all_results]) if all_results else 0
    avg_p = np.mean([r["p_value"] for r in all_results]) if all_results else 1
    sig_count = sum(1 for r in all_results if r["significant"] == "SÍ")
    print(f"  Folds totales: {len(all_results)}")
    print(f"  WR promedio: {avg_acc*100:.1f}%")
    print(f"  Folds estadísticamente significativos: {sig_count}/{len(all_results)}")
    print(f"  p-value promedio: {avg_p:.4f}")


# ═══════════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════════

def main():
    t_global = time.time()
    print("=" * 70)
    print("  🧠 XGBoost V11 Sniper — Pipeline Multi-Día")
    print(f"  Feature cutoff: T={FEATURE_CUTOFF_S}s | Label target: T={LABEL_TARGET_S}s")
    print("=" * 70)

    # Load market cache
    log.info("Cargando cache de mercados crypto...")
    cid_to_asset = load_crypto_cids()
    log.info("Mercados crypto: %d (BTC: %d, ETH: %d, XRP: %d, SOL: %d)",
             len(cid_to_asset),
             sum(1 for v in cid_to_asset.values() if v == "BTC"),
             sum(1 for v in cid_to_asset.values() if v == "ETH"),
             sum(1 for v in cid_to_asset.values() if v == "XRP"),
             sum(1 for v in cid_to_asset.values() if v == "SOL"))

    # Get parquet files
    parquet_files = get_parquet_files()
    if not parquet_files:
        log.error("No parquet files found in %s", PARQUET_DIR)
        sys.exit(1)
    log.info("Parquets disponibles: %d archivos", len(parquet_files))

    daily_datasets: list[tuple[str, pd.DataFrame]] = []

    for idx, pf in enumerate(parquet_files):
        day_label = pf.stem.replace("crypto_", "")
        t_day = time.time()
        elapsed_global = time.time() - t_global
        if idx > 0:
            avg_per_day = elapsed_global / idx
            remaining = avg_per_day * (len(parquet_files) - idx)
            eta_str = f" | ETA: {fmt_time(remaining)}"
        else:
            eta_str = ""

        print(f"\n{'─' * 70}")
        print(f"  📦 [{idx+1}/{len(parquet_files)}] {day_label} ({fmt_size(pf.stat().st_size)}){eta_str}")
        print(f"{'─' * 70}")

        print(f"  [1/4] Identificando mercados...", end=" ", flush=True)
        valid_cids = get_valid_cids_for_day(pf, cid_to_asset)
        print(f"✓ {len(valid_cids)} mercados crypto en este archivo")

        if not valid_cids:
            continue

        # Chunk logic
        CHUNK_SIZE = 500
        day_datasets = []
        
        for i in range(0, len(valid_cids), CHUNK_SIZE):
            chunk_cids = valid_cids[i:i+CHUNK_SIZE]
            print(f"    - Chunk {i//CHUNK_SIZE + 1} ({len(chunk_cids)} markets)...", end=" ", flush=True)
            
            df_raw = etl_load_day_chunk(pf, chunk_cids)
            if len(df_raw) == 0:
                print("vacio")
                continue
                
            df_raw["asset"] = df_raw["cid"].map(cid_to_asset)
            df_raw = df_raw.dropna(subset=["asset"])
            
            df_l5 = reconstruct_l5(df_raw)
            del df_raw
            
            df_5s = resample_to_5s(df_l5)
            del df_l5
            if len(df_5s) == 0:
                print("sin datos resample")
                continue
                
            df_feat = engineer_features(df_5s)
            del df_5s
            
            ds_chunk = generate_labels(df_feat)
            del df_feat
            
            if len(ds_chunk) > 0:
                day_datasets.append(ds_chunk)
            print(f"✓ {len(ds_chunk)} rows")

        if not day_datasets:
            print(f"⚠️ Sin mercados con duración suficiente. Saltando.")
            continue
            
        dataset = pd.concat(day_datasets, ignore_index=True)

        up = int(dataset["label"].sum())
        dn = len(dataset) - up
        per_asset = dataset["asset"].value_counts().to_dict()
        asset_str = ", ".join(f"{k}:{v}" for k, v in sorted(per_asset.items()))
        print(f"  [✓] Día final: {len(dataset)} mercados (UP:{up} DN:{dn}) [{asset_str}]")

        daily_datasets.append((day_label, dataset))
        print(f"  ⏱ Día completado en {fmt_time(time.time() - t_day)}")

    # Phase 4: Walk-Forward
    total_samples = sum(len(ds) for _, ds in daily_datasets)
    print(f"\n{'=' * 70}")
    print(f"  📊 ETL COMPLETADO: {len(daily_datasets)} días, {total_samples} muestras totales")
    print(f"  ⏱ Tiempo total ETL: {fmt_time(time.time() - t_global)}")
    print(f"{'=' * 70}")

    if daily_datasets:
        train_walk_forward(daily_datasets)

    print(f"\n{'=' * 70}")
    print(f"  ✅ PIPELINE COMPLETADO en {fmt_time(time.time() - t_global)}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
