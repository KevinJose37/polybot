"""
data/feeds/polymarket_ws.py — Polymarket order book and trade print feeds.
Fetches Polymarket CLOB book data and trade activity via REST API.
"""

import asyncio
import time
from typing import Optional

import aiohttp
from loguru import logger

from utils.schemas import MarketOdds


class PolymarketFeed:
    """
    Fetches Polymarket order book data for a specific market.
    Uses REST polling (Polymarket CLOB does not provide a public WS for all data).
    """

    CLOB_URL = "https://clob.polymarket.com"

    def __init__(self, token_id_yes: str, token_id_no: str, market_id: str):
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.market_id = market_id
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_odds: Optional[MarketOdds] = None
        self._cache_ttl = 1.5  # seconds
        self._last_fetch_ts = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_market_odds(self) -> Optional[MarketOdds]:
        """
        Fetch current market odds from Polymarket CLOB.
        Returns cached result if still fresh.
        """
        now = time.time()
        if self._last_odds and (now - self._last_fetch_ts) < self._cache_ttl:
            return self._last_odds

        try:
            session = await self._get_session()
            url = f"{self.CLOB_URL}/book"

            async with session.get(url, params={"token_id": self.token_id_yes}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return self._last_odds
                data = await resp.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 1.0

            yes_price = (best_bid + best_ask) / 2 if best_bid > 0 else best_ask
            book_depth_bid = len(bids)
            book_depth_ask = len(asks)

            odds = MarketOdds(
                market_id=self.market_id,
                token_id_yes=self.token_id_yes,
                token_id_no=self.token_id_no,
                yes_price=yes_price,
                bid_yes=best_bid,
                ask_yes=best_ask,
                timestamp_ms=int(now * 1000),
                book_depth_bid=book_depth_bid,
                book_depth_ask=book_depth_ask,
            )

            self._last_odds = odds
            self._last_fetch_ts = now
            return odds

        except Exception as e:
            logger.debug(f"[Poly] Error fetching odds for {self.market_id}: {e}")
            return self._last_odds

    def get_book_spread(self) -> Optional[float]:
        """Get the current book spread (ask - bid) for YES token."""
        if self._last_odds:
            return self._last_odds.ask_yes - self._last_odds.bid_yes
        return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
