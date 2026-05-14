"""
Framework de Validación y Generación de Labels para Polymarket.
Previene el Lookahead Bias cortando la extracción en T=4:00 y calculando Y en T=4:50.
"""
import pandas as pd
import numpy as np
import os
import sys

INPUT_PARQUET = r"D:\Proyectos\polystudio\polystudio\data\ml_features\features_ofi_5s.parquet"
OUTPUT_DIR = r"D:\Proyectos\polystudio\polystudio\data\ml_features"
OUTPUT_DATASET = os.path.join(OUTPUT_DIR, "dataset_final.parquet")

def generate_labels(df, feature_cutoff=240, label_target=290):
    """
    feature_cutoff: Segundos desde inicio para tomar el snapshot de features (240s = 4:00).
    label_target: Segundos desde inicio para medir el precio futuro (290s = 4:50).
    """
    records = []
    
    for cid, group in df.groupby('cid'):
        # Buscar el registro más cercano (<=) a feature_cutoff
        feat_df = group[group['seconds_since_start'] <= feature_cutoff]
        if len(feat_df) == 0:
            continue
        feat_row = feat_df.iloc[-1]
        
        # Buscar el registro más cercano a label_target
        target_df = group[group['seconds_since_start'] <= label_target]
        if len(target_df) == 0:
            continue
        target_row = target_df.iloc[-1]
        
        # Si el target no llegó al menos a los 250s, el mercado murió muy rápido, ignoramos
        if target_row['seconds_since_start'] < 250:
            continue
            
        current_price = feat_row['mid_price']
        future_price = target_row['mid_price']
        
        # Etiqueta Binaria: 1 = Sube, 0 = Baja/Igual
        label = 1 if future_price > current_price else 0
        
        # Calcular rentabilidad teórica (spread fees)
        pnl_pct = (future_price - current_price) / current_price
        
        # Extraer solo las features en T=feature_cutoff
        record = {
            'cid': cid,
            'timestamp_cutoff': feat_row['timestamp'],
            'timestamp_target': target_row['timestamp'],
            'current_price': current_price,
            'future_price': future_price,
            'label': label,
            'pnl_pct': pnl_pct,
            # Features
            'spread': feat_row['spread'],
            'bsi': feat_row['bsi'],
            'ofi_zscore': feat_row['ofi_zscore'],
            'ofi_ewma_1m': feat_row['ofi_ewma_1m'],
            'ofi_ewma_3m': feat_row['ofi_ewma_3m'],
            'gravity_imbalance': feat_row['gravity_imbalance'],
            'gravity_ewma_1m': feat_row['gravity_ewma_1m'],
            'ret_1m': feat_row['ret_1m'],
            'ret_3m': feat_row['ret_3m'],
            'spread_ewma_1m': feat_row['spread_ewma_1m'],
            'tick_rate_1m': feat_row['tick_rate_1m'],
        }
        records.append(record)
        
    return pd.DataFrame(records)

def evaluate_baseline(df):
    """Métricas baseline (siempre apostar a 1, o moneda al azar)."""
    if len(df) == 0:
        return
        
    up_count = df['label'].sum()
    total = len(df)
    win_rate = up_count / total if total > 0 else 0
    
    print("\n--- BASELINE NAIVE ---")
    print(f"Total Mercados Analizables: {total}")
    print(f"Subidas reales (Label=1): {up_count} ({win_rate*100:.2f}%)")
    print(f"Bajadas/Planos (Label=0): {total - up_count} ({(1-win_rate)*100:.2f}%)")
    print(f"Win Rate de adivinar 'Sube' siempre: {win_rate*100:.2f}%")

def prepare_dataset():
    print("=" * 80)
    print("INICIANDO GENERACIÓN DE LABELS Y FRAMEWORK")
    print("=" * 80)
    
    if not os.path.exists(INPUT_PARQUET):
        print(f"Error: {INPUT_PARQUET} no encontrado.")
        return
        
    df = pd.read_parquet(INPUT_PARQUET)
    
    print(f"[1/3] Generando variables dependientes (Y) cortando en T=4:00...")
    dataset = generate_labels(df)
    
    if len(dataset) == 0:
        print("Error: Ningún mercado cumplió los criterios de tiempo (>250s).")
        return
        
    evaluate_baseline(dataset)
    
    print(f"\n[2/3] Generando particiones de Validación Walk-Forward...")
    # Como el dataset dummy es pequeño, haremos un split simple estratificado temporalmente.
    # En producción con gigabytes, usaríamos TimeSeriesSplit o rolling window.
    dataset = dataset.sort_values('timestamp_cutoff')
    
    # 80% Train, 20% Test (Out-of-sample estricto en tiempo)
    split_idx = int(len(dataset) * 0.8)
    train_df = dataset.iloc[:split_idx]
    test_df = dataset.iloc[split_idx:]
    
    train_df.to_parquet(os.path.join(OUTPUT_DIR, "train_dataset.parquet"), index=False)
    test_df.to_parquet(os.path.join(OUTPUT_DIR, "test_dataset.parquet"), index=False)
    dataset.to_parquet(OUTPUT_DATASET, index=False)
    
    print(f"[3/3] Guardado dataset listo para XGBoost:")
    print(f"  -> Train: {len(train_df)} muestras")
    print(f"  -> Test (OOS): {len(test_df)} muestras")
    print(f"  -> Guardado en {OUTPUT_DIR}")
    print("=" * 80)
    print("VALIDATION FRAMEWORK COMPLETADO.")

if __name__ == "__main__":
    prepare_dataset()
