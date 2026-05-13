import time
from loguru import logger
from utils.schemas import OrderBookSnapshot, TradeEvent, FeatureVector
from signals.ofi import OFICalculator
from signals.vwap import VWAPCalculator
from signals.vol_and_cvd import VolAndCVDCalculator

class SignalAggregator:
    """
    Director de Orquesta.
    Recibe los eventos crudos (Snapshots y Trades) y se los pasa a los calculadores.
    Mantiene el estado global de las variables matemáticas y es capaz de ensamblar
    el FeatureVector final a demanda.
    """

    def __init__(self, vwap_window_seconds: int = 300):
        self.ofi_calc = OFICalculator()
        
        # Le damos un buffer grande (3600s) por si queremos ventanas largas, 
        # pero la target_window se definirá por defecto en vwap_window_seconds
        self.vwap_calc = VWAPCalculator(max_window_seconds=3600)
        self.vol_cvd_calc = VolAndCVDCalculator(max_window_seconds=3600)
        
        self.vwap_window_seconds = vwap_window_seconds

        # Estado interno actual
        self.last_ofi_features = None
        self.last_vwap_dev_bps = 0.0
        self.last_cvd_norm = 0.0
        self.last_atr_pct = 0.0
        self.last_rsi = 50.0
        self.last_momentum = 0.0

    def process_orderbook(self, snapshot: OrderBookSnapshot):
        """Actualiza el calculador OFI con el nuevo snapshot del book."""
        self.last_ofi_features = self.ofi_calc.update(snapshot)

    def process_trade(self, trade: TradeEvent):
        """Actualiza los calculadores basados en volumen con el nuevo trade."""
        self.last_vwap_dev_bps = self.vwap_calc.update(trade, target_window_seconds=self.vwap_window_seconds)
        
        cvd, atr, rsi, mom = self.vol_cvd_calc.update(trade)
        self.last_cvd_norm = cvd
        self.last_atr_pct = atr
        self.last_rsi = rsi
        self.last_momentum = mom

    def get_feature_vector(self) -> FeatureVector | None:
        """
        Ensambla y retorna el FeatureVector completo normalizado.
        Retorna None si el OFI aún no ha recibido suficientes datos para inicializarse.
        """
        if not self.last_ofi_features:
            return None

        now_ms = time.time_ns() // 1_000_000

        return FeatureVector(
            timestamp_ms=now_ms,
            ofi_zscore=self.last_ofi_features.ofi_zscore,
            ofi_10s=self.last_ofi_features.ofi_10s,
            ofi_60s=self.last_ofi_features.ofi_60s,
            vwap_dev_bps=self.last_vwap_dev_bps,
            cvd_norm=self.last_cvd_norm,
            bid_ask_ratio=self.last_ofi_features.bid_ask_ratio,
            spread_bps=self.last_ofi_features.spread_bps,
            atr_pct=self.last_atr_pct,
            rsi_14=self.last_rsi,
            price_momentum_1m=self.last_momentum
        )
