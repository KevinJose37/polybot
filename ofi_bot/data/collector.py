import asyncio
import json
from loguru import logger
from utils.schemas import OrderBookSnapshot, TradeEvent
from data.database import db

class DataCollector:
    """
    Recibe los eventos de los websockets y los guarda en PostgreSQL en lotes (batches)
    para optimizar la escritura y no bloquear el event loop con inserts individuales.
    """
    
    def __init__(self, batch_size: int = 100, flush_interval: float = 1.0):
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        
        self._book_batch = []
        self._trade_batch = []
        
        self._running = False
        self._flush_task = None

    async def start(self):
        """Inicia el loop de volcado de datos a PostgreSQL."""
        self._running = True
        # Aseguramos de que la base de datos esté conectada
        if not db.pool:
            await db.connect()
            
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(f"DataCollector iniciado (batch={self.batch_size}, interval={self.flush_interval}s)")

    async def stop(self):
        """Detiene el recolector y guarda lo que quede en memoria."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # Flush final antes de salir
        await self._flush_books()
        await self._flush_trades()
        logger.info("DataCollector detenido.")

    async def collect_order_book(self, snapshot: OrderBookSnapshot):
        """Agrega un snapshot a la memoria para guardarlo luego."""
        # Convertimos bids y asks a JSON para guardarlos
        bids_json = json.dumps([[b.price, b.size] for b in snapshot.bids])
        asks_json = json.dumps([[a.price, a.size] for a in snapshot.asks])
        
        record = (
            snapshot.symbol,
            snapshot.timestamp_ms,
            bids_json,
            asks_json,
            snapshot.mid_price
        )
        self._book_batch.append(record)
        
        if len(self._book_batch) >= self.batch_size:
            await self._flush_books()

    async def collect_trade(self, trade: TradeEvent):
        """Agrega un trade a la memoria para guardarlo luego."""
        record = (
            trade.symbol,
            trade.timestamp_ms,
            trade.price,
            trade.quantity,
            trade.is_buyer_maker
        )
        self._trade_batch.append(record)
        
        if len(self._trade_batch) >= self.batch_size:
            await self._flush_trades()

    async def _flush_loop(self):
        """Loop en background que vacía la memoria a intervalos regulares."""
        while self._running:
            await asyncio.sleep(self.flush_interval)
            await self._flush_books()
            await self._flush_trades()

    async def _flush_books(self):
        """Guarda en bloque todos los snapshots pendientes en la base de datos."""
        if not self._book_batch or not db.pool:
            return
            
        batch_to_save = self._book_batch[:]
        self._book_batch.clear()
        
        query = """
        INSERT INTO orderbook_snapshots (symbol, timestamp_ms, bids_json, asks_json, mid_price)
        VALUES ($1, $2, $3, $4, $5)
        """
        try:
            async with db.pool.acquire() as conn:
                await conn.executemany(query, batch_to_save)
            logger.debug(f"Guardados {len(batch_to_save)} snapshots en PostgreSQL.")
        except Exception as e:
            logger.exception(f"Error al guardar snapshots en BD: {e}")

    async def _flush_trades(self):
        """Guarda en bloque todos los trades pendientes en la base de datos."""
        if not self._trade_batch or not db.pool:
            return
            
        batch_to_save = self._trade_batch[:]
        self._trade_batch.clear()
        
        query = """
        INSERT INTO trade_events (symbol, timestamp_ms, price, quantity, is_buyer_maker)
        VALUES ($1, $2, $3, $4, $5)
        """
        try:
            async with db.pool.acquire() as conn:
                await conn.executemany(query, batch_to_save)
            logger.debug(f"Guardados {len(batch_to_save)} trades en PostgreSQL.")
        except Exception as e:
            logger.exception(f"Error al guardar trades en BD: {e}")
