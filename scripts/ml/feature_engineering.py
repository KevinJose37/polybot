"""
Generador de Features de Microestructura y Order Flow Imbalance (OFI).
Toma el output del ETL y calcula descriptores predictivos por mercado.
"""
import pandas as pd
import numpy as np
import os
import sys

# Configuraciones
INPUT_PARQUET = r"D:\Proyectos\polystudio\polystudio\data\ml_features\etl_resampled_5s.parquet"
OUTPUT_DIR = r"D:\Proyectos\polystudio\polystudio\data\ml_features"
OUTPUT_PARQUET = os.path.join(OUTPUT_DIR, "features_ofi_5s.parquet")

def calculate_ofi(df):
    """
    Cálculo de Order Flow Imbalance (OFI) según Cont et al.
    OFI = Δ(bid_size) - Δ(ask_size) ajustado por cambios en precio.
    """
    # Shifted values
    prev_bid = df['best_bid'].shift(1)
    prev_bid_size = df['best_bid_size'].shift(1)
    prev_ask = df['best_ask'].shift(1)
    prev_ask_size = df['best_ask_size'].shift(1)
    
    # Bid side OFI
    bid_ofi = np.where(df['best_bid'] > prev_bid, df['best_bid_size'],
                np.where(df['best_bid'] == prev_bid, df['best_bid_size'] - prev_bid_size,
                    -prev_bid_size))
                    
    # Ask side OFI
    ask_ofi = np.where(df['best_ask'] < prev_ask, df['best_ask_size'],
                np.where(df['best_ask'] == prev_ask, df['best_ask_size'] - prev_ask_size,
                    -prev_ask_size))
                    
    # Manejar NaNs en el primer tick de cada mercado
    bid_ofi = np.nan_to_num(bid_ofi, nan=0.0)
    ask_ofi = np.nan_to_num(ask_ofi, nan=0.0)
    
    ofi_raw = bid_ofi - ask_ofi
    return ofi_raw

def engineer_features():
    print("=" * 80)
    print("INICIANDO INGENIERÍA DE FEATURES (OFI)")
    print("=" * 80)
    
    if not os.path.exists(INPUT_PARQUET):
        print(f"Error: No se encontró el dataset ETL en {INPUT_PARQUET}")
        return
        
    df = pd.read_parquet(INPUT_PARQUET)
    print(f"[1/3] Cargados {len(df):,} registros base.")
    
    # 1. Orderbook Base Features
    df['spread'] = df['best_ask'] - df['best_bid']
    
    # Bid-Ask Size Imbalance (BSI)
    total_size = df['best_bid_size'] + df['best_ask_size']
    df['bsi'] = np.where(total_size > 0, df['best_bid_size'] / total_size, 0.5)
    
    # 2. Eliminar normalización local y usar Z-Score estadístico sobre OFI global
    print(f"[2/3] Calculando OFI crudo para {df['cid'].nunique()} mercados...")
    
    # Calcular OFI crudo agrupando por mercado para evitar leak en shift(1)
    df['ofi_raw'] = 0.0
    for cid, group in df.groupby('cid'):
        df.loc[group.index, 'ofi_raw'] = calculate_ofi(group)
        
    # Estandarización Z-Score global para BTC
    ofi_mean = df['ofi_raw'].mean()
    ofi_std = df['ofi_raw'].std()
    
    # Evitar división por 0 en el raro caso de que std sea 0
    if ofi_std > 0:
        df['ofi_zscore'] = (df['ofi_raw'] - ofi_mean) / ofi_std
    else:
        df['ofi_zscore'] = 0.0
        
    # Limitar valores atípicos extremos (ej: eventos de black swan) a +/- 5 desviaciones estándar
    df['ofi_zscore'] = np.clip(df['ofi_zscore'], -5.0, 5.0)
    
    print(f"  -> OFI Z-Score Global BTC (Media: {ofi_mean:.2f}, Std: {ofi_std:.2f})")

    # Gravity Imbalance (L5 Depth)
    total_l5 = df['bid_size_l5'] + df['ask_size_l5']
    df['gravity_imbalance'] = np.where(total_l5 > 0, (df['bid_size_l5'] - df['ask_size_l5']) / total_l5, 0.0)

    features_dfs = []
    
    print(f"[3/3] Calculando Momentum, EWMA y Gravedad L5...")
    for cid, group in df.groupby('cid'):
        group = group.copy()
        
        # OFI Z-Score EWMA (Suavizado temporal)
        group['ofi_ewma_1m'] = group['ofi_zscore'].ewm(span=12, adjust=False).mean()
        group['ofi_ewma_3m'] = group['ofi_zscore'].ewm(span=36, adjust=False).mean()
        
        # Gravity EWMA
        group['gravity_ewma_1m'] = group['gravity_imbalance'].ewm(span=12, adjust=False).mean()
        
        # Momentum de Precio (Retorno del mid-price)
        group['ret_1m'] = group['mid_price'].pct_change(12).fillna(0)
        group['ret_3m'] = group['mid_price'].pct_change(36).fillna(0)
        
        # Spread EWMA (Spread Pressure)
        group['spread_ewma_1m'] = group['spread'].ewm(span=12, adjust=False).mean()
        
        # Tick Arrival Rate EWMA (Presión de actividad)
        group['tick_rate_1m'] = group['tick_count'].ewm(span=12, adjust=False).mean()
        
        features_dfs.append(group)
        
    final_df = pd.concat(features_dfs)
    
    print("\nGuardando matriz de features BTC-Only (Con Gravedad L5 y Ohanism)...")
    
    final_df.fillna(0, inplace=True)
    final_df.to_parquet(OUTPUT_PARQUET, index=False)
    
    size_mb = os.path.getsize(OUTPUT_PARQUET) / (1024 * 1024)
    print(f"  -> Guardado en: {OUTPUT_PARQUET}")
    print(f"  -> Features generados: {list(final_df.columns)}")
    print(f"  -> Tamaño: {size_mb:.2f} MB")
    print("=" * 80)
    print("FEATURE ENGINEERING COMPLETADO.")

if __name__ == "__main__":
    engineer_features()
