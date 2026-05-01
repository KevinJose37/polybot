#!/usr/bin/env python3
"""
bot.py — MÓDULO 6: CLI principal del bot de paper trading para Polymarket.

Modos de ejecución:
  python bot.py --mode scan    # Solo muestra oportunidades
  python bot.py --mode paper   # Escanea y registra trades en papel
  python bot.py --mode report  # Muestra performance del portafolio
  python bot.py --mode scalp   # HFT scalper para mercados de 5 minutos

El bot opera en MODO PAPER por defecto — nunca ejecuta órdenes reales.
"""

import argparse
import io
import logging
import sys
from datetime import datetime, timezone

# ── Forzar UTF-8 en Windows para soportar emojis y box-drawing ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from config import setup_logging, PAPER_CAPITAL, MIN_EDGE
from polymarket_client import get_crypto_markets
from strategy import scan_opportunities
from paper_trader import (
    get_current_capital,
    record_trade,
    resolve_trades,
    get_portfolio_report,
    print_report,
)
from utils import format_pct, format_usd

logger = logging.getLogger("polybot.main")


# ═══════════════════════════════════════════════════════════════════
# Funciones de display
# ═══════════════════════════════════════════════════════════════════


def print_banner():
    """Banner del bot."""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   🤖  POLYMARKET VALUE BETTING BOT  — Paper Trading Edition     ║
