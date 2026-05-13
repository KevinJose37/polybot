import os
import joblib
import numpy as np
from loguru import logger
from utils.schemas import FeatureVector

class ProbabilityModel:
    """
    Wrapper para el modelo de Machine Learning (LogisticRegression).
    Carga el modelo pre-entrenado (.pkl) y evalúa el FeatureVector en tiempo real
    para determinar la probabilidad matemática de que el mercado suba (UP).
    """

    def __init__(self):
        self.models_dir = "models"
        self.pipelines = {}
        
    def _load_model(self, symbol: str, window_minutes: int):
        """Carga el pipeline si no está en memoria."""
        model_key = f"{symbol}_{window_minutes}"
        if model_key in self.pipelines:
            return
            
        model_path = os.path.join(self.models_dir, f"ofi_model_{symbol.lower()}_{window_minutes}m.pkl")
        
        if not os.path.exists(model_path):
            logger.warning(f"No se encontró el modelo en {model_path}. Usando fallback.")
            self.pipelines[model_key] = None
            return
            
        try:
            self.pipelines[model_key] = joblib.load(model_path)
            logger.debug(f"Modelo cargado: {model_key}")
        except Exception as e:
            logger.error(f"Error cargando el modelo {model_key}: {e}")
            self.pipelines[model_key] = None

    def predict_proba(self, features: FeatureVector, symbol: str, window_minutes: int) -> float:
        """
        Toma el vector de características y retorna la probabilidad de clase 1 (UP)
        para el activo y ventana temporal especificados.
        """
        model_key = f"{symbol}_{window_minutes}"
        self._load_model(symbol, window_minutes)
        
        pipeline = self.pipelines.get(model_key)
        
        if not pipeline:
            return 0.5
            
        try:
            scaler = pipeline.named_steps.get('scaler')
            expected_features = getattr(scaler, 'n_features_in_', 10) if scaler else 10
            
            if expected_features == 5:
                # El modelo guardado fue entrenado con el trainer.py básico (5 features)
                # [ofi_zscore, vwap_dev_bps, book_imbalance, spread_bps, trade_count_1m]
                x_array = np.array([[
                    features.ofi_zscore,
                    features.vwap_dev_bps,
                    features.bid_ask_ratio,
                    features.spread_bps,
                    50.0 # Valor nominal para trade_count
                ]])
            else:
                # Modelo completo de 10 features
                x_array = np.array(features.to_numpy()).reshape(1, -1)
                
            p_up = pipeline.predict_proba(x_array)[0][1]
            return float(p_up)
            
        except Exception as e:
            logger.error(f"Error durante predicción ML para {model_key}: {e}")
            return 0.5
