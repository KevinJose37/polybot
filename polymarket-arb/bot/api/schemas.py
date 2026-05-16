"""
Pydantic schemas for the Polymarket API.
"""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Token(BaseModel):
    """A token inside a market (e.g. YES or NO)."""
    token_id: str
    outcome: str
    price: float = 0.5


class MarketSnapshot(BaseModel):
    """Snapshot of a prediction market from REST."""
    id: str = Field(alias="condition_id")
    question: str
    slug: str
    tokens: list[Token] = Field(default_factory=list)
    active: bool
    closed: bool

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class OrderBookSnapshot(BaseModel):
    """Snapshot of the L2 orderbook."""
    market_id: str
    bids: list[tuple[float, float]]  # price, size
    asks: list[tuple[float, float]]  # price, size


class OrderRequest(BaseModel):
    """Request to place an order."""
    market_id: str
    side: Literal["BUY", "SELL"]
    price: float
    size: float


class OrderAck(BaseModel):
    """Acknowledgment of an order."""
    order_id: str
    status: str
    message: str = ""
    filled_size: float = 0.0
    fill_price: float = 0.0
