# HFT Scalper — Bitácora de Rendimiento

Registro continuo de análisis por sesión. Cada entrada compara los resultados actuales con la sesión anterior, identifica tendencias y documenta cambios en la configuración.

---

## Sesión 1 — 2026-05-05 | ~1pm–6pm CDT (Sesión de Referencia)
**Duración:** ~4h | **Bots activos:** 8 (V1–V6) | **Stake:** $1/trade | **Capital:** $24

### Contexto
Primera sesión masiva de paper-trading con todos los bots corriendo en paralelo. Mercado de BTC/ETH con SOL y XRP incluidos. Sin filtros de entry window en la mayoría.

### Leaderboard

| Bot | Trades | W/L | WR | P&L | ROI | Avg Entry |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **V6** | 7 | 6W/1L | 85.7% | +$2.18 | **+31.1%** | $0.489 |
| **V2OPT3** | 3 | 2W/1L | 66.7% | +$0.92 | **+30.7%** | $0.533 |
| **V2OPT2** | 21 | 12W/9L | 57.1% | +$3.43 | **+16.3%** | $0.490 |
| **V2OPT** | 55 | 37W/18L | 67.3% | +$8.59 | **+14.7%** | $0.524 |
| **V1** | 111 | 64W/47L | 57.7% | +$12.31 | **+11.1%** | $0.461 |
| **V5** | 51 | 29W/22L | 56.9% | +$4.54 | **+8.9%** | $0.514 |
| **V4** | 55 | 35W/20L | 63.6% | +$2.52 | **+4.6%** | $0.487 |
| **V2** | 66 | 38W/28L | 57.6% | +$2.78 | **+3.9%** | $0.523 |

### Cambios implementados post-sesión
- SOL eliminado de `HFT_ASSETS` y `HFT_TRADEABLE_ASSETS` globalmente
- `hold_to_resolution` parcheado para V1 (bug: usaba loop legacy que ignoraba `HOLD_ONLY`)
- Perfil **V7** creado: Poly Velocity + Entry Window 0-120s + Hold-to-Resolution + BTC/ETH/XRP

---

## Sesión 2 — 2026-05-06 | 1pm–6pm CDT
**Duración:** ~5h | **Bots activos:** 6 (V2, V2OPT, V2OPT2, V4, V5, V6 + nuevo V7) | **Stake:** $1/trade | **Capital:** $24
> ⚠️ V1 y V2OPT3 no tienen archivo de trades para esta sesión (archivos reiniciados/no encontrados). V7 también sin datos suficientes aún.

### Leaderboard

| Bot | Trades | W/L | WR | P&L | ROI | Avg Entry |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **V6** | 1 | 1W/0L | 100% | +$0.09 | **+9.0%** | $0.550 |
| **V2** | 49 | 28W/21L | 57.1% | +$5.51 | **+10.9%** | $0.506 |
| **V5** | 38 | 21W/17L | 55.3% | +$2.12 | **+5.6%** | $0.502 |
| **V2OPT2** | 15 | 7W/8L | 46.7% | +$0.55 | **+3.7%** | $0.444 |
| **V2OPT** | 46 | 25W/21L | 54.3% | +$1.12 | **+2.4%** | $0.478 |
| **V4** | 43 | 25W/18L | 58.1% | -$1.14 | **-2.7%** | $0.486 |

### Per-Asset (Sesión 2)

| Bot | BTC WR / P&L | ETH WR / P&L | XRP WR / P&L |
|:---|:---:|:---:|:---:|
| V2 | 53.3% / +$0.53 | 44.4% / +$0.40 | **75.0% / +$4.58** |
| V2OPT | 46.2% / -$0.84 | 55.0% / +$0.64 | 61.5% / +$1.32 |
| V2OPT2 | 42.9% / +$0.42 | 50.0% / +$0.35 | 50.0% / -$0.22 |
| V4 | **61.5% / +$0.78** | 45.5% / -$3.26 | 66.7% / +$1.34 |
| V5 | 55.0% / +$1.00 | 42.9% / -$2.01 | **100% / +$3.13** |

---

## Comparativa Sesión 1 vs Sesión 2

| Bot | ROI S1 | ROI S2 | Delta | WR S1 | WR S2 | Tendencia |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| V2 | +3.9% | +10.9% | **+7.0pp** | 57.6% | 57.1% | ✅ Mejoró (mismo WR, mejor eficiencia) |
| V2OPT | +14.7% | +2.4% | **-12.3pp** | 67.3% | 54.3% | ❌ Caída fuerte en WR |
| V2OPT2 | +16.3% | +3.7% | **-12.6pp** | 57.1% | 46.7% | ❌ WR cayó 10pp |
| V4 | +4.6% | -2.7% | **-7.3pp** | 63.6% | 58.1% | ❌ Entró en pérdidas |
| V5 | +8.9% | +5.6% | **-3.3pp** | 56.9% | 55.3% | ⚠️ Baja moderada, consistente |
| V6 | +31.1% | +9.0% | — | 85.7% | 100% | ✅ Solo 1 trade (sin datos suficientes) |

---

## Análisis de Consistencia

### ✅ Bots más consistentes entre sesiones

**1. V2 — El más consistente**
- WR casi idéntico (57.6% → 57.1%): señal estable
- ROI mejoró en S2: la eliminación de SOL (que le daba -$1.08 en S1) se refleja en mejor eficiencia
- **XRP está siendo su gran motor en S2**: 75% WR, +$4.58. Valida la decisión de mantener XRP

**2. V5 — Estable pero por debajo del potencial**
- WR muy consistente (56.9% → 55.3%), solo 1.6pp de baja
- En S2 XRP fue 100% WR (+$3.13): exactamente el patrón que vimos en S1
- ETH sigue siendo su punto débil en ambas sesiones

### ❌ Bots que cayeron entre sesiones

**V2OPT** — WR cayó -13pp (67.3% → 54.3%)
- Probable causa: el mercado de S2 (1pm-6pm) tiene más ruido/reversal que la sesión de madrugada de S1
- BTC le costó -$0.84 en S2 (era su mejor asset en S1)

**V2OPT2** — WR cayó -10pp
- Solo 15 trades (muy pocos para ser concluyente)
- El entry window 0-120s está captando mercados menos favorables en horario diurno

**V4** — Primer ROI negativo
- ETH fue un desastre en S2: 45.5% WR, -$3.26. BTC lo rescató parcialmente

---

## Insight del día

> [!IMPORTANT]
> **XRP emerge como activo sorpresa en sesión diurna.** En S1 nocturna, XRP era mixto (+$5.00 en V2OPT pero -$5.86 en V1-archive). En S2 diurna, XRP domina: V2 75%WR, V5 100%WR. Esto sugiere que XRP tiene mejor comportamiento en horario de mayor liquidez (1pm-6pm CDT).

> [!WARNING]
> **ETH underperforma en horario diurno.** En S1 ETH era sólido (+$3.51 en V2OPT). En S2 ETH es el peor asset: V4 (-$3.26), V5 (-$2.01), V2 apenas +$0.40. Posiblemente el horario diurno americano trae más presión de venta en ETH.

> [!NOTE]
> **V7 y V6 tienen datos insuficientes.** V6 hizo 1 solo trade (XRP, ganó). V7 sin archivo de trades. Se necesitan más ciclos para conclusiones.

---

## Próximas sesiones a registrar
- [ ] Sesión nocturna 2026-05-06 (después de 6pm CDT)
- [ ] Primera sesión completa de V7
- [ ] Sesión de mañana (9am-12pm CDT) — ¿cambia el patrón de XRP/ETH?
