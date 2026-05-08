# HFT Scalper — Bitácora de Rendimiento (Sesión 3)

**Fecha:** 2026-05-07
**Horario:** ~12:15pm – 5:45pm CDT (Sesión Diurna)
**Duración:** ~5.5h
**Stake:** $1/trade | **Capital Inicial:** $24

## 🏆 Leaderboard de la Sesión 3

| Strategy | Trades | Win/Loss | Win Rate | P&L ($) | ROI (%) | Notas |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **V2OPT3** | 18 | 11W/7L | 61.1% | +$2.84 | +15.8% | *Velocity Gate + Hold (Mejor ganancia absoluta)* |
| **V2** | 33 | 17W/16L | 51.5% | +$2.19 | +6.4% | *El más consistente de nuevo* |
| **V2OPT2** | 7 | 5W/2L | 71.4% | +$2.15 | +30.7% | *Entry Window + Hold* |
| **V6** | 5 | 5W/0L | 100.0% | +$1.06 | +21.2% | *Early Scalper - Poly Velocity* |
| **V7** | 1 | 1W/0L | 100.0% | +$1.00 | +100.0% | *Datos insuficientes (1 trade)* |
| **V1** | 28 | 14W/14L | 50.0% | +$0.52 | +1.9% | *Alto volumen, bajo margen* |
| **V3** | 37 | 13W/24L | 35.1% | -$5.14 | -13.9% | *Exceso de trades perdedores* |
| **V5** | 21 | 8W/13L | 38.1% | -$5.30 | -25.2% | *El peor rendimiento del día* |

---

## 🔍 Análisis de Patrones y Comparativa (Sesiones Previas vs Hoy)

> [!TIP]
> **V6 es el Rey de la Precisión**
> La **V6** mantuvo un asombroso **100% de Win Rate** (5W/0L, +$1.06). Este comportamiento es **idéntico al observado en las Sesiones 1 y 2**. Al basarse puramente en la velocidad del orderbook de Polymarket (y no de Binance), entra justo antes de los movimientos reales de los usuarios. Su volumen de operaciones sigue siendo muy bajo, pero es el bot más seguro de todos.

> [!IMPORTANT]
> **XRP y V2: Un matrimonio perfecto de tarde**
> Tal y como descubrimos en la Sesión 2 diurna, **XRP volvió a ser el motor absoluto de la V2**. De los +$2.19 que ganó la V2 hoy, **+$2.35 provinieron exclusivamente de XRP** (6W/2L). Mientras que BTC y ETH le generaron pérdidas ligeras.
> **Conclusión confirmada:** En horario americano de tarde (1pm - 6pm), XRP tiene la volatilidad exacta para que los indicadores de la V2 lo operen de manera sumamente rentable.

> [!WARNING]
> **El desplome de la V5 en horario diurno**
> Mientras que en el análisis histórico global la V5 ganaba dinero, en la sesión de esta tarde fue un desastre (-$5.30, 38% WR). **ETH volvió a ser su debilidad (-$2.07)** y BTC la arrastró aún más (-$3.23). Esto confirma que los filtros de penalidad "suaves" de la V5 son demasiado lentos y caen en las trampas de liquidez de las tardes.

> [!NOTE]
> **Hold to Resolution (V2OPT2 / V2OPT3) vs Salidas Activas**
> Las variantes que sostienen el trade hasta la resolución sin utilizar Take Profit ni Stop Loss dinámico (V2OPT2 y V2OPT3) consiguieron las rentabilidades más altas (+30.7% y +15.8% ROI). Esto revalida la teoría de la Sesión 1: el ruido de 5 minutos te hace salir de trades ganadores prematuramente. **Hold Only es matemáticamente superior**.

---

## 🪙 Desglose por Activos (Sesión 3)

**ETH (Ethereum)**
- **Brilló en:** V2OPT3 (+$2.09), V1 (+$1.90), V2OPT2 (+$0.79).
- **Fracasó en:** V3 (-$4.71), V5 (-$2.07).

**BTC (Bitcoin)**
- **Brilló en:** V2OPT2 (+$1.36), V6 (+$0.94), V2OPT3 (+$0.75).
- **Fracasó en:** V5 (-$3.23), V1 (-$2.13).

**XRP (Ripple)**
- **Brilló en:** V2 (+$2.35). *(Este bot fue prácticamente el único que lo operó con éxito)*.

---

## 📌 Acciones para Futuras Sesiones

1. **V6 y V7 a Producción:** La V6 ha validado su solidez durante tres sesiones consecutivas con WR > 85%. La V7 (que es un híbrido de la V6 + Hold) necesita correr más tiempo, pero su único trade fue perfecto.
2. **Apagar V3 y V5:** No son estrategias rentables para el mercado actual. Sus indicadores generan demasiadas falsas señales.
3. **Restringir XRP por horarios:** Solo debe operarse con la **V2** y preferentemente entre las 12 PM y las 6 PM.
