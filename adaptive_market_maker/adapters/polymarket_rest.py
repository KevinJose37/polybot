"""Polymarket REST API Adapter."""
import json
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


# [C-1] Return types for get_market() and get_clob_market_info()
@dataclass
class MarketInfo:
    """Market metadata: question text and expiry."""
    question: str
    end_date_iso: str

@dataclass
class ClobTokenInfo:
    """Token detail — uses .t accessor for interface compatibility with bot.py."""
    t: str  # token_id

@dataclass
class ClobMarketInfo:
    """CLOB market metadata: tick size, min order size, and token list."""
    mts: str  # minimum tick size (string, parsed to float by caller)
    mos: str  # minimum order size (string, parsed to float by caller)
    t: list[ClobTokenInfo]  # tokens [yes, no]


class PolymarketRESTClient:
    """Implementation of the PolymarketAdapter using REST endpoints."""
    def __init__(self, gamma_api_url: str = "https://gamma-api.polymarket.com"):
        self.gamma_api_url = gamma_api_url
        self._session: aiohttp.ClientSession | None = None
        self._market_cache: dict[str, dict] = {}  # [C-1] avoid double-fetch

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    # ── [C-1] Market metadata fetching ──────────────────────────────

    async def _fetch_market_data(self, identifier: str) -> dict:
        """Fetch raw market JSON from Gamma API by token_id or condition_id.

        Strategy: try clob_token_ids lookup first (runtime path uses token IDs),
        then fall back to condition_id lookup.  Raises ValueError on miss.
        """
        if identifier in self._market_cache:
            return self._market_cache[identifier]

        session = await self._get_session()

        # Strategy 1: lookup by clob_token_ids (most common in paper trading)
        url = f"{self.gamma_api_url}/markets?clob_token_ids={identifier}"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        self._market_cache[identifier] = data[0]
                        return data[0]
        except Exception as e:
            logger.warning("fetch_market_by_token_failed", id=identifier, error=str(e))

        # Strategy 2: lookup by condition_id
        url = f"{self.gamma_api_url}/markets?id={identifier}"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        self._market_cache[identifier] = data[0]
                        return data[0]
        except Exception as e:
            logger.warning("fetch_market_by_id_failed", id=identifier, error=str(e))

        raise ValueError(f"Market not found for identifier={identifier}")

    async def get_market(self, condition_id: str) -> MarketInfo:
        """[C-1] Fetch market question and end date by condition_id or token_id."""
        market = await self._fetch_market_data(condition_id)
        question = market.get("question", "")
        end_date = market.get("endDate", "")
        if not question:
            raise ValueError(f"Market {condition_id}: missing 'question' field")
        if not end_date:
            raise ValueError(f"Market {condition_id}: missing 'endDate' field")
        return MarketInfo(question=question, end_date_iso=end_date)

    async def get_clob_market_info(self, condition_id: str) -> ClobMarketInfo:
        """[C-1] Fetch tick size, min order size, and tokens by condition_id or token_id."""
        market = await self._fetch_market_data(condition_id)

        mts = str(market.get("minimumTickSize", "0.01"))
        mos = str(market.get("minimumOrderSize", "5.0"))

        clob_token_ids = market.get("clobTokenIds", [])
        if isinstance(clob_token_ids, str):
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except json.JSONDecodeError:
                clob_token_ids = []

        if len(clob_token_ids) < 2:
            raise ValueError(
                f"Market {condition_id}: expected ≥2 tokens, got {len(clob_token_ids)}"
            )

        tokens = [ClobTokenInfo(t=tid) for tid in clob_token_ids]
        return ClobMarketInfo(mts=mts, mos=mos, t=tokens)

    async def get_market_resolution(self, token_id: str) -> float | None:
        """Query Gamma API for a market and return 1 if token won, 0 if lost, or None if unresolved."""
        try:
            market = await self._fetch_market_data(token_id)
            if market.get("umaResolutionStatus") != "resolved":
                return None
            
            clob_token_ids = market.get("clobTokenIds", [])
            if isinstance(clob_token_ids, str):
                try:
                    clob_token_ids = json.loads(clob_token_ids)
                except json.JSONDecodeError:
                    clob_token_ids = []
                    
            outcome_prices = market.get("outcomePrices", [])
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except json.JSONDecodeError:
                    outcome_prices = []
                    
            if not clob_token_ids or not outcome_prices or len(clob_token_ids) != len(outcome_prices):
                return None
                
            for i, tid in enumerate(clob_token_ids):
                if tid == token_id:
                    return float(outcome_prices[i])
            return None
        except Exception as e:
            logger.warning("get_market_resolution_failed", token_id=token_id, error=str(e))
            return None

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
                            try:
                                clob_token_ids = json.loads(clob_token_ids)
                            except json.JSONDecodeError:
                                clob_token_ids = []
                                
                        tokens = []
                        outcomes = m.get("outcomes", ["Yes", "No"])
                        if isinstance(outcomes, str):
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
