import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from loguru import logger
from utils.schemas import MarketOdds

class MarketDiscovery:
    """
    Servicio para descubrir mercados activos en Polymarket de forma dinámica.
    Utiliza el patrón de "Slug Predecible" y la API Gamma para extraer los
    Token IDs (YES/NO) correspondientes al mercado en vivo.
    """
    
    GAMMA_API_URL = "https://gamma-api.polymarket.com"

    def __init__(self):
        # Mapeo interno de symbolos a prefijos de slug
        self.asset_slug_map = {
            "btcusdt": "btc",
            "xrpusdt": "xrp",
            "solusdt": "sol",
            "ethusdt": "eth"
        }
        
        # Mapeo de temporalidad
        self.window_slug_map = {
            5: "5m",
            15: "15m",
            60: "1h"
        }

    def _get_window_ts(self, window_minutes: int) -> int:
        """
        Calcula el timestamp (epoch en segundos) correspondiente al inicio de la ventana actual,
        alineado a los múltiplos de la ventana (divisor).
        """
        import time
        now_ts = int(time.time())
        divisor = window_minutes * 60
        window_ts = now_ts - (now_ts % divisor)
        return window_ts

    def _generate_slug(self, symbol: str, window_minutes: int) -> str:
        """
        Genera el slug predecible con Unix timestamp. 
        Ej: btc-updown-5m-1778418900
        """
        asset_prefix = self.asset_slug_map.get(symbol.lower(), "btc")
        window_prefix = self.window_slug_map.get(window_minutes, "5m")
        window_ts = self._get_window_ts(window_minutes)
        
        slug = f"{asset_prefix}-updown-{window_prefix}-{window_ts}"
        return slug

    async def get_active_market(self, symbol: str, window_minutes: int) -> Optional[dict]:
        """
        Busca en Gamma API el mercado correspondiente al momento actual.
        Retorna un diccionario con market_id, token_id_yes, token_id_no.
        """
        slug = self._generate_slug(symbol, window_minutes)
        
        url = f"{self.GAMMA_API_URL}/events?slug={slug}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and len(data) > 0:
                            event = data[0]
                            markets = event.get("markets", [])
                            if markets:
                                market = markets[0]
                                clob_token_ids_str = market.get("clobTokenIds", "[]")
                                import json
                                try:
                                    token_ids = json.loads(clob_token_ids_str)
                                except:
                                    token_ids = []
                                    
                                if len(token_ids) >= 2:
                                    # En Polymarket, token index 0 usualmente es UP/YES, 1 es DOWN/NO
                                    return {
                                        "market_id": market.get("id", ""),
                                        "token_id_yes": token_ids[0],
                                        "token_id_no": token_ids[1],
                                        "slug": slug
                                    }
                        logger.warning(f"[Discovery] Evento no encontrado para slug: {slug}")
                    else:
                        logger.warning(f"[Discovery] Fallo en la API Gamma: HTTP {response.status} para slug {slug}")
        except Exception as e:
            logger.error(f"[Discovery] Error de conexión: {e}")
            
        return None
