import collections
import time
import numpy as np
from typing import List, Tuple
from loguru import logger
from utils.schemas import OrderBookSnapshot, OFIFeatures

class OFICalculator:
    """
    Calcula el Order Flow Imbalance (OFI) en tiempo real.
    Compara snapshots consecutivos para derivar la presión compradora/vendedora.
    Mantiene buffers para normalización (z-score) y promedios móviles.
    """

    def __init__(self, windows_seconds: List[int] = [10, 30, 60], zscore_window: int = 300):
        self.windows_seconds = windows_seconds
        self.zscore_window = zscore_window
        
        self.prev_snapshot: OrderBookSnapshot = None
        
        # Buffer de valores raw OFI con timestamp: deque[(timestamp_ms, ofi_raw)]
        self.ofi_history = collections.deque()
        
        # Buffer para normalización z-score: guardamos los ofi_normalized
        self.ofi_norm_buffer = collections.deque(maxlen=zscore_window)

    def update(self, snapshot: OrderBookSnapshot) -> OFIFeatures:
        """Procesa un nuevo snapshot y retorna las features actualizadas."""
        if not self.prev_snapshot:
            self.prev_snapshot = snapshot
            # Estado inicial neutral
            return self._build_empty_features(snapshot.timestamp_ms)

        ofi_raw, bid_total, ask_total = self._calculate_raw_ofi(self.prev_snapshot, snapshot)
        
        # Normalización por liquidez total del nivel de profundidad analizado
        total_size = bid_total + ask_total
        ofi_normalized = ofi_raw / total_size if total_size > 0 else 0.0
        
        # Actualizar buffers
        self.ofi_history.append((snapshot.timestamp_ms, ofi_raw))
        self.ofi_norm_buffer.append(ofi_normalized)
        
        # Limpiar historia vieja usando el reloj local de la PC (como pidió el usuario)
        now_ms = time.time_ns() // 1_000_000
        max_window_ms = max(self.windows_seconds) * 1000
        cutoff_time = now_ms - max_window_ms
        
        while self.ofi_history and self.ofi_history[0][0] < cutoff_time:
            self.ofi_history.popleft()

        # Calcular Z-Score
        if len(self.ofi_norm_buffer) > 10:
            arr = np.array(self.ofi_norm_buffer)
            mean = np.mean(arr)
            std = np.std(arr)
            ofi_zscore = (ofi_normalized - mean) / std if std > 1e-9 else 0.0
        else:
            ofi_zscore = 0.0

        # Calcular acumulados
        ofi_10s = self._sum_window(now_ms, 10 * 1000) / total_size if total_size > 0 else 0.0
        ofi_30s = self._sum_window(now_ms, 30 * 1000) / total_size if total_size > 0 else 0.0
        ofi_60s = self._sum_window(now_ms, 60 * 1000) / total_size if total_size > 0 else 0.0

        # Bid/Ask ratio estático y spread
        bid_ask_ratio = bid_total / ask_total if ask_total > 0 else 1.0
        best_bid = snapshot.bids[0].price if snapshot.bids else 0.0
        best_ask = snapshot.asks[0].price if snapshot.asks else 0.0
        
        mid_price = snapshot.mid_price
        spread_bps = ((best_ask - best_bid) / mid_price * 10000) if mid_price > 0 else 0.0

        self.prev_snapshot = snapshot

        return OFIFeatures(
            timestamp_ms=now_ms,
            ofi_raw=ofi_raw,
            ofi_normalized=ofi_normalized,
            ofi_zscore=ofi_zscore,
            ofi_10s=ofi_10s,
            ofi_30s=ofi_30s,
            ofi_60s=ofi_60s,
            bid_ask_ratio=bid_ask_ratio,
            spread_bps=spread_bps
        )

    def _calculate_raw_ofi(self, prev: OrderBookSnapshot, curr: OrderBookSnapshot) -> Tuple[float, float, float]:
        """Calcula los deltas del libro nivel por nivel."""
        ofi_raw = 0.0
        bid_total = 0.0
        ask_total = 0.0
        
        # Asumimos que prev.bids y curr.bids tienen la misma longitud máxima (depth 20)
        depth = min(len(curr.bids), len(prev.bids), len(curr.asks), len(prev.asks))
        
        for i in range(depth):
            # Lógica de Bids
            c_bid_p, c_bid_s = curr.bids[i].price, curr.bids[i].size
            p_bid_p, p_bid_s = prev.bids[i].price, prev.bids[i].size
            
            if c_bid_p == p_bid_p:
                bid_delta = c_bid_s - p_bid_s
            elif c_bid_p > p_bid_p:
                bid_delta = c_bid_s
            else:
                bid_delta = -p_bid_s  # Corrección a la fórmula original para ser simétricos
                
            # Lógica de Asks
            c_ask_p, c_ask_s = curr.asks[i].price, curr.asks[i].size
            p_ask_p, p_ask_s = prev.asks[i].price, prev.asks[i].size
            
            if c_ask_p == p_ask_p:
                ask_delta = c_ask_s - p_ask_s
            elif c_ask_p < p_ask_p:
                ask_delta = c_ask_s
            else:
                ask_delta = -p_ask_s
                
            ofi_raw += (bid_delta - ask_delta)
            bid_total += c_bid_s
            ask_total += c_ask_s
            
        return ofi_raw, bid_total, ask_total

    def _sum_window(self, now_ms: int, window_ms: int) -> float:
        cutoff = now_ms - window_ms
        total = 0.0
        for ts, val in reversed(self.ofi_history):
            if ts < cutoff:
                break
            total += val
        return total

    def _build_empty_features(self, ts: int) -> OFIFeatures:
        return OFIFeatures(ts, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
