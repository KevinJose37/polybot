"""
utils/schemas.py — All data structures for the Market Maker bot.
Uses dataclasses for zero-overhead in-memory state.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Literal


# ═══════════════════════════════════════════════════════════════
# Market State Machine (per-market quoting state)
# ═══════════════════════════════════════════════════════════════

class MarketState(enum.Enum):
    """
    State machine for each market's quoting lifecycle.
    Transitions documented in strategy doc Section B.9.
    """
    INACTIVE = "INACTIVE"
    INITIALIZING = "INITIALIZING"
    QUOTING_BOTH = "QUOTING_BOTH_SIDES"
    DEFENSIVE = "DEFENSIVE"
    ONE_SIDED = "ONE_SIDED_ONLY"
    SUSPENDED = "SUSPENDED"
    EMERGENCY = "EMERGENCY"


class ToxicityLevel(enum.Enum):
    """Toxicity classification from order flow imbalance."""
    NORMAL = "NORMAL"           # < 0.40
    MILD = "MILD"               # 0.40 - 0.60
    DIRECTIONAL = "DIRECTIONAL" # 0.60 - 0.75
    HIGHLY_DIRECTIONAL = "HIGHLY_DIRECTIONAL"  # 0.75 - 0.90
    EXTREME = "EXTREME"         # > 0.90


class QuoteStatus(enum.Enum):
    """Lifecycle state of an individual quote."""
    PENDING_POST = "PENDING_POST"
    LIVE = "LIVE"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"


# ═══════════════════════════════════════════════════════════════
# Order Book & Trade Events
# ═══════════════════════════════════════════════════════════════

@dataclass
class OrderBookLevel:
    """Single price level in the order book."""
    price: float
    size: float


@dataclass
class OrderBookSnapshot:
    """Full order book snapshot at a point in time."""
    symbol: str
    timestamp_ms: int
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    mid_price: float = field(init=False)

    def __post_init__(self):
        if self.bids and self.asks:
            self.mid_price = (self.bids[0].price + self.asks[0].price) / 2
        else:
            self.mid_price = 0.0


@dataclass
class TradeEvent:
    """A single trade execution from an exchange."""
    symbol: str
    timestamp_ms: int
    price: float
    quantity: float
    is_buyer_maker: bool   # True = sell-initiated, False = buy-initiated


# ═══════════════════════════════════════════════════════════════
# Polymarket-specific structures
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketInfo:
    """Discovered Polymarket market with token IDs and metadata."""
    market_id: str
    token_id_yes: str
    token_id_no: str
    slug: str
    asset: str              # e.g., "btcusdt"
    window_minutes: int     # e.g., 5, 15, 60
    end_date_ts: float = 0.0  # Unix timestamp of market expiry
    question: str = ""
    strike_price: float = 0.0  # Asset price at window start (for BS model)


@dataclass
class MarketOdds:
    """Current odds/prices for a Polymarket market."""
    market_id: str
    token_id_yes: str
    token_id_no: str
    yes_price: float        # Implied probability for YES
    bid_yes: float          # Best bid for YES token
    ask_yes: float          # Best ask for YES token
    bid_no: float = 0.0     # Best bid for NO token
    ask_no: float = 0.0     # Best ask for NO token
    timestamp_ms: int = 0
    book_depth_bid: int = 0  # Number of bid levels
    book_depth_ask: int = 0  # Number of ask levels
    bids: list[dict] = field(default_factory=list)  # Full L2 bids [{"price": "0.5", "size": "100"}, ...]
    asks: list[dict] = field(default_factory=list)  # Full L2 asks


# ═══════════════════════════════════════════════════════════════
# Fair Value & Quoting
# ═══════════════════════════════════════════════════════════════

@dataclass
class FairValueEstimate:
    """Fair value computation result."""
    probability: float      # Binary option probability [0, 1]
    spot_price: float       # Underlying spot price used
    strike: float           # Strike price
    volatility: float       # Annualized volatility used
    tau_seconds: float      # Time to expiry in seconds
    timestamp_ms: int = 0
    is_stale: bool = False


@dataclass
class SpreadParams:
    """Parameters that determine the current spread."""
    half_spread_base: float         # Base half-spread in probability units
    tau_multiplier: float = 1.0     # Spread multiplier from time-to-expiry
    toxicity_multiplier: float = 1.0  # Spread multiplier from toxicity
    defensive_multiplier: float = 1.0  # Spread multiplier from defensive mode
    inventory_skew: float = 0.0     # Directional skew from inventory

    @property
    def effective_half_spread(self) -> float:
        return (self.half_spread_base
                * self.tau_multiplier
                * self.toxicity_multiplier
                * self.defensive_multiplier)


@dataclass
class QuotePair:
    """A bid/ask quote pair for a single market."""
    bid_price: float
    ask_price: float
    bid_size: float = 1.0   # Number of contracts
    ask_size: float = 1.0
    fair_value: float = 0.0
    spread_params: SpreadParams | None = None
    timestamp_ms: int = 0

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price

    @property
    def mid(self) -> float:
        return (self.bid_price + self.ask_price) / 2


@dataclass
class PendingQuote:
    """A quote sitting in the latency delay buffer."""
    quotes: QuotePair
    arrival_ms: int
    q_buy: float = 0.0
    q_sell: float = 0.0


# ═══════════════════════════════════════════════════════════════
# Inventory & Position Tracking
# ═══════════════════════════════════════════════════════════════

@dataclass
class InventoryState:
    """Current inventory position for a single market."""
    market_id: str
    asset: str
    window_minutes: int
    net_position: int = 0       # Positive = long, negative = short
    max_position: int = 100
    avg_entry_price: float = 0.0
    total_bought: int = 0
    total_sold: int = 0

    @property
    def utilization(self) -> float:
        """Position utilization as fraction of max [0, 1]."""
        if self.max_position == 0:
            return 0.0
        return abs(self.net_position) / self.max_position

    @property
    def is_long(self) -> bool:
        return self.net_position > 0

    @property
    def is_short(self) -> bool:
        return self.net_position < 0


# ═══════════════════════════════════════════════════════════════
# Toxicity Metrics
# ═══════════════════════════════════════════════════════════════

@dataclass
class ToxicityMetrics:
    """Order flow toxicity measurement."""
    order_imbalance: float = 0.0    # abs(buy_vol - sell_vol) / total_vol [0, 1]
    level: ToxicityLevel = ToxicityLevel.NORMAL
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    sample_count: int = 0
    spread_multiplier: float = 1.0  # How much to widen spread
    size_multiplier: float = 1.0    # How much to reduce quote size


# ═══════════════════════════════════════════════════════════════
# PnL Attribution
# ═══════════════════════════════════════════════════════════════

@dataclass
class PnLBreakdown:
    """Detailed PnL attribution."""
    spread_pnl: float = 0.0        # From round-trip bid-ask captures
    inventory_pnl: float = 0.0     # From mark-to-market of directional positions
    fee_pnl: float = 0.0           # Net fees paid (negative)
    total_pnl: float = 0.0         # Sum of all components
    realized_pnl: float = 0.0      # Closed positions only
    unrealized_pnl: float = 0.0    # Open positions MTM

    def update_total(self):
        self.total_pnl = self.spread_pnl + self.inventory_pnl + self.fee_pnl


# ═══════════════════════════════════════════════════════════════
# Fill Records
# ═══════════════════════════════════════════════════════════════

@dataclass
class FillRecord:
    """Record of a filled (or simulated) order."""
    market_id: str
    asset: str
    window_minutes: int
    side: Literal["BUY", "SELL"]
    price: float
    size: int
    fee: float = 0.0
    timestamp_ms: int = 0
    is_maker: bool = True
    is_simulated: bool = True       # Paper trading fill
    fill_id: str = ""

    def __post_init__(self):
        if not self.fill_id:
            self.fill_id = f"{self.market_id}_{self.side}_{self.timestamp_ms}"
        if not self.timestamp_ms:
            self.timestamp_ms = int(time.time() * 1000)


# ═══════════════════════════════════════════════════════════════
# Per-Market Runtime State (aggregated view)
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketRuntimeState:
    """Complete runtime state for a single market being quoted."""
    market_info: MarketInfo
    state: MarketState = MarketState.INACTIVE
    fair_value: FairValueEstimate | None = None
    current_quotes: QuotePair | None = None
    inventory: InventoryState | None = None
    toxicity: ToxicityMetrics | None = None
    odds: MarketOdds | None = None
    pnl: PnLBreakdown = field(default_factory=PnLBreakdown)
    last_quote_update_ms: int = 0
    last_fill_ms: int = 0
    fills: list[FillRecord] = field(default_factory=list)
