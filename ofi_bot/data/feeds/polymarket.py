import asyncio
import time
from typing import Optional
from loguru import logger
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from config.settings import config
from utils.schemas import MarketOdds

class PolymarketFeed:
    """
    Feed para obtener las odds (probabilidades) actuales de un mercado en Polymarket.
    A diferencia de Binance (que usa WebSockets), Polymarket se consulta vía polling
    al Orderbook CLOB oficial, respetando rate limits.
    """

    def __init__(self, token_id_yes: str, token_id_no: str, market_id: str = ""):
        """
        Inicializa el feed para un mercado específico.
        
        Args:
            token_id_yes: ID del token para la posición "A favor" (YES)
            token_id_no: ID del token para la posición "En contra" (NO)
            market_id: Identificador opcional del mercado para logs
        """
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.market_id = market_id
        
        pk = config.private_key
        key = pk if pk and pk != "0x..." else "0x" + "0" * 64
        
        # Inicialización del cliente CLOB de Polymarket
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=key,
            chain_id=137, # Polygon Mainnet
            creds=ApiCreds(
                api_key=config.polymarket_api_key,
                api_secret=config.polymarket_api_secret,
                api_passphrase=config.polymarket_api_passphrase
            ) if config.polymarket_api_key else None
        )

    async def get_market_odds(self) -> Optional[MarketOdds]:
        """
        Consulta el CLOB de Polymarket para obtener el orderbook del token YES.
        Calcula el mid-price que representa la probabilidad P(UP) implícita del mercado.
        
        Returns:
            MarketOdds con los precios actuales o None si hay un error.
        """
        try:
            # Ejecutar la llamada sincrónica del cliente en un thread para no bloquear asyncio
            loop = asyncio.get_running_loop()
            book = await loop.run_in_executor(None, self.client.get_order_book, self.token_id_yes)
            
            best_bid = 0.0
            best_ask = 1.0
            
            if book.bids:
                best_bid = float(book.bids[0].price)
            if book.asks:
                best_ask = float(book.asks[0].price)
                
            # Calcular el mid-price (P(UP) actual del mercado)
            yes_price = (best_bid + best_ask) / 2.0
            
            return MarketOdds(
                market_id=self.market_id,
                token_id_yes=self.token_id_yes,
                token_id_no=self.token_id_no,
                yes_price=yes_price,
                bid_yes=best_bid,
                ask_yes=best_ask,
                closes_at_utc=0.0 # Este valor se obtiene usualmente de Gamma API, aquí lo omitimos temporalmente
            )
            
        except Exception as e:
            logger.error(f"Error obteniendo odds de Polymarket para {self.token_id_yes}: {e}")
            return None
