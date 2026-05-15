"""
Global constants for the Polymarket arbitrage bot.
"""

from typing import Final

# Market identifying constants
TARGET_ASSETS: Final[tuple[str, ...]] = ("btc", "eth", "sol", "xrp")
TARGET_DIRECTIONS: Final[tuple[str, ...]] = ("up", "down")
TARGET_WINDOWS: Final[tuple[str, ...]] = ("5m", "15m")
