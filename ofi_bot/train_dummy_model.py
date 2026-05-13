import os
import joblib
import numpy as np
from loguru import logger
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

def create_dummy_model(symbol: str, window_minutes: int):
    """Crea y guarda un modelo sintético para un activo y ventana específicos."""
    logger.info(f"Generando datos sintéticos para {symbol} ({window_minutes}m)...")
    np.random.seed(hash(f"{symbol}_{window_minutes}") % (2**32 - 1)) # Semilla única por modelo
    
    n_samples = 5000
    n_features = 10
    X_train = np.random.randn(n_samples, n_features)
    
    # Cada modelo tiene una "personalidad" matemática ligeramente diferente
    w_ofi = 0.5 + (np.random.rand() * 0.2)
    w_vwap = 0.3 + (np.random.rand() * 0.2)
    
    logits = w_ofi * X_train[:, 0] - w_vwap * X_train[:, 3] + np.random.randn(n_samples) * 0.5
    probs = 1 / (1 + np.exp(-logits))
    y_train = (probs > 0.5).astype(int)
    
    pipeline = Pipeline([
        ('scaler', StandardScaler()),
        ('model', CalibratedClassifierCV(
            LogisticRegression(C=0.1, max_iter=1000, class_weight='balanced'),
            method='sigmoid', cv=3
        ))
    ])
    
    pipeline.fit(X_train, y_train)
    
    os.makedirs("models", exist_ok=True)
    model_path = f"models/ofi_model_{symbol.lower()}_{window_minutes}m.pkl"
    joblib.dump(pipeline, model_path)
    logger.success(f"Modelo guardado en: {model_path}")

if __name__ == "__main__":
    # Generar modelos para la nueva arquitectura multi-mercado
    assets = ["btcusdt", "xrpusdt", "solusdt", "ethusdt"]
    windows = [5, 15, 60]
    
    for asset in assets:
        for w in windows:
            create_dummy_model(asset, w)
