"""
Market relationships mapping.
"""
from dataclasses import dataclass, field
from bot.api.schemas import MarketSnapshot
from bot.market_discovery.parsers import parse_market_slug


@dataclass
class MarketTopology:
    """
    Stores paired markets for arbitrage scanning.
    """
    # map market_id -> MarketSnapshot
    markets: dict[str, MarketSnapshot]
    
    # Parity targets (Type A / Type C): list of market_id
    # Since each 'updown' market is a self-contained YES/NO parity pair, 
    # all valid updown markets are parity targets.
    parity_markets: list[str]
    
    # Monotonicity targets (Type B): 
    # list of (market_5m_id, market_15m_id) pairs that share the exact same asset and timestamp
    monotonicity_pairs: list[tuple[str, str]]


def build_topology(markets: list[MarketSnapshot]) -> MarketTopology:
    """
    Pairs up discovered markets into relationship structures.
    
    Monotonicity pairing matches markets with the exact same asset AND timestamp.
    Filters out any markets that are closed or not active.
    """
    market_map = {}
    parity_markets = []
    
    # Group markets by asset -> timestamp -> timeframe -> list of market_ids
    asset_groups: dict[str, dict[int, dict[str, list[str]]]] = {}
    
    for market in markets:
        if not market.active or market.closed:
            continue
            
        market_map[market.id] = market
        parity_markets.append(market.id)
        
        parsed = parse_market_slug(market.slug)
        if not parsed.is_valid:
            continue
            
        asset = parsed.asset
        ts = parsed.timestamp
        tf = parsed.timeframe
        
        if asset not in asset_groups:
            asset_groups[asset] = {}
        if ts not in asset_groups[asset]:
            asset_groups[asset][ts] = {"5m": [], "15m": []}
            
        if tf in asset_groups[asset][ts]:
            asset_groups[asset][ts][tf].append(market.id)
        
    # Only keep 5m and 15m markets that share the exact same asset and timestamp
    complete_pairs: list[tuple[str, str]] = []
    for asset, ts_groups in asset_groups.items():
        for ts, groups in ts_groups.items():
            for m5 in groups["5m"]:
                for m15 in groups["15m"]:
                    complete_pairs.append((m5, m15))
            
    return MarketTopology(
        markets=market_map,
        parity_markets=parity_markets,
        monotonicity_pairs=complete_pairs
    )
