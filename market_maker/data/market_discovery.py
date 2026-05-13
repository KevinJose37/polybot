"""
data/market_discovery.py — Discover active Polymarket prediction markets.
Uses the Gamma API to find BTC/ETH/SOL/XRP updown markets across time windows.
Follows the slug-based discovery pattern from ofi_bot.
"""

import json
import time
from typing import Optional

import aiohttp
from loguru import logger

from utils.schemas import MarketInfo


class MarketDiscovery:
    """
    Discovers active Polymarket binary prediction markets using the Gamma API.
    Markets follow a predictable slug pattern: {asset}-updown-{window}-{timestamp}
    """

    GAMMA_API_URL = "https://gamma-api.polymarket.com"

    # Map trading symbols to slug prefixes
    ASSET_SLUG_MAP = {
        "btcusdt": "btc",
        "ethusdt": "eth",
        "solusdt": "sol",
        "xrpusdt": "xrp",
    }

    # Map window minutes to slug suffix
    WINDOW_SLUG_MAP = {
        5: "5m",
        15: "15m",
        60: "1h",
    }

    # Map slug prefix to strike extraction patterns
    ASSET_STRIKE_MAP = {
        "btc": "BTCUSDT",
        "eth": "ETHUSDT",
        "sol": "SOLUSDT",
        "xrp": "XRPUSDT",
    }

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-create and reuse aiohttp session."""
        if self._session is None or self._session.closed:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            }
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    def _get_window_ts(self, window_minutes: int) -> int:
        """
        Compute the start timestamp of the current window,
        aligned to multiples of the window duration.
        """
        now_ts = int(time.time())
        divisor = window_minutes * 60
        return now_ts - (now_ts % divisor)

    def _generate_slug(self, symbol: str, window_minutes: int) -> str:
        """
        Generate the predictable slug for a market.
        Example: btc-updown-5m-1778418900
        """
        asset_prefix = self.ASSET_SLUG_MAP.get(symbol.lower(), "btc")
        window_suffix = self.WINDOW_SLUG_MAP.get(window_minutes, "5m")
        window_ts = self._get_window_ts(window_minutes)
        return f"{asset_prefix}-updown-{window_suffix}-{window_ts}"

    async def get_active_market(
        self, symbol: str, window_minutes: int
    ) -> Optional[MarketInfo]:
        """
        Look up the Gamma API for the current active market matching
        the asset and time window. Returns MarketInfo or None.
        """
        slug = self._generate_slug(symbol, window_minutes)
        url = f"{self.GAMMA_API_URL}/events?slug={slug}"

        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status != 200:
                    logger.debug(
                        f"[Discovery] HTTP {response.status} for slug {slug}"
                    )
                    return None

                data = await response.json()
                if not data or len(data) == 0:
                    logger.debug(f"[Discovery] No event found for slug: {slug}")
                    return None

                event = data[0]
                markets = event.get("markets", [])
                if not markets:
                    logger.debug(f"[Discovery] No markets in event: {slug}")
                    return None

                market = markets[0]

                # Parse clobTokenIds (may be JSON string or list)
                clob_token_ids_raw = market.get("clobTokenIds", "[]")
                if isinstance(clob_token_ids_raw, str):
                    try:
                        token_ids = json.loads(clob_token_ids_raw)
                    except (json.JSONDecodeError, TypeError):
                        token_ids = []
                else:
                    token_ids = clob_token_ids_raw

                if len(token_ids) < 2:
                    logger.debug(
                        f"[Discovery] Insufficient token IDs for {slug}: {token_ids}"
                    )
                    return None

                # Parse end date timestamp
                end_date_str = market.get("endDate", "")
                end_date_ts = 0.0
                if end_date_str:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(
                            end_date_str.replace("Z", "+00:00")
                        )
                        end_date_ts = dt.timestamp()
                    except (ValueError, TypeError):
                        pass

                market_info = MarketInfo(
                    market_id=market.get("id", ""),
                    token_id_yes=token_ids[0],
                    token_id_no=token_ids[1],
                    slug=slug,
                    asset=symbol.lower(),
                    window_minutes=window_minutes,
                    end_date_ts=end_date_ts,
                    question=market.get("question", ""),
                )

                logger.info(
                    f"[Discovery] Found market: {slug} | "
                    f"ID={market_info.market_id[:16]}... | "
                    f"Q={market_info.question[:60]}"
                )
                return market_info

        except aiohttp.ClientError as e:
            logger.error(f"[Discovery] Connection error for {slug}: {e}")
        except Exception as e:
            logger.error(f"[Discovery] Unexpected error for {slug}: {e}")

        return None

    async def discover_all_markets(
        self, assets: list[str], windows: list[int]
    ) -> dict[str, MarketInfo]:
        """
        Discover markets for all asset × window combinations.
        Returns a dict keyed by "{asset}_{window}" → MarketInfo.
        """
        discovered = {}
        for asset in assets:
            for window in windows:
                key = f"{asset}_{window}"
                market = await self.get_active_market(asset, window)
                if market:
                    discovered[key] = market
        logger.info(
            f"[Discovery] Discovered {len(discovered)}/{len(assets) * len(windows)} markets"
        )
        return discovered

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
