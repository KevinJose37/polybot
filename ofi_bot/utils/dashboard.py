import asyncio
import os
from loguru import logger

async def print_dashboard(paper_trader, latest_logs=None):
    """
    Limpia la consola cada cierto tiempo y muestra un resumen 
    visual interactivo del Paper Trader.
    """
    while True:
        await asyncio.sleep(5)
        
        # Limpiar consola robustamente (Windows/Linux/Mac y terminales modernos)
        os.system('cls' if os.name == 'nt' else 'clear')
        print('\033[2J\033[H', end='')
        
        print("="*65)
        print("📊 POLYMARKET HFT BOT - PAPER TRADING DASHBOARD 📊")
        print("="*65)
        print(f"💰 Balance Actual:   ${paper_trader.balance:.2f} USDC")
        
        # Color del PnL
        pnl_str = f"${paper_trader.total_pnl:.2f} USDC"
        if paper_trader.total_pnl > 0:
            print(f"📈 Total P&L:        \033[92m{pnl_str}\033[0m")
        elif paper_trader.total_pnl < 0:
            print(f"📉 Total P&L:        \033[91m{pnl_str}\033[0m")
        else:
            print(f"➖ Total P&L:        {pnl_str}")
            
        total_bets = paper_trader.wins + paper_trader.losses
        win_rate = (paper_trader.wins / total_bets * 100) if total_bets > 0 else 0.0
        print(f"🎯 Win Rate Global:  {win_rate:.1f}% ({paper_trader.wins}W / {paper_trader.losses}L)")
        
        if paper_trader.stats_by_type:
            print("-" * 65)
            print("🏆 Consolidado por Mercado:")
            for key, stats in paper_trader.stats_by_type.items():
                t_bets = stats["wins"] + stats["losses"]
                wr = (stats["wins"] / t_bets * 100) if t_bets > 0 else 0.0
                print(f"  > {key.ljust(15)} | Win Rate: {wr:5.1f}% | ({stats['wins']}W / {stats['losses']}L)")
                
        print("-" * 65)
        print(f"🔄 Posiciones Abiertas y Esperando Resolución ({len(paper_trader.open_positions)}):")
        
        if not paper_trader.open_positions:
            print("   (Ninguna posición activa. Esperando oportunidades...)")
        else:
            for market_id, pos in paper_trader.open_positions.items():
                print(f"  [ {pos['asset'].upper()} {pos['window_minutes']}m ] -> {pos['direction']}")
                print(f"    Size: ${pos['amount_usdc']:.2f} | Precio Slippage: {pos['execution_price']:.3f} | Slug: {pos['slug']}")
                print("  " + "."*61)
        
        print("="*65)
        print("INFO: Auto-Entrenador ML y WebSockets de Binance corriendo en background...")
        if latest_logs:
            print("-" * 65)
            print("🔍 ÚLTIMAS MÉTRICAS DEL MOTOR HFT:")
            for log in list(latest_logs):
                print(f"  > {log}")
        print("="*65)
