"""
Arbitrage opportunity models.
"""
from dataclasses import dataclass
from enum import Enum


class ArbType(Enum):
    PARITY = "TYPE-A"
    MONOTONICITY = "TYPE-B"
    EXHAUSTIVE = "TYPE-C"


@dataclass
class ArbLeg:
    """A single leg of an arbitrage execution."""
    market_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float


@dataclass
class ArbOpportunity:
    """A complete arbitrage opportunity."""
    opportunity_id: str
    type: ArbType
    edge: float
    size: float
    legs: list[ArbLeg]
    timestamp_ms: int
