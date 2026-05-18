"""Polymarket REST API Adapter."""
import time
import aiohttp
import structlog
from dataclasses import dataclass, field
from typing import Protocol, Any

logger = structlog.get_logger(__name__)

@dataclass
class Token:
    token_id: str
    outcome: str
    price: float = 0.0

@dataclass
class MarketSnapshot:
    id: str
    slug: str
    active: bool
    closed: bool
    tokens: list[Token] = field(default_factory=list)


class PolymarketRESTClient:
    """Implementation of the PolymarketAdapter using REST endpoints."""
    def __init__(self, gamma_api_url: str = "https://gamma-api.polymarket.com"):
        self.gamma_api_url = gamma_api_url
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_markets(self, slug_prefix: str) -> list[MarketSnapshot]:
        """Fetch active markets by slug prefix from Gamma API."""
        session = await self._get_session()
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
                            import json
                            try:
                                outcomes = json.loads(outcomes)
                            except json.JSONDecodeError:
                                outcomes = ["Yes", "No"]
                                
                        for i, token_id in enumerate(clob_token_ids):
                            if i < len(outcomes):
                                tokens.append(Token(token_id=token_id, outcome=outcomes[i]))
                        
                        snapshot = MarketSnapshot(
                            id=m.get("conditionId", m.get("id", "")),
                            slug=m.get("slug", ""),
                            active=m.get("active", False),
                            closed=m.get("closed", False),
                            tokens=tokens
                        )
                        markets.append(snapshot)
                return markets
        except Exception as e:
            logger.error("get_markets_error", slug=slug_prefix, error=str(e))
            return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
