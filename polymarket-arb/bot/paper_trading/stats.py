"""
Trading statistics tracker for dashboard reporting.
"""
from dataclasses import dataclass, field
from typing import List

from bot.utils.clocks import current_timestamp_ms


@dataclass
class TradeRecord:
    """Single leg fill record."""
    timestamp: float
    market_id: str
    side: str
    price: float
    size: float
    fee: float
    opp_type: str
    opp_edge: float
    opp_id: str


@dataclass
class TradingStats:
    """
    Accumulates live trading statistics for dashboard display.
    Thread-safe for single async loop usage.
    """
    trades: List[TradeRecord] = field(default_factory=list)
    settlements: dict[str, float] = field(default_factory=dict)
    total_fees_paid: float = 0.0
    total_volume: float = 0.0
    opportunities_detected: int = 0
    opportunities_executed: int = 0
    opportunities_rejected_risk: int = 0
    opportunities_rejected_dedup: int = 0
    fills_count: int = 0
    rejects_no_liquidity: int = 0
    leg_imbalances_count: int = 0
    _start_time_ms: int = field(default_factory=current_timestamp_ms)

    def record_fill(
        self,
        market_id: str,
        side: str,
        price: float,
        size: float,
        fee: float,
        opp_type: str = "",
        opp_edge: float = 0.0,
        opp_id: str = "",
    ) -> None:
        self.trades.append(TradeRecord(
            timestamp=current_timestamp_ms() / 1000.0,
            market_id=market_id,
            side=side,
            price=price,
            size=size,
            fee=fee,
            opp_type=opp_type,
            opp_edge=opp_edge,
            opp_id=opp_id,
        ))
        self.total_fees_paid += fee
        self.total_volume += price * size
        self.fills_count += 1

    def record_opportunity_detected(self) -> None:
        self.opportunities_detected += 1

    def record_opportunity_executed(self) -> None:
        self.opportunities_executed += 1

    def record_risk_rejection(self) -> None:
        self.opportunities_rejected_risk += 1

    def record_dedup_rejection(self) -> None:
        self.opportunities_rejected_dedup += 1

    def record_no_liquidity(self) -> None:
        self.rejects_no_liquidity += 1

    def record_leg_imbalance(self) -> None:
        self.leg_imbalances_count += 1

    def record_settlement(self, market_id: str, settle_price: float) -> None:
        self.settlements[market_id] = settle_price

    @property
    def uptime_seconds(self) -> float:
        return (current_timestamp_ms() - self._start_time_ms) / 1000.0

    @property
    def uptime_str(self) -> str:
        s = int(self.uptime_seconds)
        h, remainder = divmod(s, 3600)
        m, sec = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"

    @property
    def avg_edge(self) -> float:
        edges = [t.opp_edge for t in self.trades if t.opp_edge > 0]
        return sum(edges) / len(edges) if edges else 0.0

    @property
    def avg_fill_price(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.price for t in self.trades) / len(self.trades)

    def get_win_rate(self, active_market_ids: set[str]) -> tuple[float, int, int]:
        """
        Win rate based on per-opportunity net PnL.
        Returns (rate, wins, losses).
        
        TYPE-C (parity) trades count each leg individually (2 legs = 2 W or 2 L)
        since they execute both sides. TYPE-B counts as 1 per opportunity.
        TYPE-B trades are excluded while their markets are still active.
        """
        if len(self.trades) < 2:
            return (0.0, 0, 0)
        
        wins = 0
        losses = 0
        for group in self._group_trades():
            if not group:
                continue
            if self._should_exclude_group(group, active_market_ids):
                continue
            opp_type = group[0].opp_type or "UNKNOWN"
            pnl = self._compute_opp_pnl(group, include_fees=True)
            if pnl is not None:
                # Parity trades: count each leg (both sides executed)
                leg_count = len(group) if ("TYPE-C" in opp_type or "TYPE-A" in opp_type) else 1
                if pnl >= 0:
                    wins += leg_count
                else:
                    losses += leg_count
        
        total = wins + losses
        return (wins / total if total > 0 else 0.0, wins, losses)

    def get_win_rates_by_type(self, active_market_ids: set[str]) -> dict[str, tuple[float, int, int]]:
        """Win rates segmented by opportunity type. Parity trades count each leg."""
        from collections import defaultdict
        wins_by_type = defaultdict(int)
        totals_by_type = defaultdict(int)
        
        for group in self._group_trades():
            if not group:
                continue
            if self._should_exclude_group(group, active_market_ids):
                continue
            opp_type = group[0].opp_type or "UNKNOWN"
            pnl = self._compute_opp_pnl(group, include_fees=True)
            if pnl is not None:
                leg_count = len(group) if ("TYPE-C" in opp_type or "TYPE-A" in opp_type) else 1
                totals_by_type[opp_type] += leg_count
                if pnl >= 0:
                    wins_by_type[opp_type] += leg_count
                
        result = {}
        for t in ["TYPE-B", "TYPE-C"]:
            if totals_by_type[t] > 0:
                wins = wins_by_type[t]
                losses = totals_by_type[t] - wins
                result[t] = (wins / totals_by_type[t], wins, losses)
            else:
                result[t] = (0.0, 0, 0)
                
        for t, total in totals_by_type.items():
            if t not in result and total > 0:
                wins = wins_by_type[t]
                losses = total - wins
                result[t] = (wins / total, wins, losses)
                
        return result

    def get_win_rates_by_market(self, token_to_market_name: dict[str, str], active_market_ids: set[str]) -> dict[str, tuple[float, int, int]]:
        """Win rates segmented by market name."""
        from collections import defaultdict
        wins_by_mkt = defaultdict(int)
        totals_by_mkt = defaultdict(int)
        
        for group in self._group_trades():
            if not group:
                continue
            if self._should_exclude_group(group, active_market_ids):
                continue
            market_name = token_to_market_name.get(group[0].market_id, "Unknown Market")
            pnl = self._compute_opp_pnl(group, include_fees=True)
            if pnl is not None:
                totals_by_mkt[market_name] += 1
                if pnl >= 0:
                    wins_by_mkt[market_name] += 1
                
        result = {}
        for mkt, total in totals_by_mkt.items():
            if total > 0:
                wins = wins_by_mkt[mkt]
                losses = total - wins
                result[mkt] = (wins / total, wins, losses)
                
        return result

    def _compute_opp_pnl(self, group: list, include_fees: bool = True) -> float | None:
        """Compute PnL for a single opportunity group of trades.
        
        Args:
            group: List of TradeRecord for one opportunity.
            include_fees: If True, fees are deducted inline (net PnL).
                          If False, fees are excluded (gross PnL).
        Returns:
            float PnL or None if the trade is unsettled and payout cannot be determined.
        """
        if not group:
            return 0.0

        opp_type = group[0].opp_type or "UNKNOWN"

        pnl = 0.0
        for t in group:
            if t.side == "SELL":
                pnl += t.price * t.size - (t.fee if include_fees else 0.0)
            else:  # BUY
                pnl -= t.price * t.size + (t.fee if include_fees else 0.0)

        if len(group) == 2:
            # Use min of both legs for guaranteed matched payout
            matched_size = min(group[0].size, group[1].size)
            is_buy = group[0].side == "BUY"

            if "TYPE-A" in opp_type or "TYPE-C" in opp_type:
                if is_buy:
                    pnl += matched_size * 1.0  # Payout from buying parity
                else:
                    pnl -= matched_size * 1.0  # Liability from selling parity
            elif "TYPE-B" in opp_type:
                # TYPE-B (Cross-interval) has no guaranteed payout until settlement.
                if group[0].market_id in self.settlements and group[1].market_id in self.settlements:
                    payout = 0.0
                    for t in group:
                        settle = self.settlements[t.market_id]
                        if t.side == "BUY":
                            payout += t.size * settle
                        else:  # SELL
                            payout -= t.size * settle
                    return pnl + payout
                return None

        # For single-leg groups (leg imbalance)
        if len(group) == 1:
            t = group[0]
            if t.market_id in self.settlements:
                settle = self.settlements[t.market_id]
                payout = t.size * settle if t.side == "BUY" else -t.size * settle
                return pnl + payout
            return None

        return pnl

    def _group_trades(self) -> list[list]:
        """Group trades by opp_id into opportunity groups."""
        from collections import defaultdict
        opps_map: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in self.trades:
            if t.opp_id:
                opps_map[t.opp_id].append(t)
            else:
                opps_map[f"standalone_{t.timestamp}"].append(t)
        return list(opps_map.values())

    def get_pnl_by_type(self, active_market_ids: set[str]) -> dict[str, float]:
        """Total net PnL segmented by opportunity type."""
        from collections import defaultdict
        pnl_by: dict[str, float] = defaultdict(float)

        for group in self._group_trades():
            if not group:
                continue
            if self._should_exclude_group(group, active_market_ids):
                continue
            opp_type = group[0].opp_type or "UNKNOWN"
            pnl = self._compute_opp_pnl(group, include_fees=True)
            if pnl is not None:
                pnl_by[opp_type] += pnl

        # Ensure active types are present
        for t in ["TYPE-B", "TYPE-C"]:
            if t not in pnl_by:
                pnl_by[t] = 0.0

        return dict(pnl_by)

    def get_pnl_by_market(self, token_to_market_name: dict[str, str], active_market_ids: set[str]) -> dict[str, float]:
        """Total net PnL segmented by market name."""
        from collections import defaultdict
        pnl_by: dict[str, float] = defaultdict(float)

        for group in self._group_trades():
            if not group:
                continue
            if self._should_exclude_group(group, active_market_ids):
                continue
            market_name = token_to_market_name.get(group[0].market_id, "Unknown Market")
            pnl = self._compute_opp_pnl(group, include_fees=True)
            if pnl is not None:
                pnl_by[market_name] += pnl

        return dict(pnl_by)

    def get_gross_pnl(self, active_market_ids: set[str]) -> float:
        """Total P&L ignoring fees."""
        total_gross = 0.0
        for group in self._group_trades():
            if not group or self._should_exclude_group(group, active_market_ids):
                continue
            pnl = self._compute_opp_pnl(group, include_fees=False)
            if pnl is not None:
                total_gross += pnl
        return total_gross

    def get_net_pnl(self, active_market_ids: set[str]) -> float:
        """P&L after fees. Equals sum of pnl_by_type values."""
        total_net = 0.0
        for group in self._group_trades():
            if not group or self._should_exclude_group(group, active_market_ids):
                continue
            pnl = self._compute_opp_pnl(group, include_fees=True)
            if pnl is not None:
                total_net += pnl
        return total_net

    def _should_exclude_group(self, group: list, active_market_ids: set[str]) -> bool:
        """Determine if a trade group should be excluded from stats.
        
        TYPE-C/TYPE-A parity trades have deterministic PnL ($1.00 guaranteed
        payout) so they are ALWAYS included. TYPE-B trades depend on actual
        market resolution and are excluded while their markets are still active.
        """
        opp_type = group[0].opp_type or "UNKNOWN"
        if "TYPE-C" in opp_type or "TYPE-A" in opp_type:
            return False  # Parity trades always countable
        return any(t.market_id in active_market_ids for t in group)
