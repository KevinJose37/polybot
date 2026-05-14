"""
utils/pnl_engine.py — PnL attribution engine.
Tracks spread PnL, inventory PnL, and fee PnL separately.
"""

import json
import os
import time
from loguru import logger

from collections import defaultdict
from config.settings import config
from utils.schemas import PnLBreakdown, FillRecord, InventoryState


class PnLEngine:
    """
    PnL engine with full attribution:
    - Spread PnL: round-trip profit from bid-ask spread captures
    - Inventory PnL: mark-to-market from holding directional positions
    - Fee PnL: maker/taker fees paid
    """

    def __init__(self):
        self.pnl = PnLBreakdown()

        # Track fills for round-trip matching
        self._unmatched_buys: dict[str, list[FillRecord]] = {}  # market_key -> [fills]
        self._unmatched_sells: dict[str, list[FillRecord]] = {}

        # Tracking win ratio globally and per asset
        self.winning_trades = 0
        self.total_closed_trades = 0
        self.winning_trades_by_asset = defaultdict(int)
        self.total_closed_trades_by_asset = defaultdict(int)
        
        # Tracking sold positions for Hold vs Sold metric
        self.sold_positions = [] # List of {"market_key": str, "asset": str, "side": str, "exit_price": float, "size": int}

        # All fill records for persistence
        self._all_fills: list[dict] = []

    def record_fill(self, market_key: str, fill: FillRecord):
        """
        Record a fill and compute PnL attribution.
        Attempts to match buys with sells for spread PnL.
        """
        # Record fee PnL (includes gas cost baked into fill.fee by the simulator)
        self.pnl.fee_pnl -= fill.fee

        # Store fill
        self._all_fills.append({
            "market_key": market_key,
            "fill_id": fill.fill_id,
            "side": fill.side,
            "price": fill.price,
            "size": fill.size,
            "fee": fill.fee,
            "is_maker": fill.is_maker,
            "timestamp_ms": fill.timestamp_ms,
        })

        # Try to match for spread PnL
        if fill.side == "BUY":
            if market_key not in self._unmatched_buys:
                self._unmatched_buys[market_key] = []
            self._unmatched_buys[market_key].append(fill)
            self._try_match(market_key)
        else:
            if market_key not in self._unmatched_sells:
                self._unmatched_sells[market_key] = []
            self._unmatched_sells[market_key].append(fill)
            self._try_match(market_key)

        self.pnl.update_total()

    def _try_match(self, market_key: str):
        """
        Match unmatched buys with sells (FIFO) to compute spread PnL.
        Spread PnL = sell_price - buy_price per matched unit.
        """
        buys = self._unmatched_buys.get(market_key, [])
        sells = self._unmatched_sells.get(market_key, [])

        while buys and sells:
            buy = buys[0]
            sell = sells[0]

            # Reduce matched amounts by tracking remaining sizes
            if not hasattr(buy, "remaining_size"):
                buy.remaining_size = buy.size
            if not hasattr(sell, "remaining_size"):
                sell.remaining_size = sell.size

            match_size = min(buy.remaining_size, sell.remaining_size)
            spread_profit = (sell.price - buy.price) * match_size

            self.pnl.spread_pnl += spread_profit
            self.pnl.realized_pnl += spread_profit

            asset = getattr(buy, "asset", market_key.split("-")[0] if "-" in market_key else "unknown")
            
            # Track win ratio
            if spread_profit > 0:
                self.winning_trades += match_size
                self.winning_trades_by_asset[asset] += match_size
            self.total_closed_trades += match_size
            self.total_closed_trades_by_asset[asset] += match_size
            
            # Record the exit (sell) for hold vs sold tracking
            self.sold_positions.append({
                "market_key": market_key,
                "asset": asset,
                "side": "LONG_EXIT",
                "exit_price": sell.price,
                "size": match_size
            })

            buy.remaining_size -= match_size
            sell.remaining_size -= match_size

            if buy.remaining_size == 0:
                buys.pop(0)
            if sell.remaining_size == 0:
                sells.pop(0)

            logger.debug(
                f"[PnL] Matched {match_size} units in {market_key}: "
                f"buy@{buy.price:.4f} sell@{sell.price:.4f} -> spread PnL: ${spread_profit:.4f}"
            )

    def _calculate_vwap(self, l2_levels: list[dict], position_size: int) -> float:
        """Calculate the VWAP to exit a position of given size using L2 orderbook."""
        remaining = abs(position_size)
        total_value = 0.0
        last_price = 0.0
        for level in l2_levels:
            p = float(level.get("price", 0))
            s = float(level.get("size", 0))
            last_price = p
            take_size = min(remaining, s)
            total_value += take_size * p
            remaining -= take_size
            if remaining <= 0:
                break

        if remaining > 0:
            # Penalty for exceeding available L2 depth:
            # Use last seen price minus configurable slippage penalty
            slippage_penalty = config.sim_vwap_slippage_penalty
            penalty_price = max(0.0, last_price * (1.0 - slippage_penalty))
            total_value += remaining * penalty_price

        return total_value / abs(position_size) if position_size > 0 else 0.0

    def update_inventory_pnl(
        self,
        inventories: dict[str, InventoryState],
        market_odds_dict: dict[str, 'MarketOdds'],
    ):
        """
        Update inventory (unrealized) PnL based on current Polymarket orderbook.
        Uses VWAP from L2 depth.
        """
        total_inv_pnl = 0.0

        for key, inv in inventories.items():
            if inv.net_position == 0 or inv.avg_entry_price == 0:
                continue

            odds = market_odds_dict.get(key)
            if not odds:
                continue

            if inv.net_position > 0:
                # Long: must exit at the bids
                exit_price = self._calculate_vwap(odds.bids, inv.net_position) if odds.bids else 0.0
                inv_pnl = (exit_price - inv.avg_entry_price) * inv.net_position
            else:
                # Short: must exit at the asks
                exit_price = self._calculate_vwap(odds.asks, abs(inv.net_position)) if odds.asks else 1.0
                inv_pnl = (inv.avg_entry_price - exit_price) * abs(inv.net_position)

            total_inv_pnl += inv_pnl

        self.pnl.inventory_pnl = total_inv_pnl
        self.pnl.unrealized_pnl = total_inv_pnl
        self.pnl.update_total()

    def get_hold_vs_sold_metrics(self, market_odds_dict: dict[str, 'MarketOdds']) -> dict[str, float]:
        """
        Calculates how much better/worse it was to sell compared to holding to current MTM.
        Returns dictionary of metric per asset.
        """
        metrics = defaultdict(float)
        for pos in self.sold_positions:
            odds = market_odds_dict.get(pos["market_key"])
            if not odds:
                continue
            
            # What would the MTM VWAP be if we held?
            if pos["side"] == "LONG_EXIT":
                current_vwap = self._calculate_vwap(odds.bids, pos["size"]) if odds.bids else 0.0
                # Selling at exit_price vs holding at current_vwap
                benefit_of_selling = (pos["exit_price"] - current_vwap) * pos["size"]
                metrics[pos["asset"]] += benefit_of_selling
                
        return dict(metrics)

    def get_pnl(self) -> PnLBreakdown:
        """Get current PnL breakdown."""
        return self.pnl

    def save_to_file(self, filepath: str = ""):
        """Save fill history and PnL to JSON file."""
        filepath = filepath or config.fills_file
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

        data = {
            "pnl": {
                "spread_pnl": self.pnl.spread_pnl,
                "inventory_pnl": self.pnl.inventory_pnl,
                "fee_pnl": self.pnl.fee_pnl,
                "total_pnl": self.pnl.total_pnl,
                "realized_pnl": self.pnl.realized_pnl,
                "unrealized_pnl": self.pnl.unrealized_pnl,
            },
            "fills": self._all_fills,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

        try:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"[PnL] Saved to {filepath}")
        except Exception as e:
            logger.error(f"[PnL] Error saving to {filepath}: {e}")

    def get_stats(self) -> dict:
        """Get summary statistics."""
        total_fills = len(self._all_fills)
        buy_fills = sum(1 for f in self._all_fills if f["side"] == "BUY")
        sell_fills = total_fills - buy_fills

        win_ratio = (self.winning_trades / self.total_closed_trades * 100) if self.total_closed_trades > 0 else 0.0

        win_ratios_by_asset = {}
        for asset in self.total_closed_trades_by_asset:
            closed = self.total_closed_trades_by_asset[asset]
            won = self.winning_trades_by_asset[asset]
            win_ratios_by_asset[asset] = (won / closed * 100) if closed > 0 else 0.0

        return {
            "total_fills": total_fills,
            "buy_fills": buy_fills,
            "sell_fills": sell_fills,
            "win_ratio": win_ratio,
            "win_ratios_by_asset": win_ratios_by_asset,
            "spread_pnl": self.pnl.spread_pnl,
            "inventory_pnl": self.pnl.inventory_pnl,
            "fee_pnl": self.pnl.fee_pnl,
            "total_pnl": self.pnl.total_pnl,
        }
