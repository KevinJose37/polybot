import asyncio
import json
import time
from typing import Optional
import websockets
from loguru import logger

from utils.schemas import TradeEvent

class BinanceTradeFeed:
    """
    Conecta al WebSocket de Binance de trades en tiempo real.
    """

    def __init__(self, symbol: str, queue: asyncio.Queue):
        self.symbol = symbol.lower()
        self.queue = queue
        self.ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol}@trade"
        self._running = False

    async def connect(self) -> None:
        """
        Maneja la conexión WebSocket de trades con Binance.
        Al igual que el OrderBook, utiliza 'Exponential Backoff' para reconectar.
        Envía los eventos a la cola asíncrona de procesamiento.
        """
        self._running = True
        retry_delays = [1, 2, 4, 8, 16]
        retry_idx = 0

        while self._running:
            try:
                logger.info(f"Conectando a Trade feed de Binance: {self.symbol}")
                async with websockets.connect(self.ws_url) as ws:
                    logger.info(f"Conectado a {self.ws_url}")
                    retry_idx = 0
                    
                    async for message in ws:
                        if not self._running:
                            break
                        
                        data = json.loads(message)
                        trade = self._parse_trade(data)
                        if trade:
                            try:
                                self.queue.put_nowait(trade)
                            except asyncio.QueueFull:
                                logger.warning("Queue full, dropping trade")

            except asyncio.CancelledError:
                self._running = False
                logger.info("Trade feed cancelado")
                break
            except Exception as e:
                logger.error(f"Error en Trade feed ({self.symbol}): {e}")
                delay = retry_delays[retry_idx]
                logger.info(f"Reconectando Trade Feed en {delay}s...")
                await asyncio.sleep(delay)
                retry_idx = min(retry_idx + 1, len(retry_delays) - 1)

    def stop(self):
        self._running = False

    def _parse_trade(self, raw: dict) -> Optional[TradeEvent]:
        """
        Convierte el JSON de trade de Binance en el dataclass `TradeEvent`.
        Extrae el indicador clave 'm' (is_buyer_maker) para saber si la orden fue
        generada por presión compradora o vendedora.
        """
        # Formato: {"T": 1672515782136, "p": "0.001", "q": "100", "m": true, ...}
        price = raw.get("p")
        qty = raw.get("q")
        is_buyer_maker = raw.get("m")
        exchange_timestamp = raw.get("T")

        if price is None or qty is None or is_buyer_maker is None:
            return None

        # Usamos timestamp local para consistencia con el OFI
        timestamp_ms = time.time_ns() // 1_000_000

        return TradeEvent(
            symbol=self.symbol.upper(),
            timestamp_ms=timestamp_ms,
            price=float(price),
            quantity=float(qty),
            is_buyer_maker=bool(is_buyer_maker)
        )