║                                                                  ║
║   Estrategia: Value Betting (detectar mercados mal calibrados)  ║
║   Assets: BTC, ETH                                               ║
║   Fuentes: GBM + Deribit Options + Fear & Greed Index           ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
    """)


def print_opportunities_table(opportunities: list[dict]):
    """
    Imprime tabla de oportunidades ordenada por abs(edge) desc.
    Columnas: Mercado | Vence | Poly% | Real% | Edge | Dirección | Stake
    """
    if not opportunities:
        print("\n  ℹ️  No se encontraron oportunidades con edge > "
              f"{format_pct(MIN_EDGE)}.\n")
        return

    print(f"\n{'═' * 100}")
    print("  🔍 OPORTUNIDADES DETECTADAS")
    print(f"{'═' * 100}")

    header = (
        f"  {'Mercado':<40} | {'Vence':>8} | {'Poly%':>6} | "
        f"{'Real%':>6} | {'Edge':>6} | {'Dir':>5} | {'Side':>4} | {'Stake':>9}"
    )
    print(header)
    print(f"  {'─' * 96}")

    for opp in opportunities:
        question = opp["question"][:38]
        days = opp.get("days_to_expiry", 0)
        vence = f"{days:.0f}d"
        poly_pct = format_pct(opp["prob_poly"])
        real_pct = format_pct(opp["prob_real"])
        edge_pct = f"{opp['edge'] * 100:+.1f}%"
        direction = opp.get("direction", "?")[:5]
        side = opp["side"]
        stake = format_usd(opp["stake"])

        # Color indicator
        edge_val = opp["edge"]
        indicator = "🟢" if abs(edge_val) > 0.10 else "🟡"

        print(
            f"  {indicator} {question:<38} | {vence:>7} | {poly_pct:>6} | "
            f"{real_pct:>6} | {edge_pct:>6} | {direction:>5} | {side:>4} | {stake:>9}"
        )

    print(f"{'═' * 100}\n")
    print(f"  Total: {len(opportunities)} oportunidades encontradas.\n")


# ═══════════════════════════════════════════════════════════════════
# Modos de ejecución
# ═══════════════════════════════════════════════════════════════════


def mode_scan():
    """Modo scan: muestra oportunidades sin ejecutar trades."""
    print("\n  🔍 Modo SCAN — Buscando oportunidades...\n")

    capital = get_current_capital()
    print(f"  💰 Capital disponible: {format_usd(capital)}")

    print("  📡 Obteniendo mercados de Polymarket...")
    markets = get_crypto_markets()
    print(f"  📊 {len(markets)} mercados crypto encontrados.")

    print("  🧮 Analizando probabilidades y calculando edges...")
    opportunities = scan_opportunities(markets, capital)

    print_opportunities_table(opportunities)

    return opportunities


def mode_paper():
    """
    Modo paper: escanea oportunidades y registra trades en papel.
    Primero resuelve trades previos que ya vencieron.
    """
    print("\n  📝 Modo PAPER TRADING — Escaneando y registrando...\n")

    # Resolver trades previos
    print("  🏁 Verificando resolución de trades previos...")
    resolve_trades()

    # Escanear nuevas oportunidades
    opportunities = mode_scan()

    if not opportunities:
        print("  ℹ️  Sin oportunidades para registrar.\n")
        return

    # Registrar trades
    print(f"\n  📝 Registrando {len(opportunities)} trades en papel...\n")

    registered = 0
    for opp in opportunities:
        trade = record_trade(opp)
        if trade:
            registered += 1
            print(
                f"  ✅ Trade #{trade.get('id', '?')}: {trade['side']} "
                f"{trade['question'][:35]}... @ {trade['entry_price']:.4f} "
                f"→ Stake: {format_usd(trade['stake'])}"
            )

    capital = get_current_capital()
    print(f"\n  💰 Capital restante: {format_usd(capital)}")
    print(f"  📊 Trades registrados: {registered}\n")


def mode_report():
    """Modo report: muestra el reporte de performance del portafolio."""
    # Primero resolver trades vencidos
    resolve_trades()

    report = get_portfolio_report()
    print_report(report)


def mode_scalp(assets_filter: str | None = None, stake_override: float | None = None):
    """
    Modo scalp: HFT scalper para mercados de 5 minutos.
    Opera en loop continuo escaneando BTC/ETH/SOL/XRP.
    """
    from scalper.config import HFT_ASSETS, HFT_STAKE
    from scalper.runner import run_scalper

    # Filter assets if specified
    target_assets = None
    if assets_filter:
        selected = [a.strip().upper() for a in assets_filter.split(",")]
        target_assets = {k: v for k, v in HFT_ASSETS.items() if k in selected}
        if not target_assets:
            print(f"  ❌ Assets no válidos: {assets_filter}")
            print(f"  ℹ️  Assets disponibles: {', '.join(HFT_ASSETS.keys())}")
            sys.exit(1)

    # Override stake if specified
    if stake_override:
        import scalper.config as scalper_cfg
        scalper_cfg.HFT_STAKE = stake_override

    run_scalper(target_assets=target_assets)


def mode_live():
    """
    Placeholder para modo live (no implementado).
    Nunca ejecuta órdenes reales.
    """
    print("\n  ⚠️  Modo LIVE no implementado todavía.")
    print("  ℹ️  Este bot solo opera en modo PAPER por seguridad.\n")
    print("  Para implementar modo live, se requiere:")
    print("     - Configurar wallet con fondos en Polygon")
    print("     - Implementar firma de órdenes con py-clob-client")
    print("     - Agregar confirmaciones de seguridad")
    print("     - Implementar circuit breakers\n")
    sys.exit(0)


# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Value Betting Bot — Paper Trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:
  python bot.py --mode scan       Solo muestra oportunidades
  python bot.py --mode paper      Escanea y registra trades en papel
  python bot.py --mode report     Muestra performance del portafolio
  python bot.py --mode scalp      HFT scalper para mercados de 5min
  python bot.py --mode scalp --assets BTC,ETH --stake 15
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["scan", "paper", "report", "scalp", "live"],
        default="scan",
        help="Modo de ejecución (default: scan)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=None,
        help=f"Capital inicial override (default: ${PAPER_CAPITAL})",
    )
    parser.add_argument(
        "--assets",
        type=str,
        default=None,
        help="Assets para scalp mode, separados por coma (e.g. BTC,ETH)",
    )
    parser.add_argument(
        "--stake",
        type=float,
        default=None,
        help="Stake override por trade en scalp mode (default: $10)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging()
    logger.info("Bot iniciado en modo: %s", args.mode)

    # Scalp mode has its own banner and flow
    if args.mode == "scalp":
        try:
            mode_scalp(
                assets_filter=args.assets,
                stake_override=args.stake,
            )
        except KeyboardInterrupt:
            print("\n\n  ⛔ Interrumpido por el usuario.\n")
            sys.exit(0)
        except Exception as e:
            logger.exception("Error fatal: %s", e)
            print(f"\n  ❌ Error: {e}\n")
            sys.exit(1)
        return

    # Banner for value betting modes
    print_banner()
    print(f"  ⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  🎯 Min Edge: {format_pct(MIN_EDGE)}")
    print(f"  💰 Capital config: {format_usd(PAPER_CAPITAL)}")

    # Dispatch
    modes = {
        "scan": mode_scan,
        "paper": mode_paper,
        "report": mode_report,
        "live": mode_live,
    }

    try:
        modes[args.mode]()
    except KeyboardInterrupt:
        print("\n\n  ⛔ Interrumpido por el usuario.\n")
        sys.exit(0)
    except Exception as e:
        logger.exception("Error fatal: %s", e)
        print(f"\n  ❌ Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
