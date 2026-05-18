"""
Slug parsing utilities.
"""
from dataclasses import dataclass

# Target configurations for dynamic discovery
TARGET_ASSETS = ["btc", "eth", "xrp", "sol"]
TARGET_WINDOWS = ["5m", "15m"]

@dataclass
class ParsedSlug:
    asset: str
    timeframe: str
    timestamp: int
    is_valid: bool


def parse_market_slug(slug: str) -> ParsedSlug:
    """
    Extract asset, timeframe, and timestamp from a Polymarket slug.
    Expected format: {asset}-updown-{timeframe}-{timestamp}
    Example: btc-updown-5m-1778715600
    
    Strict: exactly 4 segments required. Extra hyphens reject the slug.
    """
    parts = slug.strip().split("-")
    if len(parts) == 4 and parts[1] == "updown":
        asset = parts[0].upper()
        timeframe = parts[2]
        
        if asset.lower() not in TARGET_ASSETS:
            return ParsedSlug(asset="", timeframe="", timestamp=0, is_valid=False)
            
        if timeframe not in TARGET_WINDOWS:
            return ParsedSlug(asset="", timeframe="", timestamp=0, is_valid=False)
        
        try:
            timestamp = int(parts[3])
            return ParsedSlug(
                asset=asset,
                timeframe=timeframe,
                timestamp=timestamp,
                is_valid=True
            )
        except ValueError:
            pass
            
    return ParsedSlug(
        asset="",
        timeframe="",
        timestamp=0,
        is_valid=False
    )
