import asyncio
import time
from typing import Optional, List
from loguru import logger
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderType

from config.settings import config
from utils.schemas import BetDecision, BetReceipt, MarketOdds

class PolymarketClient:
    """
    Cliente maestro de ejecución para Polymarket.
    Maneja la autenticación, la firma de órdenes, el control de saldos
    y la obtención de mercados activos.
    """

    def __init__(self):
        # Si no hay llave o tiene el placeholder '0x...', generamos una dummy válida
        pk = config.private_key
        key = pk if pk and pk != "0x..." else "0x" + "0" * 64
        
        creds = None
        if config.polymarket_api_key:
            creds = ApiCreds(
                api_key=config.polymarket_api_key,
                api_secret=config.polymarket_api_secret,
                api_passphrase=config.polymarket_api_passphrase
            )

        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=key,
            chain_id=137, # Polygon PoS
            creds=creds,
            signature_type=0, # EOA
            funder=config.funder_address
        )
        self.paper_trading = config.paper_trading

    async def get_balance(self) -> float:
        """Retorna el balance en USDC disponible en la cuenta configurada."""
        if self.paper_trading and not config.polymarket_api_key:
            return 1000.0 # Balance simulado si no hay API Keys
            
        try:
            # Llama sincrónico a get_allowance o get_balance
            # Para py-clob-client v2, si no hay método de balance directo, se asume un check
            # Usualmente se usa balance de red, pero acá devolvemos el hardcodeado/simulado temporalmente
            # TODO: Implementar llamada real a contrato ERC20 si es necesario
            return 1000.0
        except Exception as e:
            logger.error(f"Error consultando saldo: {e}")
            return 0.0

    async def place_bet(self, decision: BetDecision, market: MarketOdds) -> Optional[BetReceipt]:
        """
        Ejecuta la apuesta en la blockchain.
        Si PAPER_TRADING=True, solo simula la transacción.
        """
        if not decision.should_bet or not decision.direction:
            return None

        # Identificar qué token comprar
        token_id = market.token_id_yes if decision.direction == "YES" else market.token_id_no

        logger.info(f"[{'PAPER' if self.paper_trading else 'LIVE'}] Preparando orden de compra de {decision.direction} por {decision.amount_usdc:.2f} USDC en mercado {market.market_id}")

        if self.paper_trading:
            # Simular latencia de red
            await asyncio.sleep(0.5)
            receipt = BetReceipt(
                tx_hash="0xsimulatedhash" + str(int(time.time())),
                amount_usdc=decision.amount_usdc,
                price_filled=decision.p_market,
                status="filled",
                timestamp_ms=time.time_ns() // 1_000_000
            )
            logger.success(f"[PAPER TRADE] Orden simulada exitosa: {receipt.tx_hash}")
            return receipt

        # EJECUCIÓN REAL
        try:
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=decision.amount_usdc
            )
            
            # Ejecución bloqueante, se corre en thread
            loop = asyncio.get_running_loop()
            
            # Crear y firmar
            signed_order = await loop.run_in_executor(None, self.client.create_market_order, order_args)
            
            # Enviar FOK
            logger.warning("Enviando orden LIVE Fill-Or-Kill a Polymarket...")
            response = await loop.run_in_executor(None, self.client.post_order, signed_order, OrderType.FOK)
            
            if response and response.get('success'):
                receipt = BetReceipt(
                    tx_hash=response.get('transactionHash', 'N/A'),
                    amount_usdc=decision.amount_usdc,
                    price_filled=decision.p_market, # Se asume cercano
                    status="filled",
                    timestamp_ms=time.time_ns() // 1_000_000
                )
                logger.success(f"[LIVE TRADE] Orden FOK ejecutada exitosamente: {receipt.tx_hash}")
                return receipt
            else:
                logger.error(f"[LIVE TRADE] Orden rechazada o fallida: {response}")
                return None

        except Exception as e:
            logger.error(f"Error crítico enviando orden a Polymarket: {e}")
            return None
