import asyncio
import json
import time
from typing import Optional
import websockets
from loguru import logger

from utils.schemas import OrderBookSnapshot, OrderBookLevel

class BinanceOrderBookFeed:
    """
    Conecta al WebSocket de Binance y mantiene el order book actualizado.
    Emite snapshots al OrderBookAggregator (o queue de destino) vía asyncio.Queue.
    """

    def __init__(self, symbol: str, queue: asyncio.Queue, levels: int = 20):
        self.symbol = symbol.lower()
        self.queue = queue
        self.levels = levels
        # Endpoint de stream: "@depth20@100ms"
        self.ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol}@depth{self.levels}@100ms"
        self._running = False

    async def connect(self) -> None:
        """
        Maneja la conexión WebSocket con Binance.
        Implementa un mecanismo de 'Exponential Backoff' para reconexión automática
        en caso de desconexión o errores de red, garantizando que el bot no se caiga.
        """
        self._running = True
        retry_delays = [1, 2, 4, 8, 16] # Segundos de espera antes de reintentar
        retry_idx = 0

        while self._running:
            try:
                logger.info(f"Conectando a Order Book feed de Binance: {self.symbol}")
                async with websockets.connect(self.ws_url) as ws:
                    logger.info(f"Conectado a {self.ws_url}")
                    retry_idx = 0  # Reset backoff on successful connect
                    
                    async for message in ws:
                        if not self._running:
                            break
                        
                        data = json.loads(message)
                        snapshot = self._parse_message(data)
                        if snapshot:
                            # Put snapshot in queue without blocking
                            try:
                                self.queue.put_nowait(snapshot)
                            except asyncio.QueueFull:
                                logger.warning("Queue full, dropping snapshot")

            except asyncio.CancelledError:
                self._running = False
                logger.info("Order Book feed cancelado")
                break
            except Exception as e:
                logger.error(f"Error en Order Book feed ({self.symbol}): {e}")
                delay = retry_delays[retry_idx]
                logger.info(f"Reconectando Order Book en {delay}s...")
                await asyncio.sleep(delay)
                retry_idx = min(retry_idx + 1, len(retry_delays) - 1)

    def stop(self):
        self._running = False

    def _parse_message(self, raw: dict) -> Optional[OrderBookSnapshot]:
        """
        Convierte el mensaje JSON crudo de Binance en un dataclass `OrderBookSnapshot`.
        Usa el reloj local (`time.time_ns()`) para obtener la máxima precisión de latencia,
        evitando depender del timestamp del exchange que puede tener desfase.
        """
        # Formato esperado: {"lastUpdateId": 160, "bids": [["0.0024", "10"], ...], "asks": [...]}
        bids_raw = raw.get("bids")
        asks_raw = raw.get("asks")
        
        if not bids_raw or not asks_raw:
            return None

        # Usar timestamp local para latencia real
        timestamp_ms = time.time_ns() // 1_000_000

        bids = [OrderBookLevel(price=float(p), size=float(s)) for p, s in bids_raw]
        asks = [OrderBookLevel(price=float(p), size=float(s)) for p, s in asks_raw]

        return OrderBookSnapshot(
            symbol=self.symbol.upper(),
            timestamp_ms=timestamp_ms,
            bids=bids,
            asks=asks
        )
