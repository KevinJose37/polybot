"""
Polymarket REST API Adapter.
"""
from typing import Protocol
import aiohttp
import structlog

from bot.api.schemas import MarketSnapshot, OrderBookSnapshot, OrderRequest, OrderAck, Token

logger = structlog.get_logger(__name__)


class PolymarketAdapter(Protocol):
    """Protocol for interacting with Polymarket."""
    async def get_markets(self, slug_prefix: str) -> list[MarketSnapshot]: ...
    async def get_orderbook(self, market_id: str) -> OrderBookSnapshot: ...
    async def place_order(self, order: OrderRequest) -> OrderAck: ...
    async def cancel_order(self, order_id: str) -> bool: ...


class PolymarketRESTClient(PolymarketAdapter):
    """
    Implementation of the PolymarketAdapter using REST endpoints.
    """
    def __init__(self, gamma_api_url: str = "https://gamma-api.polymarket.com", clob_api_url: str = "https://clob.polymarket.com"):
        self.gamma_api_url = gamma_api_url
        self.clob_api_url = clob_api_url
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_markets(self, slug_prefix: str) -> list[MarketSnapshot]:
        """
        # ASSUMPTION: endpoint shape based on public docs as of 2024-Q4
        # Verify at: https://docs.polymarket.com
        """
        session = await self._get_session()
        # We query the events endpoint filtering by slug
        url = f"{self.gamma_api_url}/events?slug={slug_prefix}"
        
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning("get_markets_failed", status=response.status, slug=slug_prefix)
                    return []
                data = await response.json()
                
                markets = []
                for event in data:
                    for m in event.get("markets", []):
                        # Extract token ids
                        clob_token_ids = m.get("clobTokenIds", [])
                        if isinstance(clob_token_ids, str):
                            import json
                            try:
                                clob_token_ids = json.loads(clob_token_ids)
                            except json.JSONDecodeError:
                                clob_token_ids = []
                                
                        tokens = []
                        outcomes = m.get("outcomes", ["Yes", "No"])
                        if isinstance(outcomes, str):
                            try:
                                outcomes = json.loads(outcomes)
                            except:
                                outcomes = ["Yes", "No"]
                                
                        for idx, tid in enumerate(clob_token_ids):
                            outcome_str = outcomes[idx] if idx < len(outcomes) else str(idx)
                            tokens.append(Token(token_id=tid, outcome=outcome_str))
                            
                        markets.append(MarketSnapshot(
                            condition_id=m.get("conditionId", m.get("id", "")),
                            question=m.get("question", ""),
                            slug=event.get("slug", slug_prefix),
                            tokens=tokens,
                            active=m.get("active", True),
                            closed=m.get("closed", False)
                        ))
                return markets
        except Exception as e:
            logger.error("get_markets_error", error=str(e), slug=slug_prefix)
            return []

    async def get_orderbook(self, market_id: str) -> OrderBookSnapshot:
        """
        # ASSUMPTION: endpoint shape based on public docs as of 2024-Q4
        """
        session = await self._get_session()
        url = f"{self.clob_api_url}/book?token_id={market_id}"
        
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    return OrderBookSnapshot(market_id=market_id, bids=[], asks=[])
                data = await response.json()
                
                bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
                asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]
                
                return OrderBookSnapshot(
                    market_id=market_id,
                    bids=sorted(bids, key=lambda x: x[0], reverse=True),
                    asks=sorted(asks, key=lambda x: x[0])
                )
        except Exception as e:
            logger.error("get_orderbook_error", error=str(e), market_id=market_id)
            return OrderBookSnapshot(market_id=market_id, bids=[], asks=[])

    async def place_order(self, order_data: dict) -> dict:
        """Submit a signed order to the Polymarket CLOB API."""
        session = await self._get_session()
        url = f"{self.clob_api_url}/order"
        try:
            async with session.post(url, json=order_data) as response:
                if response.status != 200:
                    logger.warning("place_order_failed", status=response.status)
                    return {"status": "REJECTED", "message": f"HTTP {response.status}"}
                data = await response.json()
                return data
        except Exception as e:
            logger.error("place_order_error", error=str(e))
            return {"status": "REJECTED", "message": str(e)}

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order via the Polymarket CLOB API."""
        session = await self._get_session()
        url = f"{self.clob_api_url}/order/{order_id}"
        try:
            async with session.delete(url) as response:
                return response.status == 200
        except Exception as e:
            logger.error("cancel_order_error", error=str(e), order_id=order_id)
            return False

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
