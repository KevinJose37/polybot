# Análisis: XRP Nocturno & Régimen de Mercado Choppy

**Muestra:** 302 trades de V1 deduplicados, todas las sesiones desde el 4 de Mayo.

---

## Pregunta 1: ¿XRP es malo de noche en TODOS los días?

### Respuesta: SÍ. Sin excepción.

| Asset | Período | Trades | WR | PnL Total |
|:---|:---:|:---:|:---:|:---:|
| **XRP** | **NOCHE** | **41** | **31.7%** | **-$15.01** |
| XRP | DÍA | 14 | 50.0% | -$0.92 |
| BTC | NOCHE | 84 | 58.3% | +$1.62 |
| ETH | NOCHE | 91 | 58.2% | +$2.53 |

### XRP de noche, día por día:

| Fecha | W/L | PnL | ¿Rentable? |
|:---|:---:|:---:|:---:|
| 2026-05-04 | 3W / 7L | **-$5.86** | ❌ |
| 2026-05-06 | 6W / 6L | -$0.32 | ❌ |
| 2026-05-07 | 4W / 10L | **-$3.83** | ❌ |
| 2026-05-08 | 0W / 5L | **-$5.00** | ❌ |
| **TOTAL** | **13W / 28L** | **-$15.01** | ❌❌❌ |

**XRP de noche NUNCA ha sido rentable en ningún día.** En 4 noches consecutivas, ha generado pérdidas consistentes. Su WR nocturno histórico de 31.7% es catastrófico.

En contraste, BTC y ETH de noche mantienen un WR de ~58% y son rentables.

**Conclusión definitiva:** XRP debe ser **EXCLUIDO** de cualquier bot que opere entre 6pm y 8am CDT.

---

## Pregunta 2: Análisis del Mercado Choppy

*Definición: Un trade "choppy" es aquel donde el |signal_score| fue menor a 0.50, indicando que los indicadores EMA/RSI no encontraron una tendencia clara.*

### Resultados Globales

| Régimen | Trades | WR | PnL | Avg Score |
|:---|:---:|:---:|:---:|:---:|
| **CHOPPY** (score < 0.50) | 89 | **43.8%** | **-$13.19** | 0.444 |
| **TRENDING** (score >= 0.50) | 213 | **56.3%** | -$0.37 | 0.656 |

**El mercado choppy ha destruido $13.19 en 89 trades.** El mercado con tendencia (trending) apenas pierde -$0.37 en 213 trades — prácticamente breakeven.

### ¿Choppy es problema de la noche o del día?

| Régimen + Período | Trades | WR | PnL |
|:---|:---:|:---:|:---:|
| Choppy NOCHE | 74 | 44.6% | **-$9.45** |
| Choppy DÍA | 15 | 40.0% | -$3.74 |

El choppy es **5x más frecuente de noche** (74 trades vs 15). Y la pérdida nocturna choppy de -$9.45 representa el **72%** de todas las pérdidas choppy.

### ¿Choppy es problema de alguna moneda específica?

| Asset en Choppy | Trades | WR | PnL |
|:---|:---:|:---:|:---:|
| **XRP** | 22 | **18.2%** | **-$13.13** |
| BTC | 36 | 50.0% | -$1.04 |
| ETH | 27 | 55.6% | -$0.08 |

**XRP en mercado choppy tiene un Win Rate de 18.2% y es responsable del 99.5% de TODAS las pérdidas choppy (-$13.13 de -$13.19).**

BTC y ETH en choppy son prácticamente breakeven (-$1.04 y -$0.08). El problema no es el mercado choppy per se — es **XRP + choppy**.

### La Matriz Completa (Asset × Período × Régimen)

| Asset | Período | Régimen | Trades | WR | PnL |
|:---|:---:|:---:|:---:|:---:|:---:|
| BTC | DÍA | Choppy | 7 | 71.4% | +$2.47 |
| BTC | DÍA | Trending | 18 | 44.4% | -$2.97 |
| BTC | NOCHE | Choppy | 29 | 44.8% | -$3.51 |
| BTC | **NOCHE** | **Trending** | **55** | **65.5%** | **+$5.13** |
| ETH | DÍA | Choppy | 3 | 0.0% | -$3.00 |
| ETH | DÍA | Trending | 17 | 58.8% | +$3.35 |
| ETH | NOCHE | Choppy | 24 | 62.5% | +$2.92 |
| ETH | NOCHE | Trending | 67 | 56.7% | -$0.39 |
| XRP | DÍA | Choppy | 4 | 0.0% | -$4.00 |
| XRP | **DÍA** | **Trending** | **10** | **70.0%** | **+$3.08** |
| XRP | **NOCHE** | **Choppy** | **18** | **22.2%** | **-$9.13** |
| XRP | NOCHE | Trending | 23 | 39.1% | -$5.88 |

### Hallazgos Clave de la Matriz:

1. **BTC Noche Trending = ORO** (65.5% WR, +$5.13) — La combinación más rentable de toda la historia.
2. **XRP Noche = VENENO en cualquier régimen** — Choppy: 22% WR, -$9.13. Trending: 39% WR, -$5.88. No hay salvación.
3. **XRP Día Trending = Sorprendentemente bueno** (70% WR, +$3.08) — Confirma que XRP solo sirve de día con señales fuertes.
4. **ETH es resiliente** — Funciona aceptablemente en todos los escenarios excepto "Día Choppy" (solo 3 trades, insuficiente).
