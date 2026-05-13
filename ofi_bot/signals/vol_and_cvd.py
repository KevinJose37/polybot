import collections
import time
import numpy as np
from utils.schemas import TradeEvent

class VolAndCVDCalculator:
    """
    Agrupa el cálculo de métricas derivadas del volumen y el precio en el tiempo:
    - CVD (Cumulative Volume Delta)
    - ATR (Average True Range aproximado por ticks/segundos)
    - RSI y Momentum (Retorno de precios)
    """

    def __init__(self, max_window_seconds: int = 3600):
        self.max_window_ms = max_window_seconds * 1000
        # deque[(timestamp_ms, price, qty, is_buyer_maker)]
        self.trades = collections.deque()
        
        self.cvd_total = 0.0

    def update(self, trade: TradeEvent) -> tuple[float, float, float, float]:
        """
        Retorna: (cvd_norm, atr_pct, rsi_14, price_momentum_1m)
        """
        now_ms = time.time_ns() // 1_000_000
        
        # Actualizar CVD
        # Si is_buyer_maker=True -> Venta a mercado -> presión vendedora -> restar
        # Si is_buyer_maker=False -> Compra a mercado -> presión compradora -> sumar
        delta_v = -trade.quantity if trade.is_buyer_maker else trade.quantity
        self.cvd_total += delta_v
        
        self.trades.append((now_ms, trade.price, trade.quantity, trade.is_buyer_maker))
        
        # Purgar
        cutoff_max = now_ms - self.max_window_ms
        while self.trades and self.trades[0][0] < cutoff_max:
            t_ts, t_p, t_q, t_m = self.trades.popleft()
            # Restamos del cvd global lo que se purga
            p_delta = -t_q if t_m else t_q
            self.cvd_total -= p_delta

        # Cálculos de ventanas
        cutoff_60s = now_ms - 60000
        
        # 1. Price Momentum 1 minuto (%)
        price_60s_ago = trade.price
        for t_ts, t_p, t_q, t_m in self.trades:
            if t_ts >= cutoff_60s:
                price_60s_ago = t_p
                break
        
        momentum_1m = ((trade.price - price_60s_ago) / price_60s_ago) * 100 if price_60s_ago > 0 else 0.0
        
        # 2. Aproximación rápida de ATR y RSI en los últimos trades
        # Para alta frecuencia, calculamos ATR del último bloque de 1 minuto
        high = trade.price
        low = trade.price
        
        gains = 0.0
        losses = 0.0
        prev_p = None
        
        for t_ts, t_p, _, _ in reversed(self.trades):
            if t_ts < cutoff_60s:
                break
            if t_p > high: high = t_p
            if t_p < low: low = t_p
            
            if prev_p is not None:
                diff = prev_p - t_p # iterando hacia atrás
                if diff > 0:
                    gains += diff
                else:
                    losses -= diff
            prev_p = t_p
            
        true_range = high - low
        atr_pct = (true_range / trade.price) * 100 if trade.price > 0 else 0.0
        
        rs = (gains / losses) if losses > 0 else 100.0
        rsi = 100.0 - (100.0 / (1.0 + rs)) if losses > 0 else 100.0
        if gains == 0 and losses == 0:
            rsi = 50.0

        # CVD Normalizado por volatilidad (CVD / true_range proxy)
        # Si true_range es 0, usamos el precio * 0.0001
        denominator = true_range if true_range > 0 else (trade.price * 0.0001)
        cvd_norm = self.cvd_total / denominator

        return cvd_norm, atr_pct, rsi, momentum_1m
