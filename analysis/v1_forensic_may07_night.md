# Análisis Forense V1 — ¿Por Qué Cayó Esta Noche?

**Fecha:** 2026-05-07, 6pm-10pm CDT
**Sesión de referencia:** S2 (May 6, 7pm-11pm) — La mejor noche histórica de V1

---

## 1. Tabla Comparativa Cross-Session

| Sesión | Trades | WR | PnL | Avg Score | Avg Entry | Timing | Max Streak |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **S2: May 6, 7pm-11pm** ✅ | 77 | 57.1% | **+$10.01** | 0.589 | $0.498 | 88s | 6 |
| **S4: May 7, 12pm-6pm** | 31 | 51.6% | +$0.58 | 0.593 | $0.502 | 153s | 4 |
| **S1: Overnight May 5** | 77 | 61.0% | -$5.23 | 0.593 | $0.628 | 70s | 5 |
| **TONIGHT: 6pm-10pm** ❌ | 14 | 42.9% | **-$4.07** | 0.485 | $0.560 | 157s | 5 |
| **S3: May 7, 12am-8am** | 7 | 14.3% | -$5.46 | 0.535 | $0.524 | 154s | 4 |

---

## 2. Los 6 Factores que Explican la Caída

### Factor 1: XRP Nocturno — El Destructor ($-5.00 en 5 trades, 0W)

| Sesión | XRP WR | XRP PnL |
|:---|:---:|:---:|
| S2 (noche ganadora) | 38.5% | -$0.56 |
| S4 (tarde) | 55.6% | +$0.27 |
| **TONIGHT** | **0.0%** | **-$5.00** |

XRP fue responsable del **123%** de la pérdida total de V1 esta noche. Sin XRP, V1 habría cerrado con **+$0.93**.
Paradójicamente, XRP ya era ligeramente negativo en S2 (-$0.56), pero con solo 5 losses destruyó toda la sesión esta noche.

**Diagnóstico:** XRP en horario nocturno tiene movimientos erráticos y sin tendencia clara. Los indicadores EMA/RSI generan señales de momentum que son consistentemente falsas porque XRP no tiene suficiente volumen nocturno para sostener una tendencia de 5 minutos.

---

### Factor 2: Señales más Débiles (Score 0.485 vs 0.589)

| Sesión | Avg Signal Score |
|:---|:---:|
| S2 (noche ganadora) | **0.589** |
| S4 (tarde) | 0.593 |
| S1 (referencia) | 0.593 |
| **TONIGHT** | **0.485** (-17.6%) |

El score promedio de las señales esta noche fue un **17.6% más débil** que en todas las sesiones anteriores. Esto indica que los indicadores técnicos de Binance (EMA/RSI) no estaban encontrando tendencias claras — estaban generando señales "tibias" que apenas pasaban el threshold de 0.40.

**Diagnóstico:** Mercado choppy/lateral. Cuando las EMAs convergen y el RSI flota cerca de 50, el sistema genera scores de 0.45-0.50 que técnicamente pasan el filtro pero no tienen convicción real.

---

### Factor 3: El Timing de Entrada se Degradó (157s vs 88s)

| Sesión | Avg Entry Timing |
|:---|:---:|
| S2 (noche ganadora) | **88s** (minuto 1:28) |
| S1 (referencia) | 70s (minuto 1:10) |
| **TONIGHT** | **157s** (minuto 2:37) |

V1 esta noche entró en promedio al **minuto 2:37** del ciclo de 5 minutos. En su mejor sesión (S2), entraba al **minuto 1:28**. Eso es **69 segundos más tarde** — una eternidad en mercados de 5 minutos.

**Diagnóstico:** Las señales débiles (Factor 2) hacen que el bot necesite más ciclos de datos para cruzar el threshold, lo que retrasa la entrada. Entrar después del minuto 2 en un mercado de 5 minutos significa que gran parte del movimiento ya ocurrió y el R/R (risk/reward) se deteriora significativamente.

---

### Factor 4: Precio de Entrada Elevado ($0.560 vs $0.498)

| Sesión | Avg Entry | Win Entry | Loss Entry |
|:---|:---:|:---:|:---:|
| S2 (noche ganadora) | **$0.498** | $0.518 | $0.470 |
| **TONIGHT** | **$0.560** | $0.607 | $0.525 |

V1 pagó **$0.06 más por trade** esta noche. Cuando entras a $0.56 en lugar de $0.50, tu upside máximo baja de $0.50 a $0.44 (un -12%), pero tu downside se mantiene igual. La asimetría R/R se destruye.

**Diagnóstico:** Combinación del timing tardío (Factor 3) + mercado que se mueve antes de que V1 reaccione. Para cuando la señal cruza el threshold, el precio ya se movió de $0.50 a $0.56.

---

### Factor 5: Hold-Only Eliminó la Red de Seguridad

| Sesión | Exit Reasons | Impacto |
|:---|:---|:---|
| S2 (ganadora) | 29 TP + 6 SL + 24 Won + 18 Lost | **SL salvó +$2.09** |
| **TONIGHT** | 6 Won + 8 Lost (todo resolved) | **Sin protección** |

En S2, la V1 tenía Stop Loss activo que cortó 6 trades malos por +$2.09. Esta noche, al correr en `--hold-only`, cada trade perdedor costó exactamente -$1.00 (pérdida total del stake).

**Ejemplo concreto:** BTC UP @ $0.33 → Lost -$1.00. Con un SL del 30%, habría perdido -$0.30 en vez de -$1.00.

---

### Factor 6: Sesgo UP Fallido en Mercado Bajista (UP WR = 28.6%)

| Sesión | UP WR | UP PnL | DOWN WR | DOWN PnL |
|:---|:---:|:---:|:---:|:---:|
| S2 (ganadora) | 53.6% | +$2.74 | 59.2% | +$7.27 |
| **TONIGHT** | **28.6%** | **-$3.77** | 57.1% | -$0.30 |

V1 apostó UP 7 veces esta noche y solo acertó 2. El lado DOWN mantuvo un WR de 57.1% (similar a S2), pero el lado UP colapsó al 28.6%. Esto sugiere que el mercado nocturno de hoy tenía un sesgo bajista que los indicadores técnicos no capturaron correctamente, generando señales UP falsas.

---

## 3. Conclusión: El "Cocktail de la Muerte" de V1

La caída de V1 esta noche NO fue causada por un solo factor. Fue un **cocktail de 6 factores simultáneos:**

1. **XRP tóxico** (-$5.00) → eliminable con `--assets BTC,ETH`
2. **Señales débiles** (score -17.6%) → mercado choppy, no tiene solución directa
3. **Entrada tardía** (157s vs 88s) → consecuencia del punto 2
4. **Precios inflados** ($0.56 vs $0.50) → consecuencia del punto 3
5. **Sin Stop Loss** (hold-only) → eliminable quitando `--hold-only`
6. **Sesgo UP incorrecto** (28.6% WR) → consecuencia del punto 2

Los factores 2-4 y 6 son **inherentes al régimen de mercado** (choppy, sin tendencia). V1 usa EMA/RSI de Binance que son indicadores de momentum — funcionan cuando hay tendencia y fallan cuando no la hay. Eso no se puede "arreglar", es una característica del indicador.

Los factores 1 y 5 son **configuraciones operativas** que SÍ se pueden cambiar:
- Quitar XRP de noche habría convertido la sesión de -$4.07 a +$0.93
- Quitar hold-only habría reducido las pérdidas de los trades malos en ~50%

**Combinando ambos ajustes**, V1 esta noche habría terminado entre **+$2.00 y +$3.00** en vez de -$4.07.
