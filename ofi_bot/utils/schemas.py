from dataclasses import dataclass, field
from typing import Literal

@dataclass
class OrderBookLevel:
    """
    Representa un único nivel de precio y cantidad dentro del Order Book.
    - price: Precio del nivel.
    - size: Cantidad disponible a ese precio.
    """
    price: float
    size: float

@dataclass
class OrderBookSnapshot:
    """
    Representa una fotografía (snapshot) del Order Book en un momento dado.
    Se utiliza para calcular la presión compradora/vendedora en el cálculo del OFI.
    """
    symbol: str                          # Par de trading (e.g., "BTCUSDT")
    timestamp_ms: int                    # Timestamp local en milisegundos para control de latencia.
    bids: list[OrderBookLevel]           # Lista de niveles de compra. Ordenados de mayor a menor precio.
    asks: list[OrderBookLevel]           # Lista de niveles de venta. Ordenados de menor a mayor precio.
    mid_price: float = field(init=False) # Precio medio calculado automáticamente post-inicialización.

    def __post_init__(self):
        """Calcula el mid_price automáticamente a partir del mejor bid y ask."""
        if self.bids and self.asks:
            self.mid_price = (self.bids[0].price + self.asks[0].price) / 2
        else:
            self.mid_price = 0.0

@dataclass
class TradeEvent:
    """
    Representa un trade (operación) ejecutada en el exchange (Binance).
    Es esencial para calcular el VWAP Deviation y el Cumulative Volume Delta (CVD).
    """
    symbol: str             # Par de trading (e.g., "BTCUSDT")
    timestamp_ms: int       # Timestamp local en ms
    price: float            # Precio de ejecución del trade
    quantity: float         # Cantidad de la moneda operada
    is_buyer_maker: bool    # Si True, indica que la orden fue "sell-initiated" (presión vendedora). Si False, "buy-initiated".

@dataclass
class OFIFeatures:
    """
    Contiene las características matemáticas calculadas a partir del Order Flow Imbalance.
    Esto se calcula de forma continua alimentándose de los snapshots del book.
    """
    timestamp_ms: int       # Momento de cálculo
    ofi_raw: float          # OFI crudo sin procesar
    ofi_normalized: float   # OFI normalizado por el volumen total del orderbook
    ofi_zscore: float       # Desviación estándar (Z-score) del OFI
    ofi_10s: float          # OFI acumulado de los últimos 10 segundos
    ofi_30s: float          # OFI acumulado de los últimos 30 segundos
    ofi_60s: float          # OFI acumulado de los últimos 60 segundos
    bid_ask_ratio: float    # Ratio entre volumen de bids y asks
    spread_bps: float       # Diferencia (spread) entre mejor ask y mejor bid en puntos base (bps)

@dataclass
class FeatureVector:
    """
    Vector final consolidado de características que se pasará al modelo de ML
    (Regresión Logística). Todos sus campos deben estar normalizados para evitar
    sesgos en el modelo.
    """
    timestamp_ms: int           # Momento de consolidación de features
    ofi_zscore: float           # Señal principal normalizada
    ofi_10s: float              # Presión a corto plazo (10s)
    ofi_60s: float              # Presión a mediano plazo (60s)
    vwap_dev_bps: float         # Desviación actual contra el VWAP en bps
    cvd_norm: float             # Delta de volumen normalizado por la volatilidad (ATR)
    bid_ask_ratio: float        # Imbalance estático del libro
    spread_bps: float           # Liquidez / costo
    atr_pct: float              # Volatilidad en el momento como porcentaje del precio
    rsi_14: float               # Índice de fuerza relativa a corto plazo
    price_momentum_1m: float    # Retorno porcentual de 1 minuto: float

    def to_numpy(self) -> list[float]:
        """Retorna features en el orden exacto que espera el modelo."""
        return [
            self.ofi_zscore, self.ofi_10s, self.ofi_60s,
            self.vwap_dev_bps, self.cvd_norm, self.bid_ask_ratio,
            self.spread_bps, self.atr_pct, self.rsi_14,
            self.price_momentum_1m
        ]

@dataclass
class MarketOdds:
    """
    Representa el estado actual de un mercado en Polymarket.
    Contiene la probabilidad de que ocurra el evento implicado por el precio de los tokens.
    """
    market_id: str          # Identificador del mercado en Polymarket
    token_id_yes: str       # Token para la posición "A favor"
    token_id_no: str        # Token para la posición "En contra"
    yes_price: float        # Probabilidad (P(UP)) implícita del mercado. Rango [0, 1]
    bid_yes: float          # Mejor oferta de compra para YES
    ask_yes: float          # Mejor oferta de venta para YES
    closes_at_utc: float    # Unix timestamp de cierre, necesario para filtrar apuestas fuera de tiempo

@dataclass
class BetDecision:
    """
    La decisión de inversión generada por el cruce del Modelo ML con el Motor de Sizing.
    Determina si operar y qué tamaño (Kelly).
    """
    should_bet: bool                    # Indica si se cumplen los criterios para apostar
    direction: Literal["YES", "NO"] | None # Dirección del trade (comprar YES o NO)
    amount_usdc: float                  # Cantidad a apostar en USDC
    edge: float                         # Ventaja matemática detectada (p_model - p_market)
    f_kelly: float                      # Fracción de Kelly pura (riesgo recomendado)
    p_model: float                      # Probabilidad P(UP) predicha por el modelo
    p_market: float                     # Probabilidad implícita en Polymarket
    reasoning: str                      # Justificación textual para logs

@dataclass
class BetReceipt:
    """
    Confirmación o recibo luego de que una orden se haya enviado a Polymarket.
    Sirve para registro en BD y control de posiciones.
    """
    tx_hash: str                                    # Hash de la transacción en Polygon
    amount_usdc: float                              # Total invertido
    price_filled: float                             # Precio real de ejecución (puede diferir por slippage)
    status: Literal["filled", "rejected", "partial"]# Estado final de la orden FOK
    timestamp_ms: int                               # Cuándo se confirmó
