"""Market relationships mapping."""
from dataclasses import dataclass, field
from bot.api.schemas import MarketSnapshot
from bot.market_discovery.parsers import parse_market_slug
from bot.constants import TARGET_WINDOWS


@dataclass
class MarketTopology:
    """Stores paired markets for arbitrage scanning."""
    # map market_id -> MarketSnapshot
    markets: dict[str, MarketSnapshot]
    
    # Parity targets (Type A / Type C): list of market_id
    parity_markets: list[str]
    
    # Monotonicity targets (Type B): 
    # list of (market_5m_id, market_15m_id) pairs per asset
    # Cross-joins all active 5m × 15m markets for the same asset
    monotonicity_pairs: list[tuple[str, str]]


def build_topology(markets: list[MarketSnapshot]) -> MarketTopology:
    """
    Pairs up discovered markets into relationship structures.
    
    Monotonicity pairing: groups by ASSET, then cross-joins all active
    5m markets with all active 15m markets for that asset. This is correct
    because we're comparing probability distributions across timeframes
    for the same underlying (e.g., BTC up/down 5m vs BTC up/down 15m).
    
    Filters out any markets that are closed or not active.
    """
    market_map = {}
    parity_markets = []
    
    # Group markets by asset -> timeframe -> list of market_ids
    asset_groups: dict[str, dict[str, list[str]]] = {}
    
    for market in markets:
        if not market.active or market.closed:
            continue
            
        market_map[market.id] = market
        parity_markets.append(market.id)
        
        parsed = parse_market_slug(market.slug)
        if not parsed.is_valid:
            continue
            
        asset = parsed.asset
        tf = parsed.timeframe
        
        if asset not in asset_groups:
            asset_groups[asset] = {"5m": [], "15m": []}
            
        if tf in asset_groups[asset]:
            asset_groups[asset][tf].append(market.id)
        
    # Cross-join 5m × 15m per asset, but only where 5m is contained within 15m
    complete_pairs: list[tuple[str, str]] = []
    for asset, groups in asset_groups.items():
        for m5 in groups["5m"]:
            m5_parsed = parse_market_slug(market_map[m5].slug)
            if not m5_parsed.is_valid:
                continue
            for m15 in groups["15m"]:
                m15_parsed = parse_market_slug(market_map[m15].slug)
                if not m15_parsed.is_valid:
                    continue
                # Temporal containment: 5m window [ts, ts+300] must be within 15m window [ts, ts+900]
                if m5_parsed.timestamp >= m15_parsed.timestamp and (m5_parsed.timestamp + 300) <= (m15_parsed.timestamp + 900):
                    complete_pairs.append((m5, m15))
            
    return MarketTopology(
        markets=market_map,
        parity_markets=parity_markets,
        monotonicity_pairs=complete_pairs
    )
