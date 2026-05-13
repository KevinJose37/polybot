from loguru import logger
from config.settings import config
from utils.schemas import BetDecision

class KellySizer:
    """
    Calculador de gestión de riesgo basado en el Kelly Criterion.
    Evalúa automáticamente las posiciones YES (subida) y NO (bajada), 
    buscando si existe alguna ventaja estadística superior al mínimo exigido.
    """

    def __init__(self):
        self.fraction = config.kelly_fraction
        self.min_edge = config.min_edge
        self.max_bet_pct = config.max_bet_pct
        self.max_bet_usdc = config.max_bet_usdc

    def evaluate_bet(self, p_model: float, yes_price: float, bankroll: float) -> BetDecision:
        """
        Toma la P(UP) del modelo y las odds del mercado, y decide si es seguro
        y matemáticamente rentable apostar, y cuánto apostar.
        
        Args:
            p_model: Probabilidad de subida dictada por el ML (0.0 a 1.0)
            yes_price: Precio actual del token YES en Polymarket (0.0 a 1.0)
            bankroll: USDC disponibles en la billetera
        """
        # --- 1. Evaluar oportunidad en posición YES (Apostar a que sube) ---
        p_market_yes = yes_price
        edge_yes = p_model - p_market_yes
        
        if edge_yes >= self.min_edge:
            return self._calculate_kelly(
                direction="YES",
                p=p_model,
                p_market=p_market_yes,
                edge=edge_yes,
                bankroll=bankroll
            )

        # --- 2. Evaluar oportunidad en posición NO (Apostar a que baja) ---
        # Si el modelo dice P(UP) = 0.20, entonces P(DOWN) = 0.80
        p_model_no = 1.0 - p_model
        # Si el token YES cuesta 0.60, el token NO implícitamente cuesta 0.40
        p_market_no = 1.0 - yes_price 
        edge_no = p_model_no - p_market_no

        if edge_no >= self.min_edge:
            return self._calculate_kelly(
                direction="NO",
                p=p_model_no,
                p_market=p_market_no,
                edge=edge_no,
                bankroll=bankroll
            )

        # --- 3. Ninguna dirección tiene el edge mínimo ---
        return BetDecision(
            should_bet=False,
            direction=None,
            amount_usdc=0.0,
            edge=max(edge_yes, edge_no),
            f_kelly=0.0,
            p_model=p_model,
            p_market=yes_price,
            reasoning=f"Edge insuficiente. YES_Edge: {edge_yes:.3f}, NO_Edge: {edge_no:.3f} (Min: {self.min_edge})"
        )

    def _calculate_kelly(self, direction: str, p: float, p_market: float, edge: float, bankroll: float) -> BetDecision:
        """
        Aplica la fórmula matemática exacta de Kelly.
        f_kelly = p - (q / b)
        donde b es el pago neto esperado por dólar apostado.
        """
        # Calcular el 'b' (odds decimales - 1)
        # Si p_market = 0.50, b = (1 - 0.50)/0.50 = 1.0 (ganas 1 por cada 1 apostado)
        if p_market <= 0 or p_market >= 1:
            return BetDecision(False, direction, 0, edge, 0, p, p_market, "Precio de mercado inválido")

        b = (1.0 - p_market) / p_market
        q = 1.0 - p
        
        # Fórmula pura
        f_kelly_pure = p - (q / b)
        
        # Si Kelly arroja negativo o 0, la ventaja matemática desaparece al incluir riesgo
        if f_kelly_pure <= 0:
            return BetDecision(
                should_bet=False, direction=direction, amount_usdc=0, edge=edge, 
                f_kelly=f_kelly_pure, p_model=p, p_market=p_market,
                reasoning=f"Kelly negativo ({f_kelly_pure:.3f}). Riesgo no justificable."
            )

        # Aplicar fracción de Kelly (conservadurismo para no quebrar por varianza)
        f_kelly_real = f_kelly_pure * self.fraction
        
        # Limitar al máximo porcentaje de bankroll por apuesta (ej. 5%)
        f_final = min(f_kelly_real, self.max_bet_pct)
        
        amount_to_bet = bankroll * f_final

        # Limitar al tope absoluto en USDC dictado por config
        if amount_to_bet > self.max_bet_usdc:
            amount_to_bet = self.max_bet_usdc

        # La mínima orden aceptable en Polymarket suele ser $1.00
        if amount_to_bet < 1.0:
            return BetDecision(
                should_bet=False, direction=direction, amount_usdc=amount_to_bet, edge=edge, 
                f_kelly=f_kelly_pure, p_model=p, p_market=p_market,
                reasoning=f"Apuesta sugerida ({amount_to_bet:.2f} USDC) menor al límite de $1.00"
            )

        return BetDecision(
            should_bet=True,
            direction=direction,
            amount_usdc=amount_to_bet,
            edge=edge,
            f_kelly=f_kelly_pure,
            p_model=p,
            p_market=p_market,
            reasoning=f"Edge válido ({edge:.3f}). Kelly Puro: {f_kelly_pure:.3f}, Fraction: {self.fraction}. Tamaño final: {f_final*100:.1f}% del bank"
        )
