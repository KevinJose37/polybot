"""
PnL and Sharpe tracking.
"""
import numpy as np
from typing import List

class PnLTracker:
    def __init__(self):
        self.history: List[float] = []

    def record_pnl(self, pnl: float) -> None:
        self.history.append(pnl)

    def calculate_sharpe(self) -> float:
        """
        Calculate simple Sharpe ratio over history.
        Assumes risk-free rate is 0 for simplicity in high-frequency.
        """
        if len(self.history) < 2:
            return 0.0
            
        mean = np.mean(self.history)
        std = np.std(self.history)
        
        if std == 0:
            return 0.0
            
        return float(mean / std * np.sqrt(252 * 288)) # Annualized roughly (288 5-min intervals)
