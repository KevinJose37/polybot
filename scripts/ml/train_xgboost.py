"""
Entrenamiento del Baseline Model (XGBoost) para la predicción de OFI.
"""
import pandas as pd
import numpy as np
import os

try:
    import xgboost as xgb
    from sklearn.metrics import accuracy_score, precision_score, classification_report
except ImportError:
    print("XGBoost o Scikit-Learn no están instalados. Por favor ejecuta:")
    print("pip install xgboost scikit-learn")
    exit(1)

OUTPUT_DIR = r"D:\Proyectos\polystudio\polystudio\data\ml_features"
TRAIN_PARQUET = os.path.join(OUTPUT_DIR, "train_dataset.parquet")
TEST_PARQUET = os.path.join(OUTPUT_DIR, "test_dataset.parquet")

FEATURES = [
    'spread',
    'bsi',
    'ofi_zscore',
    'ofi_ewma_1m',
    'ofi_ewma_3m',
    'gravity_imbalance',
    'gravity_ewma_1m',
    'ret_1m',
    'ret_3m',
    'spread_ewma_1m',
    'tick_rate_1m'
]

def train_baseline():
    print("=" * 80)
    print("ENTRENAMIENTO MODELO PREDICTIVO (XGBoost) - BTC ONLY")
    print("=" * 80)
    
    if not os.path.exists(TRAIN_PARQUET) or not os.path.exists(TEST_PARQUET):
        print("Error: No se encontraron los datasets de Train/Test.")
        return
        
    train_df = pd.read_parquet(TRAIN_PARQUET)
    test_df = pd.read_parquet(TEST_PARQUET)
    
    print(f"Dataset de Entrenamiento: {len(train_df)} muestras")
    print(f"Dataset de Prueba (OOT): {len(test_df)} muestras")
    
    if len(train_df) < 2 or len(test_df) < 1:
        print("\nNo hay suficientes muestras para entrenar en esta pequeña prueba local.")
        print("Esperando la descarga completa de los parquets...")
        return
        
    X_train = train_df[FEATURES]
    y_train = train_df['label']
    
    X_test = test_df[FEATURES]
    y_test = test_df['label']
    
    print("\n[1/3] Entrenando XGBoost con profundidad controlada (y balanceo de clases)...")
    # Calcular balance de clases
    pos_samples = sum(y_train == 1)
    neg_samples = sum(y_train == 0)
    scale_weight = neg_samples / pos_samples if pos_samples > 0 else 1.0
    
    # Parametrización para evitar overfitting en ruido financiero
    clf = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_weight,
        eval_metric='logloss',
        random_state=42
    )
    
    clf.fit(
        X_train, y_train,
        eval_set=[(X_train, y_train), (X_test, y_test)],
        verbose=False
    )
    
    print("[2/3] Evaluando predicciones Out-of-Sample (OOS)...")
    y_pred = clf.predict(X_test)
    y_prob = clf.predict_proba(X_test)[:, 1]
    
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    
    print(f"\n--- RENDIMIENTO OOS (EXCLUSIVO BTC) ---")
    print(f"Win Rate (Accuracy): {acc*100:.2f}%")
    print(f"Precisión en señales alcistas: {prec*100:.2f}%")
    print("\nReporte Detallado:")
    print(classification_report(y_test, y_pred, zero_division=0))
    
    print("[3/3] Importancia de Features (Gain):")
    importance = clf.feature_importances_
    for feat, imp in sorted(zip(FEATURES, importance), key=lambda x: x[1], reverse=True):
        print(f"  -> {feat:<15}: {imp:.4f}")
        
    print("=" * 80)
    print("ENTRENAMIENTO COMPLETADO.")

if __name__ == "__main__":
    train_baseline()
