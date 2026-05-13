# Order Flow Imbalance (OFI) — Bot de Apuestas HFT en Polymarket

> **Propósito de este documento:** Especificación técnica completa para que un LLM desarrolle el código de producción de un bot de apuestas de alta frecuencia en Polymarket usando la estrategia de Order Flow Imbalance. Incluye arquitectura, fórmulas, pseudocódigo, contratos de datos y checklist de implementación.

---

## Tabla de contenidos

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Arquitectura del sistema](#2-arquitectura-del-sistema)
3. [Capa de datos — feeds en tiempo real](#3-capa-de-datos--feeds-en-tiempo-real)
4. [Motor de señales — cálculo de OFI](#4-motor-de-señales--cálculo-de-ofi)
5. [Modelo de probabilidad](#5-modelo-de-probabilidad)
6. [Motor de sizing — Kelly Criterion](#6-motor-de-sizing--kelly-criterion)
7. [Integración con Polymarket](#7-integración-con-polymarket)
8. [Gestor de riesgo](#8-gestor-de-riesgo)
9. [Backtesting y calibración](#9-backtesting-y-calibración)
10. [Estructura de archivos del proyecto](#10-estructura-de-archivos-del-proyecto)
11. [Contratos de datos (schemas)](#11-contratos-de-datos-schemas)
12. [Variables de entorno y configuración](#12-variables-de-entorno-y-configuración)
13. [Checklist de implementación](#13-checklist-de-implementación)
14. [Errores comunes y cómo evitarlos](#14-errores-comunes-y-cómo-evitarlos)

---

## 1. Resumen ejecutivo

### ¿Qué hace este bot?

Conecta a feeds en tiempo real de exchanges (Binance, Coinbase) para calcular el **Order Flow Imbalance (OFI)** del order book del activo subyacente (BTC, SOL, ETH). Usa esa señal para estimar la probabilidad de que el precio suba o baje en la siguiente ventana de tiempo (5min, 15min). Compara esa estimación contra las odds actuales en Polymarket. Si detecta un **edge** (diferencia ≥ umbral mínimo), apuesta el tamaño óptimo calculado por Kelly Criterion.

### Stack tecnológico

| Componente | Tecnología recomendada |
|---|---|
| Lenguaje | Python 3.11+ |
| Feeds WebSocket | `websockets`, `ccxt` |
| Procesamiento numérico | `numpy`, `pandas`, `numba` |
| Modelo ML | `scikit-learn` (LogisticRegression) |
| Calibración | `scikit-learn` CalibratedClassifierCV |
| Polymarket API | `py-clob-client` (gamma-api oficial) |
| Base de datos | `sqlite3` (dev), `timescaledb` (prod) |
| Async runtime | `asyncio`, `aiohttp` |
| Scheduler | `apscheduler` |
| Logging | `loguru` |
| Config | `pydantic-settings`, `.env` |
| Tests | `pytest`, `pytest-asyncio` |

### Mercados objetivo

```
BTC-USD-5MIN-UP   → "¿BTC sube en los próximos 5 minutos?"
SOL-USD-15MIN-UP  → "¿SOL sube en los próximos 15 minutos?"
ETH-USD-5MIN-UP   → "¿ETH sube en los próximos 5 minutos?"
Cualquier otro mercado de criptos que tenga una ventana de tiempo de 5 o 15 minutos. Incluso evalúa si incluimos de 1 hora también.
```

---

## 2. Arquitectura del sistema

### Diagrama de flujo

```
┌─────────────────────────────────────────────────────────────┐
│                      DATA LAYER                             │
│  Binance WS (depth20@100ms) ──┐                             │
│  Coinbase WS (level2)   ──────┼──► OrderBookAggregator      │
│  Binance WS (trade)     ──────┘        │                    │
└────────────────────────────────────────┼────────────────────┘
                                         │ RawOrderBook (10ms)
┌────────────────────────────────────────▼────────────────────┐
│                    SIGNAL ENGINE                             │
│  OFICalculator ──► NormalizedOFI                            │
│  VWAPCalculator ──► VWAPDeviation                           │
│  ATRCalculator  ──► VolatilityFilter                        │
│  CVDCalculator  ──► CumVolumeDelta                          │
└────────────────────────────────────────┬────────────────────┘
                                         │ FeatureVector (1s)
┌────────────────────────────────────────▼────────────────────┐
│                  PROBABILITY MODEL                           │
│  LogisticRegression (calibrada con Platt Scaling)           │
│  Input: [ofi_norm, vwap_dev, cvd_norm, atr_pct, rsi_14]    │
│  Output: P(UP | features) ∈ [0, 1]                         │
└────────────────────────────────────────┬────────────────────┘
                                         │ P(UP)
┌────────────────────────────────────────▼────────────────────┐
│                   DECISION ENGINE                            │
│  EdgeDetector: edge = P_model - P_market                    │
│  KellySizer:   f* = (p·b - q) / b  × 0.25                  │
│  Gatekeeper:   verifica límites de riesgo                   │
└────────────────────────────────────────┬────────────────────┘
                                         │ BetOrder
┌────────────────────────────────────────▼────────────────────┐
│                  EXECUTION LAYER                             │
│  PolymarketClient (gamma-api / CLOB)                        │
│  NonceManager, GasEstimator, TxMonitor                      │
└────────────────────────────────────────┬────────────────────┘
                                         │ TxReceipt
┌────────────────────────────────────────▼────────────────────┐
│                   RISK MANAGER + LOGGER                      │
│  DrawdownMonitor, ExposureTracker, BetLogger (SQLite)       │
└─────────────────────────────────────────────────────────────┘
```

### Principios de diseño

- **Todo es asíncrono.** Usar `asyncio` end-to-end. Nunca bloquear el event loop.
- **Separación estricta de concerns.** Cada módulo tiene una responsabilidad única.
- **Fail-safe por defecto.** Cualquier excepción no manejada debe detener el bot, no ignorarse.
- **Idempotencia en apuestas.** El bot no debe apostar dos veces en el mismo mercado/resolución si ya tiene posición abierta.
- **Latencia objetivo < 150ms** desde señal hasta orden enviada a Polymarket.

---

## 3. Capa de datos — feeds en tiempo real

### 3.1 Conexión WebSocket a Binance (order book depth)

**Endpoint:** `wss://stream.binance.com:9443/ws/<symbol>@depth20@100ms`

**Ejemplo para BTC:** `wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms`

**Formato del mensaje recibido:**

```json
{
  "lastUpdateId": 160,
  "bids": [
    ["0.0024", "10"],
    ["0.0023", "20"]
  ],
  "asks": [
    ["0.0026", "100"],
    ["0.0027", "50"]
  ]
}
```

Cada entrada en `bids`/`asks` es `[price, quantity]`. El array tiene exactamente 20 niveles.

**Clase requerida: `BinanceOrderBookFeed`**

```python
# Pseudocódigo / contrato de la clase
class BinanceOrderBookFeed:
    """
    Conecta al WebSocket de Binance y mantiene el order book actualizado.
    Emite snapshots al OrderBookAggregator vía asyncio.Queue.
    """

    def __init__(self, symbol: str, queue: asyncio.Queue, levels: int = 20):
        # symbol: "btcusdt", "solusdt", "ethusdt"
        # queue: canal de salida hacia el signal engine
        # levels: cuántos niveles del book usar (10 o 20)
        ...

    async def connect(self) -> None:
        # Conectar con reconnect automático (exponential backoff)
        # En cada mensaje, parsear y hacer put() en la queue
        # Si la conexión se cae, reconectar en: 1s, 2s, 4s, 8s, 16s (max)
        ...

    def _parse_message(self, raw: dict) -> OrderBookSnapshot:
        # Convertir el JSON de Binance a OrderBookSnapshot
        # timestamp = time.time_ns() // 1_000_000  (milisegundos)
        ...
```

**Importante:** Usar `time.time_ns()` local (no el timestamp del exchange) para máxima precisión. El timestamp del exchange puede tener decenas de ms de lag.

### 3.2 Feed de trades (para CVD)

**Endpoint:** `wss://stream.binance.com:9443/ws/<symbol>@trade`

**Formato:**

```json
{
  "T": 1672515782136,
  "p": "0.001",
  "q": "100",
  "m": true
}
```

- `p`: precio del trade
- `q`: cantidad
- `m`: si `true`, el buyer fue el market maker → el trade fue **sell-initiated** (presión vendedora). Si `false`, fue **buy-initiated** (presión compradora).

**Clase requerida: `BinanceTradeFeed`**

```python
class BinanceTradeFeed:
    def __init__(self, symbol: str, queue: asyncio.Queue): ...
    async def connect(self) -> None: ...
    def _parse_trade(self, raw: dict) -> TradeEvent: ...
```

### 3.3 Datos de Polymarket (odds actuales)

**API REST de Polymarket (gamma-api):**

```
GET https://gamma-api.polymarket.com/markets?slug=<market_slug>
```

**Respuesta relevante:**

```json
{
  "outcomePrices": ["0.55", "0.45"],
  "tokens": [
    {"token_id": "...", "outcome": "Yes"},
    {"token_id": "...", "outcome": "No"}
  ],
  "closed": false,
  "endDateIso": "2024-01-15T12:05:00Z"
}
```

`outcomePrices[0]` = precio actual del token YES ≈ probabilidad implícita de mercado para UP.

**Clase requerida: `PolymarketFeed`**

```python
class PolymarketFeed:
    """
    Consulta las odds actuales de un mercado de Polymarket.
    Cachea el resultado por N segundos para no saturar la API.
    """
    def __init__(self, market_slug: str, cache_ttl_seconds: int = 5): ...
    async def get_market_odds(self) -> MarketOdds: ...
    # MarketOdds.yes_price ∈ [0, 1]
    # MarketOdds.token_id_yes: str
    # MarketOdds.market_closes_at: datetime
```

---

## 4. Motor de señales — cálculo de OFI

### 4.1 Fórmula matemática del OFI

El OFI mide el cambio neto en presión compradora vs. vendedora entre dos snapshots consecutivos del order book.

**Para cada nivel i del book, entre tiempo t-1 y t:**

```
bid_delta_i = bid_size[i][t] - bid_size[i][t-1]   si bid_price[i][t] == bid_price[i][t-1]
            = bid_size[i][t]                        si bid_price[i][t] > bid_price[i][t-1]  (mejor bid mejoró)
            = 0                                      si bid_price[i][t] < bid_price[i][t-1]  (mejor bid cayó)

ask_delta_i = ask_size[i][t] - ask_size[i][t-1]   si ask_price[i][t] == ask_price[i][t-1]
            = 0                                      si ask_price[i][t] > ask_price[i][t-1]  (mejor ask empeoró)
            = -ask_size[i][t]                        si ask_price[i][t] < ask_price[i][t-1] (mejor ask mejoró)
```

**OFI crudo (para N niveles):**

```
OFI_raw = Σ(i=0 to N-1) [ bid_delta_i - ask_delta_i ]
```

**Interpretación:**
- `OFI_raw > 0` → presión compradora neta → sesgo UP
- `OFI_raw < 0` → presión vendedora neta → sesgo DOWN
- `OFI_raw ≈ 0` → mercado equilibrado

### 4.2 Normalización del OFI

El OFI crudo no es comparable entre periodos de alta y baja liquidez. Normalizar por el volumen total del book:

```
total_book_size = Σ(i=0 to N-1) [ bid_size[i] + ask_size[i] ]
OFI_normalized = OFI_raw / total_book_size
OFI_normalized ∈ [-1, 1]
```

**Adicionalmente, aplicar z-score rolling:**

```
OFI_zscore = (OFI_normalized - mean(OFI_normalized, window=300)) 
             / std(OFI_normalized, window=300)
```

`window=300` significa los últimos 300 ticks (≈ 30 segundos si llegan a 100ms).

### 4.3 OFI acumulado en ventana temporal

Para la señal principal, acumular el OFI en una ventana de tiempo:

```
OFI_window(T) = Σ OFI_raw(t)  para t ∈ [now - T, now]
```

Calcular para múltiples ventanas:
- `OFI_10s`: ventana de 10 segundos
- `OFI_30s`: ventana de 30 segundos
- `OFI_60s`: ventana de 60 segundos

### 4.4 Clase requerida: `OFICalculator`

```python
class OFICalculator:
    """
    Calcula el Order Flow Imbalance a partir de snapshots del order book.

    Mantiene internamente:
    - El snapshot anterior del book para calcular deltas
    - Buffers rolling para normalización (deque con maxlen)
    - Buffers por ventana temporal (10s, 30s, 60s)
    """

    def __init__(self, levels: int = 20, windows_seconds: list[int] = [10, 30, 60]):
        # levels: cuántos niveles del book considerar
        # windows_seconds: ventanas temporales para OFI acumulado
        ...

    def update(self, snapshot: OrderBookSnapshot) -> OFIFeatures:
        """
        Recibe un nuevo snapshot, actualiza el estado interno,
        y retorna las features OFI calculadas.

        Returns OFIFeatures con:
          - ofi_raw: float
          - ofi_normalized: float
          - ofi_zscore: float
          - ofi_10s: float
          - ofi_30s: float
          - ofi_60s: float
          - bid_ask_ratio: float  (bid_size_total / ask_size_total)
          - spread_bps: float     (spread en basis points)
        """
        ...

    def _calc_bid_delta(self, prev_bids, curr_bids) -> float: ...
    def _calc_ask_delta(self, prev_asks, curr_asks) -> float: ...
    def _normalize(self, ofi_raw: float, total_size: float) -> float: ...
    def _zscore(self, value: float, buffer: deque) -> float: ...
```

### 4.5 VWAP Deviation

El desvío del precio respecto al VWAP es una señal complementaria al OFI.

```
VWAP(T) = Σ(price_i × volume_i) / Σ(volume_i)  para trades en [now - T, now]

VWAP_deviation = (mid_price - VWAP) / VWAP × 10000  (en basis points)
```

Un precio muy por encima del VWAP sugiere sobrecompra de corto plazo (reversión probable). Debajo sugiere sobreventa.

**Clase requerida: `VWAPCalculator`**

```python
class VWAPCalculator:
    def __init__(self, window_seconds: int = 300): ...  # 5 minutos
    def update(self, trade: TradeEvent) -> float: ...   # retorna VWAP_deviation en bps
```

### 4.6 ATR (Average True Range) como filtro de volatilidad

```
True Range = max(high - low, |high - prev_close|, |low - prev_close|)
ATR(n) = EWM(True Range, span=n)  (exponentially weighted)
```

Usar ATR del minuto actual normalizado por precio:

```
ATR_pct = ATR(14) / mid_price × 100
```

**Usar como filtro:**
- `ATR_pct < percentil_25(ATR_hist)` → mercado dormido → NO operar
- `ATR_pct > percentil_85(ATR_hist)` → mercado caótico → NO operar
- `percentil_25 ≤ ATR_pct ≤ percentil_85` → zona operativa ideal

### 4.7 Cumulative Volume Delta (CVD)

```
para cada trade:
  si buy-initiated (m=false): CVD += volume
  si sell-initiated (m=true): CVD -= volume

CVD_window = CVD(now) - CVD(now - T)
CVD_normalized = CVD_window / ATR(14)  (para hacerlo comparable)
```

---

## 5. Modelo de probabilidad

### 5.1 Features de entrada

El modelo recibe un `FeatureVector` con estos campos (todos normalizados):

| Feature | Descripción | Rango típico |
|---|---|---|
| `ofi_zscore` | OFI z-score en ventana 30s | [-3, 3] |
| `ofi_10s` | OFI acumulado 10s (norm.) | [-1, 1] |
| `ofi_60s` | OFI acumulado 60s (norm.) | [-1, 1] |
| `vwap_dev_bps` | Desvío vs VWAP en bps | [-50, 50] |
| `cvd_norm` | CVD normalizado por ATR | [-5, 5] |
| `bid_ask_ratio` | bid_size / ask_size (top 5) | [0.2, 5] |
| `spread_bps` | Spread bid-ask en bps | [0, 20] |
| `atr_pct` | ATR como % del precio | [0.01, 0.5] |
| `rsi_14` | RSI en velas de 1min | [0, 100] |
| `price_momentum_1m` | Retorno % últimos 60s | [-2, 2] |

**Nunca usar como feature:** el precio absoluto, el volumen sin normalizar, ni el timestamp crudo.

### 5.2 Variable target

```
target = 1   si price[t + window_seconds] > price[t]  (UP)
target = 0   si price[t + window_seconds] <= price[t] (DOWN o flat)
```

**Importante:** `price[t]` debe ser el **mid-price** en el momento de la apuesta, no el precio de cierre de la vela anterior.

```
mid_price = (best_bid + best_ask) / 2
```

### 5.3 Modelo recomendado: Logistic Regression calibrada

**¿Por qué Logistic Regression y no XGBoost/LSTM?**

- Menos parámetros → menos riesgo de overfitting con pocos datos
- Las probabilidades son naturalmente calibradas (con Platt Scaling)
- Entrenamiento rápido → se puede re-entrenar online
- Interpretable → puedes auditar qué señal está dominando

**Entrenamiento:**

```python
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

# Pipeline completo
pipeline = Pipeline([
    ('scaler', StandardScaler()),
    ('model', CalibratedClassifierCV(
        LogisticRegression(
            C=0.1,             # regularización fuerte (evita overfitting)
            max_iter=1000,
            class_weight='balanced'  # importante si hay más UPs que DOWNs
        ),
        method='sigmoid',      # Platt Scaling para calibración
        cv=5
    ))
])

pipeline.fit(X_train, y_train)
```

**Guardar y cargar el modelo:**

```python
import joblib
joblib.dump(pipeline, 'models/ofi_model_btc_5min.pkl')
pipeline = joblib.load('models/ofi_model_btc_5min.pkl')
```

### 5.4 Clase requerida: `ProbabilityModel`

```python
class ProbabilityModel:
    """
    Wrapper del modelo ML. Maneja carga, predicción y re-entrenamiento.
    """

    def __init__(self, model_path: str): ...

    def predict_proba(self, features: FeatureVector) -> float:
        """
        Retorna P(UP) ∈ [0, 1].
        Internamente:
        1. Convertir FeatureVector a numpy array en el orden correcto
        2. Aplicar el pipeline (scaler + modelo)
        3. Retornar predict_proba(X)[0][1]  (prob de clase 1 = UP)
        """
        ...

    def should_retrain(self) -> bool:
        """True si han pasado más de 24h desde el último entrenamiento."""
        ...

    async def retrain(self, db: Database) -> None:
        """
        Re-entrena el modelo con los últimos N días de datos.
        Ejecutar en un thread separado para no bloquear el event loop.
        Usar: await asyncio.get_event_loop().run_in_executor(None, self._retrain_sync)
        """
        ...
```

### 5.5 Validación del modelo (métricas requeridas)

Antes de usar el modelo en producción, verificar:

| Métrica | Umbral mínimo | Descripción |
|---|---|---|
| **Brier Score** | < 0.24 | Calibración de probabilidades (random = 0.25) |
| **Log-loss** | < 0.69 | Mejor que random (random = ln(2) ≈ 0.693) |
| **ROC-AUC** | > 0.55 | Poder discriminante mínimo aceptable |
| **Accuracy** | > 0.53 | Con fees, necesitas >53% para ser rentable |
| **Calibration curve** | Pendiente ≈ 1 | Probabilidades deben ser realistas |

**Validación walk-forward obligatoria:**

```
No usar: train_test_split (data leakage temporal)
Usar: TimeSeriesSplit con n_splits=5

Ejemplo:
  Fold 1: train=[días 1-60],  test=[días 61-72]
  Fold 2: train=[días 1-72],  test=[días 73-84]
  Fold 3: train=[días 1-84],  test=[días 85-96]
  ...
```

---

## 6. Motor de sizing — Kelly Criterion

### 6.1 Fórmula Kelly para mercados binarios

En Polymarket, una apuesta de $1 en YES a precio `p_market` retorna:
- Si ganas (UP ocurre): `(1 - p_market) / p_market` de ganancia neta (= odds b)
- Si pierdes (DOWN ocurre): pierdes $1

```
b = (1 - p_market) / p_market    # odds de pago neto
p = P_model(UP)                   # tu probabilidad estimada
q = 1 - p                         # probabilidad de pérdida

f_kelly = (p × b - q) / b
        = (p × b - (1-p)) / b
```

**Simplificado:**

```
f_kelly = p - (1 - p) / b
        = p - q / b
        = (p × (b + 1) - 1) / b
```

**Kelly fraccionario (SIEMPRE usar fracción):**

```
f_real = f_kelly × fraction   # fraction = 0.25 (Kelly cuarto)

Tamaño de apuesta = f_real × bankroll_disponible
```

**Ejemplo numérico:**
- `p_model = 0.65` (tu modelo dice 65% UP)
- `p_market = 0.52` (Polymarket dice 52% UP)
- `b = (1 - 0.52) / 0.52 = 0.923`
- `f_kelly = (0.65 × 0.923 - 0.35) / 0.923 = 0.28`
- `f_real = 0.28 × 0.25 = 0.07` → apostar 7% del bankroll

### 6.2 Casos donde NO apostar

```python
def should_bet(f_kelly: float, edge: float, config: Config) -> bool:
    """
    Retorna True solo si se cumplen TODAS las condiciones:
    """
    conditions = [
        f_kelly > 0,                              # Kelly positivo (edge real)
        edge >= config.min_edge,                  # Edge mínimo (ej. 0.04 = 4%)
        f_real <= config.max_bet_fraction,        # No apostar más del X% del bankroll
        not position_already_open(market_id),     # Sin posición abierta en ese mercado
        time_to_close > config.min_time_remaining, # Mercado no cierra en <60s
        atr_in_valid_range(),                     # ATR dentro del percentil 25-85
    ]
    return all(conditions)
```

### 6.3 Clase requerida: `KellySizer`

```python
class KellySizer:
    def __init__(
        self,
        fraction: float = 0.25,        # Kelly fraccionario
        min_edge: float = 0.04,        # Edge mínimo 4%
        max_bet_pct: float = 0.05,     # Nunca más del 5% del bankroll por apuesta
        min_bet_usdc: float = 1.0,     # Apuesta mínima
        max_bet_usdc: float = 500.0    # Apuesta máxima absoluta
    ): ...

    def compute(
        self,
        p_model: float,      # P(UP) del modelo
        p_market: float,     # P(UP) implícita en Polymarket
        bankroll: float      # USDC disponible
    ) -> BetDecision:
        """
        Retorna BetDecision con:
          - should_bet: bool
          - amount_usdc: float
          - direction: "YES" | "NO"
          - edge: float
          - f_kelly: float
          - reasoning: str  (para logging)
        """
        ...
```

**Nota sobre dirección:** Si `p_model > p_market`, apostar YES. Si `p_model < p_market - min_edge`, apostar NO (el mercado sobreestima UP).

---

## 7. Integración con Polymarket

### 7.1 Setup de la API

Polymarket usa el `py-clob-client` oficial para interactuar con su CLOB (Central Limit Order Book).

**Instalación:**

```bash
pip install py-clob-client
```

**Inicialización:**

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,          # Clave privada de la wallet (Polygon)
    chain_id=137,             # Polygon PoS mainnet
    creds=ApiCreds(
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE
    ),
    signature_type=0,         # EOA signature
    funder=FUNDER_ADDRESS     # Dirección de tu wallet
)
```

**Nota de seguridad:** La `PRIVATE_KEY` nunca debe estar hardcodeada. Cargar siempre desde variable de entorno o vault.

### 7.2 Obtener precio actual de un mercado

```python
# Obtener el order book del token YES de un mercado
order_book = client.get_order_book(token_id=YES_TOKEN_ID)

# El mejor precio ask del YES = probabilidad implícita del mercado
best_ask_yes = float(order_book.asks[0].price)  # ej. 0.55 = 55% UP
best_bid_yes = float(order_book.bids[0].price)  # ej. 0.53

# Usar el mid-price del mercado de predicción
market_p_up = (best_ask_yes + best_bid_yes) / 2
```

### 7.3 Enviar una orden de apuesta

```python
from py_clob_client.clob_types import MarketOrderArgs, OrderType

# Apostar YES (predicción: precio sube)
order_args = MarketOrderArgs(
    token_id=YES_TOKEN_ID,
    amount=bet_amount_usdc,     # Cantidad en USDC a gastar
)

# Crear y firmar la orden
signed_order = client.create_market_order(order_args)

# Enviar la orden
response = client.post_order(signed_order, OrderType.FOK)  # Fill-or-Kill
# FOK: ejecuta completamente o no ejecuta. NUNCA usar GTC para HFT.
```

**Tipos de orden:**
- `FOK` (Fill-or-Kill): recomendado para HFT. Se ejecuta al precio actual o no se ejecuta.
- `GTC` (Good-till-Cancelled): no usar, puede ejecutarse en momento inoportuno.

### 7.4 Clase requerida: `PolymarketClient`

```python
class PolymarketClient:
    """
    Wrapper sobre py-clob-client con manejo de errores,
    retry logic y rate limiting.
    """

    def __init__(self, config: PolymarketConfig): ...

    async def get_market_price(self, token_id: str) -> MarketPrice:
        """
        Retorna MarketPrice con:
          - yes_price: float   (mid-price del token YES)
          - bid: float
          - ask: float
          - spread: float
          - timestamp: float
        """
        ...

    async def place_bet(self, bet: BetDecision, token_id: str) -> BetReceipt:
        """
        Envía la orden. En caso de error:
        - Rate limit (429): esperar y reintentar hasta 3 veces
        - Insuficient balance: lanzar InsufficientBalanceError
        - Network error: reintentar con exponential backoff
        - Cualquier otro error: lanzar y NO reintentar (previene doble-apuesta)

        Retorna BetReceipt con:
          - tx_hash: str
          - amount_usdc: float
          - price_filled: float
          - status: "filled" | "rejected" | "partial"
          - timestamp: float
        """
        ...

    async def get_balance(self) -> float:
        """Retorna el balance USDC disponible."""
        ...

    async def get_open_positions(self) -> list[Position]:
        """Retorna posiciones abiertas (apuestas aún no resueltas)."""
        ...
```

### 7.5 Identificar mercados activos

Polymarket tiene una API REST para listar mercados activos:

```
GET https://gamma-api.polymarket.com/markets?tag=crypto&closed=false&limit=50
```

**Filtrar mercados relevantes:**

```python
async def find_active_markets(asset: str, window_minutes: int) -> list[Market]:
    """
    Busca mercados activos del tipo:
    "Will BTC be higher in 5 minutes?"

    Filtros:
    - asset: "BTC", "SOL", "ETH"
    - window_minutes: 5 o 15
    - closed=false
    - endDate: entre now+2min y now+window+2min (margen para latencia)
    """
    ...
```

**Mercado slug pattern:**
Los slugs de Polymarket para crypto siguen el patrón:
`will-<asset>-be-higher-in-<N>-minutes-<date>`

---

## 8. Gestor de riesgo

### 8.1 Límites por apuesta

```python
class RiskConfig:
    max_bet_pct_bankroll: float = 0.05      # Máx 5% por apuesta
    max_bet_usdc: float = 500.0             # Tope absoluto en USDC
    min_bet_usdc: float = 1.0              # Mínimo operativo
    min_edge_to_bet: float = 0.04          # Edge mínimo 4%
    max_open_positions: int = 3             # Máx 3 apuestas simultáneas
    max_exposure_pct: float = 0.15         # Máx 15% del bankroll expuesto total
    min_time_to_close_seconds: int = 60    # No apostar si quedan <60s
```

### 8.2 Circuit breakers (parar el bot automáticamente)

```python
class CircuitBreaker:
    """
    Detiene el bot si se cumplen condiciones de pérdida extrema.
    """

    # Parar si pérdida en las últimas N apuestas supera umbral
    consecutive_losses_limit: int = 5
    loss_streak_stop: bool = True

    # Parar si drawdown del día supera X%
    daily_drawdown_limit_pct: float = 0.10   # 10% del bankroll

    # Parar si win rate de las últimas 20 apuestas < umbral
    min_recent_win_rate: float = 0.40
    recent_win_rate_window: int = 20

    def check(self, bet_history: list[BetResult]) -> CircuitBreakerStatus:
        """
        Retorna CircuitBreakerStatus con:
          - should_stop: bool
          - reason: str | None
        """
        ...
```

### 8.3 Registro de apuestas

Todas las apuestas deben guardarse en base de datos con el siguiente schema:

```sql
CREATE TABLE bets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL NOT NULL,          -- Unix timestamp
    market_id       TEXT NOT NULL,
    asset           TEXT NOT NULL,          -- "BTC", "SOL", "ETH"
    window_minutes  INTEGER NOT NULL,       -- 5 o 15
    direction       TEXT NOT NULL,          -- "YES" o "NO"
    amount_usdc     REAL NOT NULL,
    price_entered   REAL NOT NULL,          -- Precio pagado por el token
    p_model         REAL NOT NULL,          -- P(UP) del modelo
    p_market        REAL NOT NULL,          -- P(UP) del mercado al momento
    edge            REAL NOT NULL,          -- p_model - p_market
    f_kelly         REAL NOT NULL,
    ofi_zscore      REAL,                   -- Features clave (para análisis)
    vwap_dev_bps    REAL,
    cvd_norm        REAL,
    atr_pct         REAL,
    tx_hash         TEXT,
    resolved        INTEGER DEFAULT 0,      -- 0=pendiente, 1=won, -1=lost
    pnl_usdc        REAL,                   -- NULL hasta resolución
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
```

---

## 9. Backtesting y calibración

### 9.1 Descarga de datos históricos

**Datos de precio (OHLCV) — Binance API REST:**

```
GET https://api.binance.com/api/v3/klines
  ?symbol=BTCUSDT
  &interval=1m
  &startTime=<unix_ms>
  &endTime=<unix_ms>
  &limit=1000
```

**Datos de order book histórico (más difícil):**
Binance no provee order book histórico gratuito. Opciones:
1. **Recolectar tú mismo** durante 2–4 semanas antes de operar en vivo.
2. **Tardis.dev** (de pago, $150/mes): provee order book tick-by-tick histórico.
3. **Aproximación:** reconstruir presión de book con datos de trades (CVD) disponibles.

**Implementar `DataCollector`:**

```python
class DataCollector:
    """
    Corre en paralelo con el bot, guardando todos los snapshots
    del order book y trades en la base de datos para futuros re-entrenamientos.
    """
    async def collect_order_book(self, snapshot: OrderBookSnapshot) -> None: ...
    async def collect_trade(self, trade: TradeEvent) -> None: ...
    async def collect_market_outcome(self, market_id: str, outcome: bool) -> None: ...
```

### 9.2 Pipeline de backtesting

```
1. Cargar snapshots históricos del order book (CSV o DB)
2. Para cada timestamp T con ventana W:
   a. Calcular OFI y features en [T-300s, T]
   b. Predecir P(UP) con el modelo
   c. Obtener precio hipotético de Polymarket (o aproximar como 0.50 ± ruido)
   d. Calcular edge y tamaño Kelly
   e. Simular apuesta (aplicar fees: ~2%)
   f. Verificar outcome: price[T+W] > price[T] ?
   g. Registrar PnL
3. Calcular métricas finales
```

**Métricas de backtesting requeridas:**

```python
class BacktestMetrics:
    total_bets: int
    win_rate: float              # % de apuestas ganadas
    avg_edge: float              # Edge promedio cuando se apostó
    total_pnl_usdc: float
    sharpe_ratio: float          # (E[pnl] / std[pnl]) × sqrt(252)
    max_drawdown_pct: float      # Mayor caída desde pico
    avg_bet_size_usdc: float
    bets_per_day: float
    roi_pct: float               # Total PnL / Bankroll inicial
    calmar_ratio: float          # ROI anual / Max Drawdown
```

### 9.3 Checklist de validación pre-producción

```
[ ] Brier Score < 0.24 en test set walk-forward
[ ] ROC-AUC > 0.55 en test set walk-forward
[ ] Backtesting ROI > 0% después de fees (2% por apuesta)
[ ] Max drawdown en backtest < 20%
[ ] Modelo re-entrenado con datos de los últimos 90 días mínimo
[ ] Calibration curve tiene pendiente cercana a 1.0
[ ] No hay data leakage (verificar con shuffled labels → ROC-AUC ≈ 0.50)
```

---

## 10. Estructura de archivos del proyecto

```
polymarket-ofi-bot/
│
├── config/
│   ├── settings.py           # Pydantic Settings (carga .env)
│   └── markets.yaml          # Config de mercados (slugs, tokens, windows)
│
├── data/
│   ├── feeds/
│   │   ├── binance_book.py   # BinanceOrderBookFeed
│   │   ├── binance_trades.py # BinanceTradeFeed
│   │   └── polymarket.py     # PolymarketFeed
│   ├── collector.py          # DataCollector (guarda datos crudos)
│   └── database.py           # Wrapper SQLite/TimescaleDB
│
├── signals/
│   ├── ofi.py                # OFICalculator (clase principal)
│   ├── vwap.py               # VWAPCalculator
│   ├── atr.py                # ATRCalculator
│   ├── cvd.py                # CVDCalculator
│   └── aggregator.py         # Combina todas las señales en FeatureVector
│
├── models/
│   ├── trainer.py            # Entrenamiento y validación del modelo
│   ├── predictor.py          # ProbabilityModel (inferencia)
│   └── artifacts/            # Modelos guardados (.pkl)
│       ├── btc_5min.pkl
│       ├── sol_15min.pkl
│       └── eth_5min.pkl
│
├── execution/
│   ├── kelly.py              # KellySizer
│   ├── polymarket_client.py  # PolymarketClient
│   └── order_manager.py      # Maneja estados de órdenes
│
├── risk/
│   ├── manager.py            # RiskManager (valida antes de apostar)
│   ├── circuit_breaker.py    # CircuitBreaker
│   └── position_tracker.py  # Trackea posiciones abiertas
│
├── backtest/
│   ├── engine.py             # Motor de backtesting
│   ├── metrics.py            # BacktestMetrics
│   └── data_loader.py        # Carga datos históricos
│
├── utils/
│   ├── logger.py             # Setup de loguru
│   ├── time_utils.py         # Helpers de tiempo (UTC siempre)
│   └── schemas.py            # Todos los dataclasses/Pydantic models
│
├── tests/
│   ├── unit/
│   │   ├── test_ofi.py
│   │   ├── test_kelly.py
│   │   └── test_risk.py
│   └── integration/
│       └── test_polymarket_client.py
│
├── main.py                   # Entry point del bot
├── train.py                  # Script de entrenamiento standalone
├── backtest_run.py           # Script de backtesting standalone
├── requirements.txt
├── .env.example
└── README.md
```

---

## 11. Contratos de datos (schemas)

Definir todos los dataclasses en `utils/schemas.py`:

```python
from dataclasses import dataclass, field
from typing import Literal
import time

@dataclass
class OrderBookLevel:
    price: float
    size: float

@dataclass
class OrderBookSnapshot:
    symbol: str                          # "BTCUSDT"
    timestamp_ms: int                    # Timestamp local en ms
    bids: list[OrderBookLevel]           # Ordenados mejor precio primero
    asks: list[OrderBookLevel]           # Ordenados mejor precio primero
    mid_price: float = field(init=False)

    def __post_init__(self):
        self.mid_price = (self.bids[0].price + self.asks[0].price) / 2

@dataclass
class TradeEvent:
    symbol: str
    timestamp_ms: int
    price: float
    quantity: float
    is_buyer_maker: bool    # True = sell-initiated, False = buy-initiated

@dataclass
class OFIFeatures:
    timestamp_ms: int
    ofi_raw: float
    ofi_normalized: float
    ofi_zscore: float
    ofi_10s: float
    ofi_30s: float
    ofi_60s: float
    bid_ask_ratio: float
    spread_bps: float

@dataclass
class FeatureVector:
    timestamp_ms: int
    ofi_zscore: float
    ofi_10s: float
    ofi_60s: float
    vwap_dev_bps: float
    cvd_norm: float
    bid_ask_ratio: float
    spread_bps: float
    atr_pct: float
    rsi_14: float
    price_momentum_1m: float

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
    market_id: str
    token_id_yes: str
    token_id_no: str
    yes_price: float        # P(UP) implícita del mercado ∈ [0, 1]
    bid_yes: float
    ask_yes: float
    closes_at_utc: float    # Unix timestamp UTC

@dataclass
class BetDecision:
    should_bet: bool
    direction: Literal["YES", "NO"] | None
    amount_usdc: float
    edge: float
    f_kelly: float
    p_model: float
    p_market: float
    reasoning: str

@dataclass
class BetReceipt:
    tx_hash: str
    amount_usdc: float
    price_filled: float
    status: Literal["filled", "rejected", "partial"]
    timestamp_ms: int
```

---

## 12. Variables de entorno y configuración

### `.env.example`

```bash
# Polymarket / Polygon wallet
PRIVATE_KEY=0x...                     # Clave privada de la wallet (NUNCA commitear)
FUNDER_ADDRESS=0x...                  # Dirección pública de la wallet
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...

# Exchanges (solo lectura, sin trading)
BINANCE_WS_URL=wss://stream.binance.com:9443/ws

# Base de datos
DATABASE_PATH=./data/bot.db

# Bot config
MIN_EDGE=0.04                         # Edge mínimo para apostar (4%)
KELLY_FRACTION=0.25                   # Fracción Kelly
MAX_BET_PCT=0.05                      # Máximo 5% del bankroll por apuesta
MAX_OPEN_POSITIONS=3
DAILY_DRAWDOWN_LIMIT=0.10            # Parar si perdemos 10% del bankroll en el día

# Mercados a operar (separados por coma)
ACTIVE_MARKETS=BTC-5MIN,SOL-15MIN,ETH-5MIN

# Logging
LOG_LEVEL=INFO
LOG_FILE=./logs/bot.log
```

### `config/settings.py`

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    private_key: str
    funder_address: str
    polymarket_api_key: str
    polymarket_api_secret: str
    polymarket_api_passphrase: str

    binance_ws_url: str = "wss://stream.binance.com:9443/ws"
    database_path: str = "./data/bot.db"

    min_edge: float = 0.04
    kelly_fraction: float = 0.25
    max_bet_pct: float = 0.05
    max_open_positions: int = 3
    daily_drawdown_limit: float = 0.10

    active_markets: list[str] = ["BTC-5MIN", "SOL-15MIN"]

    log_level: str = "INFO"
    log_file: str = "./logs/bot.log"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
```

---

## 13. Checklist de implementación

### Fase 1 — Data layer (semana 1)

```
[ ] Implementar BinanceOrderBookFeed con reconexión automática
[ ] Implementar BinanceTradeFeed
[ ] Implementar PolymarketFeed (REST polling)
[ ] Crear base de datos SQLite con schemas correctos
[ ] Implementar DataCollector (guardar todo para backtesting)
[ ] Pruebas: correr 24h en modo "solo datos" sin apostar
```

### Fase 2 — Signal engine (semana 1-2)

```
[ ] Implementar OFICalculator con tests unitarios
[ ] Implementar VWAPCalculator con tests unitarios
[ ] Implementar ATRCalculator con tests unitarios
[ ] Implementar CVDCalculator con tests unitarios
[ ] Implementar SignalAggregator (produce FeatureVector)
[ ] Validar: comparar OFI calculado manualmente con datos reales
```

### Fase 3 — Modelo ML (semana 2-3)

```
[ ] Recolectar datos: mínimo 30 días de snapshots + outcomes de Polymarket
[ ] Implementar pipeline de entrenamiento con walk-forward validation
[ ] Verificar métricas de calibración (Brier Score < 0.24)
[ ] Implementar ProbabilityModel con carga/inferencia
[ ] Implementar re-entrenamiento automático (cada 24h)
[ ] Guardar modelos versionados con fecha en el nombre
```

### Fase 4 — Ejecución y riesgo (semana 3-4)

```
[ ] Implementar KellySizer con tests
[ ] Implementar PolymarketClient con manejo de errores
[ ] Implementar RiskManager y CircuitBreaker
[ ] Implementar PositionTracker (no apostar dos veces el mismo mercado)
[ ] Prueba de integración: apostar $1 en testnet/paper trading
```

### Fase 5 — Backtesting y go-live (semana 4)

```
[ ] Correr backtesting sobre datos recolectados
[ ] Verificar ROI positivo post-fees en backtest
[ ] Configurar alertas (Telegram bot o email para circuit breakers)
[ ] Go-live con bankroll reducido (10% del capital objetivo)
[ ] Monitorear 1 semana en vivo antes de escalar
```

---

## 14. Errores comunes y cómo evitarlos

| Error | Consecuencia | Prevención |
|---|---|---|
| Usar `train_test_split` en lugar de walk-forward | Overfitting, modelo inútil en producción | Siempre `TimeSeriesSplit` |
| No normalizar OFI por liquidez del book | Señales incomparables entre sesiones | Siempre dividir por `total_book_size` |
| Apostar en mercados que cierran en <60s | Orden puede no ejecutarse o ejecutarse a mal precio | Filtro `min_time_to_close` obligatorio |
| Hardcodear la private key | Pérdida total de fondos | Solo desde variables de entorno |
| No usar Kelly fraccionario | Ruina matemática garantizada con rachas malas | Siempre `f* × 0.25` como máximo |
| Usar `GTC` en vez de `FOK` en Polymarket | Orden ejecutada en momento incorrecto | Siempre `OrderType.FOK` para HFT |
| No manejar reconexión del WebSocket | Bot se detiene silenciosamente | Exponential backoff en todos los feeds |
| Apostar el mismo mercado dos veces | Doble exposición no deseada | `PositionTracker` verifica antes de cada orden |
| Ignorar fees de Polymarket (~2%) | Backtest rentable, producción perdedora | Incluir fees en backtesting: `pnl × (1 - 0.02)` |
| Feature con el precio absoluto | El modelo aprende el nivel de precio, no el movimiento | Solo retornos % y features normalizadas |
| No validar calibración del modelo | Edge calculado incorrectamente | Brier Score y calibration curve obligatorios |
| Calcular OFI sobre velas OHLCV en lugar de ticks | Señal de baja calidad, mucho ruido | Usar WebSocket Level 2 con snapshots de 100ms |

---

## Referencias

- Cont, Kukanov, Stoikov (2014). *"The Price Impact of Order Book Events."* Journal of Financial Econometrics. — Paper original que formaliza el OFI.
- Kelly (1956). *"A New Interpretation of Information Rate."* Bell System Technical Journal. — Fundamento matemático del Kelly Criterion.
- Avellaneda & Stoikov (2008). *"High-frequency trading in a limit order book."* — Modelo de market making con gestión de inventario.
- [Polymarket CLOB API Docs](https://docs.polymarket.com/) — Documentación oficial.
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client) — SDK oficial de Polymarket.
- [Binance WebSocket Streams](https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams) — Documentación de feeds.
