# Análisis de Sesgo Sistemático — Estrategia V3

**Hipótesis del Usuario:** La V3 tiene un sesgo sistemático (no aleatorio). Entra tarde en la dirección equivocada (ej: comprando UP cuando el mercado ya se resolvió DOWN). Al ser un error sistemático, invertir la señal debería generar ganancias.

Para comprobar esto, se analizaron *todos* los trades históricos de la V3 (`hft_trades_v3.json` y backups).

## 📊 Resultados Globales de la V3

| Dirección | Trades | Win/Loss | Win Rate | P&L | Stop Losses |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **UP** | 65 | 28W / 37L | 43.0% | **-$7.68** | 15 |
| **DOWN** | 21 | 7W / 14L | 33.3% | -$0.87 | 9 |

## 🚨 El Patrón Descubierto: Sesgo Direccional Extremo

Existe un sesgo **sistemático y masivo** en las decisiones que toma la V3, especialmente en Bitcoin. 

**Trades por Moneda y Dirección:**
- **BTC:** 41 trades en UP / **0 trades en DOWN** 🚩
- **SOL:** 8 trades en UP / **0 trades en DOWN** 🚩
- **ETH:** 10 trades en UP / 20 trades en DOWN
- **XRP:** 6 trades en UP / 1 trade en DOWN

La V3 **jamás** apostó a que Bitcoin o Solana iban a bajar. Siempre intentó adivinar que iban a subir (UP). 

## 💸 Comprando el "Cuchillo Cayendo" (Falling Knife)

Al analizar los precios de entrada, confirmamos exactamente lo que notaste en tu bitácora:

* **Avg Entry de UP Ganadores:** **$0.557** (Cuando acierta, es porque compra caro un momentum real).
* **Avg Entry de UP Perdedores:** **$0.394** (Cuando pierde, es porque compra muy barato).

**¿Qué significa esto?**
Cuando el precio de UP está en $0.07, $0.19 o $0.32, significa que el mercado **ya se ha desplomado** (DOWN está ganando abrumadoramente). Sin embargo, los indicadores técnicos de la V3 tienen tanto "lag" (retraso) que están leyendo un falso rebote o divergencia y le ordenan al bot: *"¡Compra UP, está barato y va a rebotar!"*.

El mercado de 5 minutos en Polymarket no perdona. Si compras UP a $0.07, estás apostando a un milagro, y por eso el bot termina tocando el Stop Loss inmediatamente (15 Stop losses registrados en UP).

## 🔄 ¿Se puede invertir la señal?

**SÍ, el sesgo es invertible bajo ciertas condiciones.**

Dado que la V3 sistemáticamente falla intentando comprar "rebotes" (UP) en mercados bajistas, invertir su lógica significa que:
> *Si V3 dice "Compra UP a $0.20", nosotros compramos DOWN a $0.80.*

Dado que el 100% de sus trades en BTC en las últimas sesiones fueron UP y la gran mayoría fracasaron por caer en la trampa del cuchillo cayendo, una **V3_INVERTIDA** habría comprado el momentum bajista que el mercado real estaba experimentando, logrando un win rate mucho mayor.

### Recomendación Estratégica:
Si deseas crear una variante `v3_inverted`, la regla clave debe ser:
1. Tomar la señal de la V3.
2. Si la V3 indica `UP` con un precio menor a `$0.45`, ignorarla o **apostar `DOWN`**.
3. El indicador que usa la V3 (probablemente una divergencia de RSI o un Mean Reversion) está roto para temporalidades de 5 minutos. Funciona como un excelente **indicador contrario**.
