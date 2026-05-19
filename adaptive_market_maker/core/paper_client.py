"""High-Fidelity Paper Trading Client for Polymarket.

Audit fixes applied:
  [C-1] P&L tracking with cost basis, realized/unrealized computation
  [M-1] Lognormal latency distribution (replaces Gaussian)
  [H-3] REST delegation — get_market/get_clob_market_info use real REST API
  [H-4] Drawdown kill-switch with equity tracking
"""

import asyncio
import json
import math
import random
import time
import atexit
from dataclasses import dataclass
from typing import Callable
from pathlib import Path

import structlog

from adapters.base import TradeEvent, OrderBook
from config.settings import LatencyConfig
from core.interfaces import PolymarketClientProtocol

logger = structlog.get_logger(__name__)


@dataclass
class PaperLiveOrder:
    id: str
    market_id: str
    side: str
    price: float
    size: float
    remaining_size: float
    queue_ahead: float
    created_at: float


class ForensicLogger:
    def __init__(self, log_dir: str = "logs"):
        Path(log_dir).mkdir(exist_ok=True)
        # [C-3] Append mode to preserve history across restarts
        self.file = open(Path(log_dir) / "paper_forensics.jsonl", "a")
        atexit.register(self.close)

    def log_event(self, event_type: str, data: dict):
        record = {
            "timestamp": time.time(),
            "event": event_type,
            **data
        }
        self.file.write(json.dumps(record) + "\n")
        self.file.flush()  # Immediate flush for crash safety

    def close(self):
        if not self.file.closed:
            self.file.flush()
            self.file.close()


# [M-1] Lognormal latency model — right-skewed, always positive, realistic tail behavior
def sample_latency(mean: float, std: float, p_fat: float, fat_mult: float) -> float:
    """Sample a realistic network latency in seconds.
    
    Uses lognormal distribution which naturally produces:
    - Always positive values (no clamping needed)
    - Right-skewed distribution matching real network behavior
    - Configurable fat tails for outlier events
    """
    if mean <= 0:
        return 0.0
    effective_mean = mean * fat_mult if random.random() < p_fat else mean
    # Convert mean/std in ms to lognormal parameters
    # For lognormal: E[X] = exp(mu + sigma^2/2), Var[X] = (exp(sigma^2)-1)*exp(2*mu+sigma^2)
    variance = std ** 2
    mu = math.log(effective_mean ** 2 / math.sqrt(variance + effective_mean ** 2))
    sigma = math.sqrt(math.log(1 + variance / effective_mean ** 2))
    return random.lognormvariate(mu, sigma) / 1000.0


