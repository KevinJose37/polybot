# Changelog: V11 Spread Sniper (HFT Kamikaze Mode)

Este documento registra todas las modificaciones realizadas en el sistema base para permitir que la estrategia V11 pueda operar sin restricciones. Si en el futuro notas que otras estrategias (V4, V6, etc.) se comportan extraño o asumen mucho riesgo, revisa esta lista para revertir los cambios globales.

## 1. Modificaciones Globales (Afectan a todo el bot)

Estas modificaciones se hicieron en los motores centrales del bot porque la V11 requería saltarse reglas nativas de Polymarket.

### `scalper/live_client.py`
- **Filtro de Riesgo/Beneficio (R/R) Apagado:** Cambiamos `max_rr` de `2.0` a `99.0`. 
  - *Motivo:* El bot nativo rechazaba entradas si tenías que arriesgar mucho para ganar poco (ej. comprar a $0.80). La V11 necesita entrar a cualquier precio si hay desbalance.
- **Filtro de Liquidez Mínima Reducido:** Cambiamos la exigencia de `best_ask_sz < 2.0` a `best_ask_sz < 0.5`.
  - *Motivo:* Al entrar en los primeros 5 segundos de un mercado, casi no hay volumen. Esto permite comprar hasta fracciones de acciones.

### `scalper/trader.py`
- **Fallback de Liquidez en Paper Trading (Compra):** Si el simulador (WebSocket) no ve a nadie vendiendo (`sim["best_ask"] <= 0`), ahora usa el precio de la API REST como "estimación" en lugar de cancelar la orden.
- **Fallback de Liquidez en Paper Trading (Venta):** Si el simulador no ve a nadie comprando a la hora de hacer el Take Profit, usará el precio de la API REST en lugar de quedarse bloqueado con `No bids on book -> holding for resolution`.
- **Modificación del Runner (`scalper/runner.py`):**
  - Cambiamos el código en las líneas 192 y 663 para que no use el límite duro `HFT_MAX_SPREAD=0.03`. Ahora lee dinámicamente `profile.max_spread` para permitir spreads distintos por estrategia.

## 2. Modificaciones de Perfil (Afectan solo a V11)

Estas modificaciones viven en `scalper/strategy_profiles.py` y solo afectan cuando usas `--strategy v11`.

- **`entry_window_end`**: Ampliado de `45s` a `120s`. (Le da 2 minutos al bot para detectar la ruptura del spread).
- **`sniper_trigger_price`**: Bajado a `0.505` (Matemáticamente asegura que dispare exactamente cuando Polymarket muestre 0.51).
- **`min_entry_price`**: Bajado de `0.51` a `0.10` (Evita rechazos REST si el precio se desploma en milisegundos).
- **`max_spread`**: Agregado como variable nueva y fijado en `0.06` (El bot normal usa 0.03).
- **`stop_loss`**: Desactivado (Cambiado a `99.0`). 
- **`signal_reversal`**: Desactivado (Cambiado a `99.0`).

## 3. Modificaciones de Machine Learning

### `scripts/ml/train_xgboost.py`
- Se inyectó la lógica de `scale_pos_weight` calculando dinámicamente el desbalance (`neg_samples / pos_samples`).
- *Motivo:* El mercado real no sube el 91% de las veces. Esto curó al modelo de su cobardía, elevando su Recall (detección de pumps) del 0% al 40%.
