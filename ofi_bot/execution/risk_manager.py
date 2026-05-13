from loguru import logger
from config.settings import config
from utils.schemas import BetDecision

class RiskManager:
    """
    Guardián final de la ejecución de órdenes.
    Evita violaciones a los límites globales como el máximo de posiciones
    abiertas o el drawdown diario.
    """

    def __init__(self):
        self.max_open_positions = config.max_open_positions
        self.daily_drawdown_limit = config.daily_drawdown_limit
        
        # En memoria temporal, debería ser leído de BD en el futuro
        self.current_open_positions = 0
        self.current_daily_drawdown = 0.0

    def can_place_order(self, decision: BetDecision) -> bool:
        """
        Evalúa si el estado global permite enviar esta orden de compra.
        """
        if not decision.should_bet:
            return False
            
        if self.current_open_positions >= self.max_open_positions:
            logger.warning(f"RiskManager: Límite de posiciones abiertas alcanzado ({self.max_open_positions}). Orden denegada.")
            return False
            
        if self.current_daily_drawdown >= self.daily_drawdown_limit:
            logger.error(f"RiskManager: CIRCUIT BREAKER. Drawdown diario límite alcanzado ({self.daily_drawdown_limit * 100:.1f}%). Bot detenido temporalmente.")
            return False
            
        return True
        
    def record_new_position(self):
        """Registra internamente que se abrió una posición."""
        self.current_open_positions += 1
        
    def resolve_position(self, pnl: float, bankroll: float):
        """
        Libera el espacio y actualiza el drawdown.
        Si pnl es negativo, actualiza el drawdown.
        """
        self.current_open_positions = max(0, self.current_open_positions - 1)
        if pnl < 0:
            # Cálculo muy simple de impacto en drawdown
            self.current_daily_drawdown += abs(pnl) / bankroll