class PaperPolymarketClient(PolymarketClientProtocol):
    """High-fidelity paper trading client with P&L tracking and drawdown monitoring."""

    def __init__(self, latency_config: LatencyConfig, rest_client=None,
                 initial_capital: float = 1000.0, max_drawdown_pct: float = 0.15,
                 spot_mid_fetcher: Callable[[str], float | None] | None = None):
        self.latency_config = latency_config
        self.rest_client = rest_client  # [H-3] Real REST client for market metadata
        self.synthetic_inventory: dict[str, float] = {}
        self.live_orders: dict[str, PaperLiveOrder] = {}
        self.latest_books: dict[str, OrderBook] = {}
        self.forensic = ForensicLogger()
        self._order_counter = 0

        # [C-1] P&L tracking state
        self.cost_basis: dict[str, float] = {}        # market_id -> total cost basis (USDC)
        self.realized_pnl: dict[str, float] = {}      # market_id -> cumulative realized P&L
        self.fill_count = 0
        self.win_count = 0
        self.loss_count = 0

        # [H-4] Drawdown kill-switch
        self.initial_capital = initial_capital
        self.max_drawdown_pct = max_drawdown_pct
        self._peak_equity: float = initial_capital
        self._drawdown_triggered: bool = False

        # [C-2] Callback to notify ExecutionManager when fills complete
        self._fill_callback = None
        
        # [M-2] Callback to fetch latest Binance spot for forensic logging
        self.spot_mid_fetcher = spot_mid_fetcher

    def set_fill_callback(self, callback):
        """[C-2] Register a callback invoked after each fill.

        Signature: callback(order_id: str, market_id: str, remaining_size: float)
        """
        self._fill_callback = callback

    def update_book(self, book: OrderBook):
        self.latest_books[book.market_id] = book

    async def get_clob_market_info(self, condition_id: str):
        """[H-3] Delegate to real REST API if available, otherwise return mock."""
        if self.rest_client and hasattr(self.rest_client, 'get_clob_market_info'):
            return await self.rest_client.get_clob_market_info(condition_id)
        # Fallback mock for testing
        class MockToken:
            def __init__(self, token_id):
                self.t = token_id
        class MockInfo:
            def __init__(self):
                self.mts = "0.001"
                self.mos = "10.0"
                self.t = [MockToken("yes_token"), MockToken("no_token")]
        return MockInfo()

    async def get_market(self, condition_id: str):
        """[H-3] Delegate to real REST API if available, otherwise return mock."""
        if self.rest_client and hasattr(self.rest_client, 'get_market'):
            return await self.rest_client.get_market(condition_id)
        # Fallback mock for testing
        class MockMarket:
            def __init__(self):
                self.question = f"Will ETH be above 3000? {condition_id}"
                self.end_date_iso = "2026-12-31T23:59:59Z"
        return MockMarket()

    async def get_market_resolution(self, token_id: str) -> float | None:
        """[H-4] Delegate to real REST API if available."""
        if self.rest_client and hasattr(self.rest_client, 'get_market_resolution'):
            return await self.rest_client.get_market_resolution(token_id)
        return None

    async def fetch_inventory(self, market_id: str) -> float:
        await asyncio.sleep(sample_latency(
            self.latency_config.market_data_mean_ms,
            self.latency_config.market_data_std_ms,
            self.latency_config.p_fat_tail,
            self.latency_config.fat_tail_mult
        ))
        return self.synthetic_inventory.get(market_id, 0.0)

    def get_inventory(self, market_id: str) -> float:
        return self.synthetic_inventory.get(market_id, 0.0)

    # [H-4] Drawdown kill-switch
    @property
    def is_drawdown_breached(self) -> bool:
        return self._drawdown_triggered

    def get_total_equity(self, current_prices: dict[str, float]) -> float:
        """Compute current equity = initial_capital + realized_pnl + unrealized_pnl."""
        total_realized = sum(self.realized_pnl.values())
        total_unrealized = self.get_total_unrealized_pnl(current_prices)
        return self.initial_capital + total_realized + total_unrealized

    def get_total_unrealized_pnl(self, current_prices: dict[str, float]) -> float:
        """[C-1] Calculate unrealized P&L across all positions."""
        total = 0.0
        for market_id, shares in self.synthetic_inventory.items():
            if abs(shares) < 1e-9:
                continue
            if market_id not in current_prices:
                continue
            current_price = current_prices[market_id]
            cost = self.cost_basis.get(market_id, 0.0)
            # Mark-to-market: current value - cost basis
            if shares > 0:
                mark_value = shares * current_price
            else:
                # Short position: we sold at some price, current liability is shares * current_price
                mark_value = shares * current_price  # negative shares * price = negative value
            total += mark_value - cost
        return total

    def check_drawdown(self, current_prices: dict[str, float]) -> bool:
        """[H-4] Update peak equity and check drawdown threshold."""
        equity = self.get_total_equity(current_prices)
        if equity > self._peak_equity:
            self._peak_equity = equity
        drawdown = (self._peak_equity - equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        if drawdown >= self.max_drawdown_pct and not self._drawdown_triggered:
            self._drawdown_triggered = True
            logger.error(
                "drawdown_kill_switch_triggered",
                equity=equity,
                peak=self._peak_equity,
                drawdown_pct=drawdown,
                threshold=self.max_drawdown_pct
            )
            self.forensic.log_event("drawdown_kill_switch", {
                "equity": equity,
                "peak": self._peak_equity,
                "drawdown_pct": drawdown,
            })
        return self._drawdown_triggered

    def settle_market(self, market_id: str, payout: float) -> None:
        """[H-4] Realistically simulate settlement of an expired market at $1 or $0."""
        shares = self.synthetic_inventory.get(market_id, 0.0)
        if abs(shares) < 1e-9:
            return
            
        cost = self.cost_basis.get(market_id, 0.0)
        mark_value = shares * payout
        pnl = mark_value - cost
        
        self.realized_pnl[market_id] = self.realized_pnl.get(market_id, 0.0) + pnl
        
        if pnl > 1e-6:
            self.win_count += 1
        elif pnl < -1e-6:
            self.loss_count += 1
            
        self.synthetic_inventory[market_id] = 0.0
        self.cost_basis[market_id] = 0.0
        
        self.forensic.log_event("market_settled", {
            "market_id": market_id,
            "payout": payout,
            "shares": shares,
            "pnl_realized": pnl
        })
        logger.info("market_settled", market_id=market_id, payout=payout, shares=shares, pnl=pnl)

    async def place_order(self, market_id: str, side: str, price: float, size: float) -> str:
        latency = sample_latency(
            self.latency_config.place_mean_ms,
            self.latency_config.place_std_ms,
            self.latency_config.p_fat_tail,
            self.latency_config.fat_tail_mult
        )
        await asyncio.sleep(latency)

        self._order_counter += 1
        order_id = f"paper_{self._order_counter}"

        # Estimate queue ahead from live book snapshot
        queue_ahead = 0.0
        book = self.latest_books.get(market_id)
        if book:
            queue_ahead = book.depth_at(price, side)

        self.live_orders[order_id] = PaperLiveOrder(
            id=order_id,
            market_id=market_id,
            side=side,
            price=price,
            size=size,
            remaining_size=size,
            queue_ahead=queue_ahead,
            created_at=time.time()
        )

        self.forensic.log_event("order_placed", {
            "order_id": order_id,
            "market_id": market_id,
            "side": side,
            "price": price,
            "size": size,
            "queue_ahead": queue_ahead,
            "latency_ms": latency * 1000.0
        })

        return order_id

    async def cancel_order(self, order_id: str, market_id: str) -> bool:
        latency = sample_latency(
            self.latency_config.cancel_mean_ms,
            self.latency_config.cancel_std_ms,
            self.latency_config.p_fat_tail,
            self.latency_config.fat_tail_mult
        )
        await asyncio.sleep(latency)

        if order_id in self.live_orders:
            del self.live_orders[order_id]
            self.forensic.log_event("order_cancelled", {
                "order_id": order_id,
                "latency_ms": latency * 1000.0
            })
            return True
        return False

    def process_fill(self, order: PaperLiveOrder, fill_size: float, trade_ts: float):
        """[C-1] Process a fill with cost-basis and P&L tracking."""
        old_inv = self.synthetic_inventory.get(order.market_id, 0.0)
        order.remaining_size -= fill_size

        pnl = 0.0

        if order.side == "BID":
            # Buying
            if old_inv < -1e-9:
                # Covering a short
                total_cost = self.cost_basis.get(order.market_id, 0.0)
                avg_short_price = abs(total_cost) / abs(old_inv)
                
                cover_size = min(fill_size, abs(old_inv))
                # For shorts, PnL is (Entry Price - Exit Price) * Size
                pnl += cover_size * (avg_short_price - order.price)
                
                # Reduce the negative cost basis
                self.cost_basis[order.market_id] = total_cost + (cover_size * avg_short_price)
                
                # If we bought more than our short, remainder opens a long
                remainder = fill_size - cover_size
                if remainder > 1e-9:
                    self.cost_basis[order.market_id] = self.cost_basis.get(order.market_id, 0.0) + remainder * order.price
            else:
                # Opening or adding to a long
                self.cost_basis[order.market_id] = self.cost_basis.get(order.market_id, 0.0) + fill_size * order.price
                
            self.synthetic_inventory[order.market_id] = old_inv + fill_size

        else:
            # Selling (ASK)
            if old_inv > 1e-9:
                # Closing a long
                total_cost = self.cost_basis.get(order.market_id, 0.0)
                avg_long_price = total_cost / old_inv
                
                sell_size = min(fill_size, old_inv)
                # For longs, PnL is (Exit Price - Entry Price) * Size
                pnl += sell_size * (order.price - avg_long_price)
                
                # Reduce the positive cost basis
                self.cost_basis[order.market_id] = total_cost - (sell_size * avg_long_price)
                
                # If we sold more than our long, remainder opens a short
                remainder = fill_size - sell_size
                if remainder > 1e-9:
                    self.cost_basis[order.market_id] = self.cost_basis.get(order.market_id, 0.0) - remainder * order.price
            else:
                # Opening or adding to a short
                self.cost_basis[order.market_id] = self.cost_basis.get(order.market_id, 0.0) - fill_size * order.price
                
            self.synthetic_inventory[order.market_id] = old_inv - fill_size

        if abs(pnl) > 1e-9:
            self.realized_pnl[order.market_id] = self.realized_pnl.get(order.market_id, 0.0) + pnl
            if pnl > 1e-6:
                self.win_count += 1
            elif pnl < -1e-6:
                self.loss_count += 1

        self.fill_count += 1

        self.forensic.log_event("order_filled", {
            "order_id": order.id,
            "market_id": order.market_id,
            "side": order.side,
            "fill_price": order.price,
            "fill_size": fill_size,
            "remaining_size": order.remaining_size,
            "trade_timestamp": trade_ts,
            "inventory_after": self.synthetic_inventory.get(order.market_id, 0.0),
            "cost_basis_after": self.cost_basis.get(order.market_id, 0.0),
            "realized_pnl": self.realized_pnl.get(order.market_id, 0.0),
            "binance_spot_mid": self.spot_mid_fetcher(order.market_id) if self.spot_mid_fetcher else None,
        })

        # [C-2] Notify ExecutionManager of fill state
        if self._fill_callback:
            self._fill_callback(order.id, order.market_id, order.remaining_size)

        if order.remaining_size <= 1e-6:
            if order.id in self.live_orders:
                del self.live_orders[order.id]

    async def on_trade(self, trade: TradeEvent):
        # We need a list so we can iterate and modify
        active_orders = [o for o in self.live_orders.values() if o.market_id == trade.market_id]

        for order in active_orders:
            # [C-3] Adverse selection penalty simulation
            # In live markets, passive limit orders are systematically adversely selected.
            # When a trade fills us, the actual price action immediately following the fill
            # is highly likely to move against us. To prevent the simulation from overstating
            # profitability, we penalize the effective fill price.
            adverse_penalty = getattr(self, "adverse_selection_bps", 10.0) / 10000.0

            if order.side == "BID":
                if trade.price < order.price:
                    # Trade crossed through our level - immediate fill
                    # The market is dumping through our bid. This is the worst adverse selection.
                    # We take the fill, but we penalize the price even further than normal to reflect
                    # that we likely got filled right before a major drop.
                    effective_price = max(0.001, order.price + adverse_penalty * 2.0)
                    # Hack to temporarily override price for this fill computation without mutating order
                    original_price = order.price
                    order.price = effective_price
                    self.process_fill(order, order.remaining_size, trade.timestamp)
                    order.price = original_price

                elif trade.price == order.price:
                    if order.queue_ahead > 0:
                        consumed = min(order.queue_ahead, trade.size)
                        order.queue_ahead -= consumed
                        remaining_trade = trade.size - consumed
                    else:
                        remaining_trade = trade.size

                    if remaining_trade > 0:
                        filled = min(order.remaining_size, remaining_trade)
                        # Normal queue erosion fill. Standard adverse selection applies.
                        effective_price = max(0.001, order.price + adverse_penalty)
                        original_price = order.price
                        order.price = effective_price
                        self.process_fill(order, filled, trade.timestamp)
                        order.price = original_price

            elif order.side == "ASK":
                if trade.price > order.price:
                    # Trade crossed through our level - immediate fill
                    # Market pumping through our ask. We sold too cheap.
                    effective_price = min(0.999, order.price - adverse_penalty * 2.0)
                    original_price = order.price
                    order.price = effective_price
                    self.process_fill(order, order.remaining_size, trade.timestamp)
                    order.price = original_price

                elif trade.price == order.price:
                    if order.queue_ahead > 0:
                        consumed = min(order.queue_ahead, trade.size)
                        order.queue_ahead -= consumed
                        remaining_trade = trade.size - consumed
                    else:
                        remaining_trade = trade.size

                    if remaining_trade > 0:
                        filled = min(order.remaining_size, remaining_trade)
                        effective_price = min(0.999, order.price - adverse_penalty)
                        original_price = order.price
                        order.price = effective_price
                        self.process_fill(order, filled, trade.timestamp)
                        order.price = original_price
