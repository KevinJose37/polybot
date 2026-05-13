import asyncio
import asyncpg
from loguru import logger
from config.settings import config

class DatabaseManager:
    """
    Gestiona la conexión y operaciones asíncronas con PostgreSQL.
    Mantiene un pool de conexiones para alta concurrencia.
    """
    
    def __init__(self):
        self.pool = None
        self.dsn = config.database_url

    async def connect(self):
        """Inicializa el pool de conexiones a la base de datos."""
        logger.info(f"Conectando a PostgreSQL: {self.dsn.split('@')[-1]}")
        try:
            self.pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=2,
                max_size=20,
                command_timeout=10
            )
            await self._init_schema()
            logger.info("Conectado a PostgreSQL y schema verificado.")
        except Exception as e:
            logger.error(f"Error conectando a PostgreSQL: {e}")
            raise

    async def close(self):
        """Cierra el pool de conexiones."""
        if self.pool:
            await self.pool.close()
            logger.info("Conexión a PostgreSQL cerrada.")

    async def _init_schema(self):
        """Crea las tablas necesarias si no existen."""
        
        # Tabla de apuestas (Para Fase 4)
        CREATE_BETS_TABLE = """
        CREATE TABLE IF NOT EXISTS bets (
            id SERIAL PRIMARY KEY,
            timestamp DOUBLE PRECISION NOT NULL,
            market_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            window_minutes INTEGER NOT NULL,
            direction TEXT NOT NULL,
            amount_usdc DOUBLE PRECISION NOT NULL,
            price_entered DOUBLE PRECISION NOT NULL,
            p_model DOUBLE PRECISION NOT NULL,
            p_market DOUBLE PRECISION NOT NULL,
            edge DOUBLE PRECISION NOT NULL,
            f_kelly DOUBLE PRECISION NOT NULL,
            ofi_zscore DOUBLE PRECISION,
            vwap_dev_bps DOUBLE PRECISION,
            cvd_norm DOUBLE PRECISION,
            atr_pct DOUBLE PRECISION,
            tx_hash TEXT,
            resolved INTEGER DEFAULT 0,
            pnl_usdc DOUBLE PRECISION,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """

        # Tabla para recolectar snapshots de Orderbook (Para backtesting - Fase 1)
        CREATE_SNAPSHOTS_TABLE = """
        CREATE TABLE IF NOT EXISTS orderbook_snapshots (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            timestamp_ms BIGINT NOT NULL,
            bids_json TEXT NOT NULL,
            asks_json TEXT NOT NULL,
            mid_price DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time ON orderbook_snapshots(symbol, timestamp_ms);
        """

        # Tabla para recolectar trades crudos (Para backtesting - Fase 1)
        CREATE_TRADES_TABLE = """
        CREATE TABLE IF NOT EXISTS trade_events (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            timestamp_ms BIGINT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            is_buyer_maker BOOLEAN NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trade_events(symbol, timestamp_ms);
        """

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(CREATE_BETS_TABLE)
                await conn.execute(CREATE_SNAPSHOTS_TABLE)
                await conn.execute(CREATE_TRADES_TABLE)

    async def record_bet(self, decision, receipt, features, market_id: str, asset: str, window_minutes: int):
        """
        Guarda el registro de la apuesta en la base de datos.
        Incluye las predicciones y features al momento de la orden.
        """
        if not self.pool:
            return

        query = """
        INSERT INTO bets (
            timestamp, market_id, asset, window_minutes, direction, amount_usdc,
            price_entered, p_model, p_market, edge, f_kelly, ofi_zscore,
            vwap_dev_bps, cvd_norm, atr_pct, tx_hash
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
        )
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    query,
                    receipt.timestamp_ms / 1000.0,
                    market_id,
                    asset,
                    window_minutes,
                    decision.direction,
                    receipt.amount_usdc,
                    receipt.price_filled,
                    decision.p_model,
                    decision.p_market,
                    decision.edge,
                    decision.f_kelly,
                    features.ofi_zscore,
                    features.vwap_dev_bps,
                    features.cvd_norm,
                    features.atr_pct,
                    receipt.tx_hash
                )
        except Exception as e:
            logger.error(f"Error registrando apuesta en DB: {e}")

    async def cleanup_old_data(self, days: int = 7):
        """Elimina datos más antiguos que 'days' días de las tablas pesadas."""
        if not self.pool:
            return
            
        # days en ms
        cutoff_ms = int(asyncio.get_event_loop().time() * 1000) - (days * 24 * 60 * 60 * 1000)
        
        try:
            async with self.pool.acquire() as conn:
                res1 = await conn.execute("DELETE FROM orderbook_snapshots WHERE timestamp_ms < $1", cutoff_ms)
                res2 = await conn.execute("DELETE FROM trade_events WHERE timestamp_ms < $1", cutoff_ms)
                logger.info(f"[DB Cleanup] Limpieza ejecutada. Snapshots: {res1}, Trades: {res2}")
        except Exception as e:
            logger.error(f"Error en limpieza de BD: {e}")

# Instancia global de BD
db = DatabaseManager()
