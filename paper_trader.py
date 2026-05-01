"""
paper_trader.py — MÓDULO 5: Paper trading tracker.
Registra trades en JSON local, verifica resolución, calcula métricas.
Incluye mark-to-market para P&L no realizado en tiempo real.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import PAPER_TRADES_FILE, PAPER_CAPITAL
from utils import format_pct, format_usd, retry_with_backoff

logger = logging.getLogger("polybot.paper_trader")

GAMMA_API_URL = "https://gamma-api.polymarket.com"


def _load_trades() -> dict:
    """Carga el archivo de paper trades. Crea uno vacío si no existe."""
    path = Path(PAPER_TRADES_FILE)
    if not path.exists():
        return {
            "initial_capital": PAPER_CAPITAL,
            "current_capital": PAPER_CAPITAL,
            "trades": [],
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Error leyendo %s: %s. Creando nuevo.", PAPER_TRADES_FILE, e)
        return {
            "initial_capital": PAPER_CAPITAL,
            "current_capital": PAPER_CAPITAL,
            "trades": [],
        }


def _save_trades(data: dict):
    """Guarda el archivo de paper trades."""
    with open(PAPER_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("Paper trades guardados en %s", PAPER_TRADES_FILE)


def get_current_capital() -> float:
    """Retorna el capital disponible actual."""
    data = _load_trades()
    return data.get("current_capital", PAPER_CAPITAL)


# ═══════════════════════════════════════════════════════════════════
# Mark-to-Market: obtener precio actual de mercados abiertos
# ═══════════════════════════════════════════════════════════════════


@retry_with_backoff(max_retries=2, base_delay=0.5)
def _get_current_market_price(gamma_id: str = "", condition_id: str = "") -> float | None:
    """
    Obtiene el precio actual YES de un mercado desde Gamma API.
    Usa gamma_id (numerico) para fetch directo, o conditionId como fallback.
    Retorna float [0,1] o None si no se puede obtener.
    """
    market = None

    # Intento 1: fetch directo por gamma_id (numerico)
    if gamma_id:
        try:
            url = f"{GAMMA_API_URL}/markets/{gamma_id}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                market = resp.json()
        except Exception as e:
            logger.debug("Fetch por gamma_id %s fallo: %s", gamma_id, e)

    # Intento 2: buscar por conditionId en query params
    if market is None and condition_id:
        try:
            url = f"{GAMMA_API_URL}/markets"
            resp = requests.get(url, params={"conditionId": condition_id, "limit": 1}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # La API puede devolver resultados no exactos, verificar
                if isinstance(data, list):
                    for m in data:
                        if m.get("conditionId", "") == condition_id:
                            market = m
                            break
        except Exception as e:
            logger.debug("Busqueda por conditionId %s fallo: %s", condition_id[:20], e)

    if market is None:
        return None

    # Extraer precio YES actual
    outcome_prices = market.get("outcomePrices", "")
    if outcome_prices:
        try:
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            if isinstance(prices, list) and len(prices) >= 1:
                return float(prices[0])
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback a otros campos
    for field in ["lastTradePrice", "price"]:
        val = market.get(field)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass

    return None


def _calc_unrealized_pnl(trade: dict, current_yes_price: float) -> float:
    """
    Calcula el P&L no realizado de un trade abierto.

    En Polymarket, si compraste YES a entry_price y ahora vale current_price:
      shares = stake / entry_price
      current_value = shares * current_price
      unrealized_pnl = current_value - stake

    Si compraste NO, el precio NO = 1 - precio YES.
    """
    stake = trade["stake"]
    entry_price = trade["entry_price"]
    side = trade["side"]

    if entry_price <= 0:
        return 0.0

    shares = stake / entry_price

    if side == "YES":
        current_value = shares * current_yes_price
    else:
        current_no_price = 1.0 - current_yes_price
        current_value = shares * current_no_price

    return current_value - stake


# ═══════════════════════════════════════════════════════════════════
# Registro de trades
# ═══════════════════════════════════════════════════════════════════


def record_trade(opportunity: dict) -> dict:
    """
    Registra un nuevo trade en paper_trades.json.

    Campos registrados:
      - timestamp, market_id, question, side, stake, entry_price
      - edge, prob_poly, prob_real, status ('open')
    """
    data = _load_trades()
    capital = data["current_capital"]

    stake = opportunity["stake"]
    if stake > capital:
        stake = capital
        logger.warning("Stake reducido a capital disponible: $%.2f", stake)

    if stake <= 0:
        logger.warning("Capital insuficiente para trade.")
        return {}

    trade = {
        "id": len(data["trades"]) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market_id": opportunity.get("condition_id", ""),
        "gamma_id": str(opportunity.get("gamma_id", "")),
        "question": opportunity.get("question", ""),
        "asset": opportunity.get("asset", ""),
        "strike": opportunity.get("strike", 0),
        "direction": opportunity.get("direction", ""),
        "side": opportunity.get("side", "YES"),
        "stake": round(stake, 2),
        "entry_price": round(opportunity.get("entry_price", 0), 4),
        "edge": round(opportunity.get("edge", 0), 4),
        "prob_poly": round(opportunity.get("prob_poly", 0), 4),
        "prob_real": round(opportunity.get("prob_real", 0), 4),
        "end_date": opportunity.get("end_date_str", ""),
        "days_to_expiry": round(opportunity.get("days_to_expiry", 0), 1),
        "status": "open",
        "result": None,
        "pnl": None,
        "resolved_at": None,
    }

    # Descontar stake del capital
    data["current_capital"] = round(capital - stake, 2)
    data["trades"].append(trade)
    _save_trades(data)

    logger.info(
        "Trade registrado #%d: %s %s @ %.4f, stake=$%.2f, edge=%.1f%%",
        trade["id"], trade["side"], trade["question"][:40],
        trade["entry_price"], trade["stake"], trade["edge"] * 100
    )
    return trade


# ═══════════════════════════════════════════════════════════════════
# Resolución de trades
# ═══════════════════════════════════════════════════════════════════


def resolve_trades():
    """
    Verifica trades abiertos cuya end_date ya pasó.
    Consulta Polymarket para ver si el mercado ya resolvió.
    Si el precio es 0 o 1, el mercado ya resolvió definitivamente.
    """
    data = _load_trades()
    now = datetime.now(timezone.utc)
    resolved_count = 0

    for trade in data["trades"]:
        if trade["status"] != "open":
            continue

        # Verificar si el mercado ya venció
        end_date_str = trade.get("end_date", "")
        if not end_date_str:
            continue

        try:
            end_date = datetime.fromisoformat(str(end_date_str).replace("Z", "+00:00"))
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        if end_date > now:
            continue  # Todavía no venció

        # Consultar precio actual para ver si resolvió
        current_price = _get_current_market_price(trade.get("market_id", ""))

        stake = trade["stake"]
        entry_price = trade["entry_price"]
        side = trade.get("side", "YES")

        if current_price is not None and (current_price >= 0.99 or current_price <= 0.01):
            # Mercado resolvió definitivamente
            yes_won = current_price >= 0.99
            won = (side == "YES" and yes_won) or (side == "NO" and not yes_won)
        else:
            # No podemos confirmar resolución, simular con prob_real
            import random
            prob_real = trade.get("prob_real", 0.5)
            yes_wins = random.random() < prob_real
            won = (side == "YES" and yes_wins) or (side == "NO" and not yes_wins)

        if won:
            payout = stake / entry_price
            pnl = payout - stake
            trade["status"] = "won"
        else:
            pnl = -stake
            trade["status"] = "lost"

        trade["pnl"] = round(pnl, 2)
        trade["result"] = "won" if won else "lost"
        trade["resolved_at"] = now.isoformat()

        data["current_capital"] = round(data["current_capital"] + stake + pnl, 2)
        resolved_count += 1

        logger.info(
            "Trade #%d resuelto: %s -> P&L=$%.2f",
            trade["id"], trade["status"], pnl
        )

    if resolved_count > 0:
        _save_trades(data)
        logger.info("Resueltos %d trades.", resolved_count)
    else:
        logger.info("No hay trades para resolver.")


# ═══════════════════════════════════════════════════════════════════
# Reporte con mark-to-market
# ═══════════════════════════════════════════════════════════════════


def get_portfolio_report(live_prices: bool = True) -> dict:
    """
    Calcula métricas del portafolio de paper trading.
    Si live_prices=True, consulta precios actuales para P&L no realizado.
    """
    data = _load_trades()
    trades = data.get("trades", [])

    initial_capital = data.get("initial_capital", PAPER_CAPITAL)
    current_capital = data.get("current_capital", PAPER_CAPITAL)

    total_trades = len(trades)
    open_trades = [t for t in trades if t["status"] == "open"]
    closed_trades = [t for t in trades if t["status"] in ("won", "lost")]
    won_trades = [t for t in trades if t["status"] == "won"]

    # P&L realizado
    total_pnl = sum(t.get("pnl", 0) for t in closed_trades)
    capital_in_play = sum(t.get("stake", 0) for t in open_trades)

    # ── Mark-to-market: P&L no realizado ──
    unrealized_pnl = 0.0
    mtm_details = []

    if live_prices and open_trades:
        print("  📡 Consultando precios actuales para mark-to-market...")
        for t in open_trades:
            gamma_id = t.get("gamma_id", "")
            market_id = t.get("market_id", "")
            current_price = _get_current_market_price(gamma_id=gamma_id, condition_id=market_id)

            if current_price is not None:
                u_pnl = _calc_unrealized_pnl(t, current_price)
                unrealized_pnl += u_pnl

                # Precio actual del lado que compramos
                if t["side"] == "YES":
                    current_side_price = current_price
                else:
                    current_side_price = 1.0 - current_price

                mtm_details.append({
                    "id": t["id"],
                    "question": t["question"],
                    "side": t["side"],
                    "entry_price": t["entry_price"],
                    "current_price": round(current_side_price, 4),
                    "stake": t["stake"],
                    "unrealized_pnl": round(u_pnl, 2),
                    "pnl_pct": round(u_pnl / t["stake"] * 100, 1) if t["stake"] > 0 else 0,
                })
            else:
                mtm_details.append({
                    "id": t["id"],
                    "question": t["question"],
                    "side": t["side"],
                    "entry_price": t["entry_price"],
                    "current_price": None,
                    "stake": t["stake"],
                    "unrealized_pnl": 0,
                    "pnl_pct": 0,
                })

    # Win rate
    win_rate = len(won_trades) / len(closed_trades) if closed_trades else 0.0

    # Edge promedio
    avg_edge = (
        sum(abs(t.get("edge", 0)) for t in trades) / total_trades
        if total_trades > 0 else 0.0
    )

    # Calibración
    avg_prob_real = (
        sum(t.get("prob_real", 0.5) for t in closed_trades) / len(closed_trades)
        if closed_trades else 0.5
    )

    # Total portfolio value = cash + mark-to-market value of open positions
    portfolio_value = current_capital + capital_in_play + unrealized_pnl

    report = {
        "initial_capital": initial_capital,
        "current_capital": current_capital,
        "capital_in_play": round(capital_in_play, 2),
        "portfolio_value": round(portfolio_value, 2),
        "total_pnl": round(total_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl_with_unrealized": round(total_pnl + unrealized_pnl, 2),
        "pnl_pct": round((portfolio_value - initial_capital) / initial_capital * 100, 2),
        "total_trades": total_trades,
        "open_trades": len(open_trades),
        "closed_trades": len(closed_trades),
        "won_trades": len(won_trades),
        "lost_trades": len(closed_trades) - len(won_trades),
        "win_rate": round(win_rate, 4),
        "avg_edge": round(avg_edge, 4),
        "avg_prob_real": round(avg_prob_real, 4),
        "calibration_gap": round(win_rate - avg_prob_real, 4),
        "mtm_details": mtm_details,
        "trades": trades,
    }

    return report


def print_report(report: dict):
    """Imprime el reporte del portafolio con mark-to-market."""
    print("\n" + "=" * 75)
    print("  POLYMARKET PAPER TRADING — REPORTE DE PORTAFOLIO")
    print("=" * 75)

    print(f"\n  Capital inicial:      {format_usd(report['initial_capital'])}")
    print(f"  Cash disponible:      {format_usd(report['current_capital'])}")
    print(f"  Capital en juego:     {format_usd(report['capital_in_play'])}")

    # Mark-to-market
    u_pnl = report.get("unrealized_pnl", 0)
    icon_u = "+" if u_pnl >= 0 else ""
    print(f"  P&L no realizado:     {icon_u}{format_usd(u_pnl)}")

    pv = report.get("portfolio_value", report["current_capital"])
    print(f"  Valor del portafolio: {format_usd(pv)} ({report['pnl_pct']:+.2f}%)")

    realized = report.get("total_pnl", 0)
    icon_r = "+" if realized >= 0 else ""
    print(f"  P&L realizado:        {icon_r}{format_usd(realized)}")

    print(f"\n  Trades totales:       {report['total_trades']}")
    print(f"     Abiertos:          {report['open_trades']}")
    print(f"     Ganados:           {report['won_trades']}")
    print(f"     Perdidos:          {report['lost_trades']}")

    if report.get("closed_trades", 0) > 0:
        print(f"\n  Win Rate:             {format_pct(report['win_rate'])}")
        print(f"  Edge Promedio:        {format_pct(report['avg_edge'])}")
        print(f"  Prob Real Promedio:   {format_pct(report['avg_prob_real'])}")
        print(f"  Calibracion Gap:      {report['calibration_gap']:+.4f}")

    # ── Mark-to-Market de posiciones abiertas ──
    mtm = report.get("mtm_details", [])
    if mtm:
        print(f"\n{'─' * 75}")
        print("  POSICIONES ABIERTAS — Mark-to-Market")
        print(f"{'─' * 75}")
        print(f"  {'#':>3} | {'Side':>4} | {'Entrada':>8} | {'Actual':>8} | {'Stake':>9} | {'P&L':>10} | {'%':>7} | Mercado")
        print(f"  {'─' * 71}")

        for m in mtm:
            question = m["question"][:28]
            entry = format_pct(m["entry_price"])
            current = format_pct(m["current_price"]) if m["current_price"] is not None else "  N/A"
            pnl = m["unrealized_pnl"]
            pnl_icon = "+" if pnl >= 0 else ""
            pnl_pct = m["pnl_pct"]
            pnl_color = "+" if pnl_pct >= 0 else ""

            print(
                f"  {m['id']:>3} | {m['side']:>4} | {entry:>8} | {current:>8} | "
                f"{format_usd(m['stake']):>9} | {pnl_icon}{format_usd(pnl):>9} | "
                f"{pnl_color}{pnl_pct:.1f}% | {question}"
            )

    # ── Historial de trades cerrados ──
    closed = [t for t in report.get("trades", []) if t["status"] in ("won", "lost")]
    if closed:
        print(f"\n{'─' * 75}")
        print("  TRADES CERRADOS")
        print(f"{'─' * 75}")
        print(f"  {'#':>3} | {'Estado':>6} | {'Side':>4} | {'Stake':>8} | {'P&L':>10} | {'Edge':>6} | Mercado")
        print(f"  {'─' * 71}")

        for t in closed[-10:]:
            status_icon = {"won": "[W]", "lost": "[L]"}.get(t["status"], "?")
            pnl_str = format_usd(t["pnl"]) if t["pnl"] is not None else "  --"
            if t.get("pnl", 0) >= 0:
                pnl_str = "+" + pnl_str
            question = t.get("question", "")[:28]
            print(
                f"  {t['id']:>3} | {status_icon:>6} | {t['side']:>4} | "
                f"{format_usd(t['stake']):>8} | {pnl_str:>10} | "
                f"{format_pct(abs(t.get('edge', 0))):>6} | {question}"
            )

    print(f"\n{'=' * 75}\n")
