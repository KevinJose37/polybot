import asyncio
import time
from loguru import logger
from utils.schemas import BetDecision, MarketOdds
from data.database import DatabaseManager

class PaperTrader:
    """
    Motor de simulación realista de operaciones.
    Realiza un seguimiento del bankroll, aplica slippage, y resuelve 
    las operaciones consultando los precios locales en PostgreSQL.
    """
    def __init__(self, initial_balance: float = 500.0, slippage: float = 0.01):
        self.balance = initial_balance
        self.slippage = slippage
        self.open_positions = {}
        self.history = []
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.stats_by_type = {}

    def place_bet(self, decision: BetDecision, odds: MarketOdds, asset: str, window_minutes: int, slug: str) -> bool:
        if self.balance < decision.amount_usdc:
            logger.warning(f"PaperTrader: Saldo insuficiente para apostar {decision.amount_usdc:.2f}. Balance actual: {self.balance:.2f}")
            return False

        # Extraer timestamp del slug (ej: btc-updown-5m-1778564400)
        try:
            start_time_sec = int(slug.split('-')[-1])
        except:
            start_time_sec = int(time.time())

        expiration_time_sec = start_time_sec + (window_minutes * 60)

        # Simular Slippage real
        market_price = odds.yes_price if decision.direction == "YES" else (1.0 - odds.yes_price)
        execution_price = min(market_price + self.slippage, 0.99) # Nunca más de 99 centavos para asegurar retorno matemático válido

        shares = decision.amount_usdc / execution_price
        
        self.balance -= decision.amount_usdc

        self.open_positions[odds.market_id] = {
            "asset": asset,
            "direction": decision.direction,
            "amount_usdc": decision.amount_usdc,
            "shares": shares,
            "execution_price": execution_price,
            "start_time_sec": start_time_sec,
            "expiration_time_sec": expiration_time_sec,
            "window_minutes": window_minutes,
            "slug": slug
        }
        
        return True

    async def _get_price_at(self, db: DatabaseManager, asset: str, target_ts_sec: int) -> float:
        """Busca el precio en la BD en el momento más cercano por encima del target."""
        target_ms = target_ts_sec * 1000
        if not db.pool:
            return 0.0
            
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT mid_price FROM orderbook_snapshots WHERE symbol = $1 AND timestamp_ms >= $2 ORDER BY timestamp_ms ASC LIMIT 1",
                    asset.upper(), target_ms
                )
                if row:
                    return row['mid_price']
        except Exception as e:
            logger.error(f"Error fetching price for PaperTrader: {e}")
        return 0.0

    async def resolve_loop(self, db: DatabaseManager):
        """Bucle en segundo plano que revisa si expiró el tiempo de las posiciones."""
        while True:
            try:
                now_sec = int(time.time())
                resolved_ids = []

                for market_id, pos in list(self.open_positions.items()):
                    # Dar 15 segundos adicionales de margen para que la BD reciba datos asíncronos de Binance
                    if now_sec >= pos["expiration_time_sec"] + 15:
                        
                        start_price = await self._get_price_at(db, pos["asset"], pos["start_time_sec"])
                        end_price = await self._get_price_at(db, pos["asset"], pos["expiration_time_sec"])
                        
                        if start_price == 0.0 or end_price == 0.0:
                            # Faltan datos locales, postergamos resolución
                            continue
                            
                        # Determinar ganador: Polymarket resuelve UP si end_price >= start_price
                        up_won = end_price >= start_price
                        
                        bet_won = False
                        if pos["direction"] == "YES" and up_won:
                            bet_won = True
                        elif pos["direction"] == "NO" and not up_won:
                            bet_won = True
                            
                        key = f"{pos['asset'].upper()} {pos['window_minutes']}m"
                        if key not in self.stats_by_type:
                            self.stats_by_type[key] = {"wins": 0, "losses": 0}
                            
                        if bet_won:
                            payout = pos["shares"] * 1.00 # Cada share exitoso paga $1 USDC bruto
                            # Restar comisiones (típicamente Polymarket tiene 0% fees, pero los LPs cobran spread. Dejémoslo en 0 para simular la plataforma pura)
                            profit = payout - pos["amount_usdc"]
                            self.balance += payout
                            self.wins += 1
                            self.stats_by_type[key]["wins"] += 1
                        else:
                            profit = -pos["amount_usdc"]
                            self.losses += 1
                            self.stats_by_type[key]["losses"] += 1
                            
                        self.total_pnl += profit
                        
                        self.history.append({
                            "market_id": market_id,
                            "slug": pos["slug"],
                            "direction": pos["direction"],
                            "profit": profit,
                            "won": bet_won
                        })
                        resolved_ids.append(market_id)
                        
                for rid in resolved_ids:
                    del self.open_positions[rid]
                    
            except Exception as e:
                logger.error(f"Error en resolve_loop del PaperTrader: {e}")
                
            await asyncio.sleep(10)
