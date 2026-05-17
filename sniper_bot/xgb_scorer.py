"""
sniper_bot/xgb_scorer.py — XGBoost model loader and inference wrapper.

Loads trained models per asset from disk and provides a single-call scoring API.
Returns predict_proba[:,1] (probability of UP/positive class) as the score.

IMPORTANT: Feature names are loaded from model_features.json (written by
train_asset_models.py) to guarantee train/serve alignment.
"""
import json
import os
import logging
import numpy as np

logger = logging.getLogger("sniper_bot.xgb_scorer")

MODELS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "ml_models"
)

def _load_feature_names(models_dir: str) -> list[str]:
    """Load feature names from metadata written during training."""
    meta_path = os.path.join(models_dir, "model_features.json")
    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            meta = json.load(f)
            names = meta.get("feature_names", [])
            if names:
                return names
    # Fallback — must match what train_asset_models.py uses
    logger.warning("model_features.json not found, using hardcoded fallback")
    return ['spread', 'tick_rate', 'ret_short']

class XGBScorer:
    """
    Thin wrapper around the trained XGBoost models.

    Usage:
        scorer = XGBScorer()
        features = accumulator.get_features(token_id)
        score = scorer.predict(features, asset="BTC")  # 0.0 to 1.0 or None
    """

    def __init__(self, models_dir: str | None = None):
        self.models = {}
        self._models_dir = models_dir or MODELS_DIR
        self._predict_count = 0
        self.feature_names = _load_feature_names(self._models_dir)
        self._load_models()

    def _load_models(self) -> None:
        """Load XGBoost models from disk."""
        try:
            import xgboost as xgb
        except ImportError:
            logger.error("xgboost not installed. Run: pip install xgboost")
            return

        for asset in ['BTC', 'ETH', 'SOL', 'XRP']:
            model_path = os.path.join(self._models_dir, f"xgb_{asset.lower()}.json")
            if not os.path.exists(model_path):
                logger.warning(f"Model not found for {asset} at {model_path}")
                continue

            try:
                model = xgb.XGBClassifier()
                model.load_model(model_path)
                self.models[asset] = model
                logger.info(f"XGBoost model loaded for {asset} from {model_path}")
            except Exception as e:
                logger.error(f"Failed to load XGBoost model for {asset}: {e}")

        logger.info(f"XGBoost scorer ready: {list(self.models.keys())} | features={self.feature_names}")

    @property
    def is_loaded(self) -> bool:
        return len(self.models) > 0

    def predict(self, features: dict, asset: str) -> float | None:
        """
        Run inference on a feature dict for a specific asset.
        Returns probability of positive class (0.0 to 1.0).
        Returns None if model not loaded for asset or features invalid.
        """
        if asset not in self.models:
            return None

        try:
            # Build feature vector in exact training order using self.feature_names
            X = np.array([[features.get(f, 0.0) for f in self.feature_names]])

            # predict_proba returns [[prob_class_0, prob_class_1]]
            proba = self.models[asset].predict_proba(X)
            score = float(proba[0, 1])
            self._predict_count += 1
            return round(score, 4)
        except Exception as e:
            logger.error("XGB prediction error for %s: %s", asset, e)
            return None

    def metrics(self) -> dict:
        """Metrics for dashboard."""
        return {
            "loaded_assets": list(self.models.keys()),
            "models_dir": self._models_dir,
            "predictions": self._predict_count,
            "feature_names": self.feature_names,
        }
