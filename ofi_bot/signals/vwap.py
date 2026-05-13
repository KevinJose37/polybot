import collections
import time
from typing import Tuple
from loguru import logger
from utils.schemas import TradeEvent

class VWAPCalculator:
    """
    Calcula el Volume-Weighted Average Price (VWAP) en una ventana temporal móvil.
    Mantiene un buffer de trades y los purga según el tiempo local.
    """

    def __init__(self, max_window_seconds: int = 3600):
        # max_window_seconds define el límite de tiempo que retenemos en RAM.
        # Por defecto 3600 (1 hora) para soportar mercados largos.
        self.max_window_ms = max_window_seconds * 1000
        
        # Buffer de trades: deque[(timestamp_ms, price, qty)]
        self.trades = collections.deque()
        
        # Mantenemos las sumatorias globales para O(1) de cálculo
        self.sum_price_vol = 0.0
        self.sum_vol = 0.0

    def update(self, trade: TradeEvent, target_window_seconds: int = 300) -> float:
        """
        Ingresa un nuevo trade, purga los viejos y retorna el VWAP.
        Se puede consultar el VWAP para una ventana específica (ej. 5 minutos).
        Retorna la desviación del último precio respecto al VWAP en basis points (bps).
        """
        now_ms = time.time_ns() // 1_000_000
        
        # 1. Agregar nuevo trade
        self.trades.append((now_ms, trade.price, trade.quantity))
        self.sum_price_vol += (trade.price * trade.quantity)
        self.sum_vol += trade.quantity
        
        # 2. Purgar trades fuera del max_window (limpieza de RAM)
        cutoff_max = now_ms - self.max_window_ms
        while self.trades and self.trades[0][0] < cutoff_max:
            t_ts, t_p, t_q = self.trades.popleft()
            self.sum_price_vol -= (t_p * t_q)
            self.sum_vol -= t_q

        # 3. Calcular VWAP para la ventana específica
        # Si la target_window es igual a la max_window, usamos O(1)
        target_window_ms = target_window_seconds * 1000
        
        if target_window_ms >= self.max_window_ms:
            # Caso óptimo: usamos las sumas precalculadas
            vwap = self.sum_price_vol / self.sum_vol if self.sum_vol > 0 else trade.price
        else:
            # Calcular iterando hacia atrás (subóptimo pero necesario para ventanas variables)
            cutoff_target = now_ms - target_window_ms
            temp_sum_pv = 0.0
            temp_sum_v = 0.0
            for t_ts, t_p, t_q in reversed(self.trades):
                if t_ts < cutoff_target:
                    break
                temp_sum_pv += (t_p * t_q)
                temp_sum_v += t_q
            vwap = temp_sum_pv / temp_sum_v if temp_sum_v > 0 else trade.price

        # Desvío en bps: (Precio actual - VWAP) / VWAP * 10000
        if vwap > 0:
            vwap_dev_bps = ((trade.price - vwap) / vwap) * 10000
        else:
            vwap_dev_bps = 0.0

        return vwap_dev_bps
