# 🤖 Polymarket Value Betting Bot — Paper Trading

Bot de paper trading para Polymarket enfocado en mercados de precio de crypto (BTC/ETH). Detecta mercados mal calibrados comparando la probabilidad implícita en Polymarket contra fuentes externas.

## Estrategia: Value Betting

El bot identifica **discrepancias** entre la probabilidad que el mercado de Polymarket asigna a un evento y la probabilidad "real" estimada por modelos externos:

```
edge = prob_real - prob_poly
```

Si `|edge| > 6%`, el mercado está potencialmente mal calibrado y representa una oportunidad de trading.

## Fuentes de Probabilidad

| Fuente | Peso | Descripción |
|--------|------|-------------|
| **GBM / Black-Scholes** | 40% | Modelo log-normal con volatilidad histórica de 30d de Binance |
| **Deribit Options** | 40% | Delta de la opción más cercana al strike/vencimiento como proxy de probabilidad implícita |
| **Fear & Greed Index** | 20% | Ajuste de sentimiento macro: Fear (<30) → -3%, Greed (>70) → +3% |

Si una fuente no está disponible, los pesos se redistribuyen automáticamente.

## Arquitectura

```
bot.py                  ← CLI principal (argparse)
├── config.py           ← Configuración centralizada (.env + constantes)
├── utils.py            ← Retry, parsing de preguntas, formateo
├── polymarket_client.py← Conexión API REST a Polymarket CLOB
├── probability.py      ← 3 fuentes de probabilidad (GBM, Deribit, FNG)
├── strategy.py         ← Filtros de oportunidad + Kelly sizing
└── paper_trader.py     ← Registro JSON + resolución + métricas
```

## Instalación

### 1. Clonar y crear entorno virtual

```bash
cd polystudio
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate
```

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3. Configurar credenciales

```bash
cp .env.example .env
# Editar .env con tus credenciales de Polymarket
```

Variables requeridas en `.env`:

| Variable | Descripción |
|----------|-------------|
| `POLY_PRIVATE_KEY` | Clave privada de tu wallet (para futuro modo live) |
| `POLY_API_KEY` | API key de Polymarket |
| `POLY_API_SECRET` | API secret |
| `POLY_API_PASSPHRASE` | API passphrase |
| `PAPER_CAPITAL` | Capital inicial para paper trading (default: 1000) |
| `MIN_EDGE` | Edge mínimo para considerar un trade (default: 0.06) |

> **Nota:** Para el modo paper, las credenciales de Polymarket son opcionales — los mercados se obtienen de la API pública.

## Uso

### Modo Scan — Solo muestra oportunidades

```bash
python bot.py --mode scan
```

Output:
```
  🔍 OPORTUNIDADES DETECTADAS
══════════════════════════════════════════════════════════════════════
  Mercado                                  | Vence   | Poly% | Real% | Edge  | Dir   | Side | Stake
  ────────────────────────────────────────────────────────────────────
  🟢 Will BTC be above $120,000 by Jul... |    23d  | 42.0% | 55.3% | +13.3%| above |  YES |  $87.50
  🟡 Will ETH exceed $5,000 before...     |    45d  | 35.0% | 42.1% |  +7.1%| above |  YES |  $32.00
```

### Modo Paper — Escanea y registra trades

```bash
python bot.py --mode paper
```

- Resuelve trades previos que ya vencieron
- Escanea nuevas oportunidades
- Registra trades automáticamente en `paper_trades.json`

### Modo Report — Performance del portafolio

```bash
python bot.py --mode report
```

Output:
```
  📊 POLYMARKET PAPER TRADING — REPORTE DE PORTAFOLIO
══════════════════════════════════════════════════════════
  💰 Capital inicial:     $1,000.00
  💰 Capital actual:      $1,125.50
  📈 P&L Total:           $125.50 (+12.55%)
  🎯 Win Rate:            62.5%
  📐 Edge Promedio:        8.3%
```

### Modo Live — Placeholder

```bash
python bot.py --mode live
# ⚠️ No implementado — muestra instrucciones
```

## Sizing: Kelly Fraccionado

El bot usa el criterio de Kelly fraccionado (¼ Kelly) para sizing conservador:

```python
kelly = max(0, (p*b - q) / b)
stake = capital * kelly * 0.25  # Cuarto de Kelly
```

Límites:
- **Máximo**: 5% del capital por trade
- **Mínimo**: $5 por trade

## Filtros de Oportunidad

Un mercado solo se considera operable si:
- `|edge| > 6%` (diferencia de probabilidad)
- Volumen 24h > $50,000
- Días para vencer: entre 3 y 60
- La pregunta contiene un precio numérico parseable

## Logs

Los logs se guardan en `bot.log` con nivel INFO. Los errores de API se reintentan 3 veces con backoff exponencial.

## Estructura de paper_trades.json

```json
{
  "initial_capital": 1000,
  "current_capital": 912.50,
  "trades": [
    {
      "id": 1,
      "timestamp": "2025-06-15T10:30:00+00:00",
      "market_id": "0x...",
      "question": "Will BTC be above $120,000...",
      "side": "YES",
      "stake": 87.50,
      "entry_price": 0.42,
      "edge": 0.133,
      "prob_poly": 0.42,
      "prob_real": 0.553,
      "status": "open"
    }
  ]
}
```

## ⚠️ Disclaimer

Este bot es exclusivamente para **paper trading y propósitos educativos**. No ejecuta órdenes reales ni mueve fondos. El trading en mercados de predicción conlleva riesgos significativos. Los modelos de probabilidad son estimaciones y no garantizan resultados.
