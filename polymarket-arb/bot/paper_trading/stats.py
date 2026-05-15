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

    @property
    def win_rate(self) -> float:
        """
        Win rate based on per-opportunity P&L (grouped by timestamp clusters).
        A trade pair (SELL leg + BUY leg within 500ms) is a single opportunity.
        """
        if len(self.trades) < 2:
            return (0.0, 0, 0)
        
        # Group trades by opp_id
        from collections import defaultdict
        opps_map: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in self.trades:
            if t.opp_id:
                opps_map[t.opp_id].append(t)
            else:
                opps_map[f"standalone_{t.timestamp}"].append(t)
                
        opps = list(opps_map.values())
        
        wins = 0
        losses = 0
        for group in opps:
            if not group:
                continue
                
            pnl = 0.0
            for t in group:
                if t.side == "SELL":
                    pnl += t.price * t.size - t.fee
                else:  # BUY
                    pnl -= t.price * t.size + t.fee
                    
            if len(group) == 2:
                opp_type = group[0].opp_type
                size = group[0].size
                is_buy = group[0].side == "BUY"
                
                if "TYPE-A" in opp_type or "TYPE-C" in opp_type:
                    if is_buy:
                        pnl += size * 1.0  # Payout from buying parity
                    else:
                        pnl -= size * 1.0  # Liability from selling parity
                elif "TYPE-B" in opp_type:
                    # Type B is cross-interval; payouts and liabilities perfectly cancel out.
                    pass
            else:
                # Leg imbalance. M2M value is roughly net zero minus fees
                pnl = -sum(t.fee for t in group)

            if pnl >= 0:
                wins += 1
            else:
                losses += 1
        
        total = wins + losses
        return (wins / total if total > 0 else 0.0, wins, losses)

    @property
    def win_rates_by_type(self) -> dict[str, tuple[float, int, int]]:
        """Win rates segmented by opportunity type."""
        from collections import defaultdict
        opps_map: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in self.trades:
            if t.opp_id:
                opps_map[t.opp_id].append(t)
            else:
                opps_map[f"standalone_{t.timestamp}"].append(t)
                
        wins_by_type = defaultdict(int)
        totals_by_type = defaultdict(int)
        
        for group in opps_map.values():
            if not group:
                continue
                
            opp_type = group[0].opp_type or "UNKNOWN"
                
            pnl = 0.0
            for t in group:
                if t.side == "SELL":
                    pnl += t.price * t.size - t.fee
                else:  # BUY
                    pnl -= t.price * t.size + t.fee
                    
            if len(group) == 2:
                size = group[0].size
                is_buy = group[0].side == "BUY"
                
                if "TYPE-A" in opp_type or "TYPE-C" in opp_type:
                    if is_buy:
                        pnl += size * 1.0  # Payout from buying parity
                    else:
                        pnl -= size * 1.0  # Liability from selling parity
                elif "TYPE-B" in opp_type:
                    pass
            else:
                pnl = -sum(t.fee for t in group)

            totals_by_type[opp_type] += 1
            if pnl >= 0:
                wins_by_type[opp_type] += 1
                
        result = {}
        for t in ["TYPE-A", "TYPE-B", "TYPE-C"]:
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

    def win_rates_by_market(self, token_to_market_name: dict[str, str]) -> dict[str, tuple[float, int, int]]:
        """Win rates segmented by market name."""
        from collections import defaultdict
        opps_map: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in self.trades:
            if t.opp_id:
                opps_map[t.opp_id].append(t)
            else:
                opps_map[f"standalone_{t.timestamp}"].append(t)
                
        wins_by_mkt = defaultdict(int)
        totals_by_mkt = defaultdict(int)
        
        for group in opps_map.values():
            if not group:
                continue
                
            opp_type = group[0].opp_type or "UNKNOWN"
            # Get base market name for the first leg
            market_name = token_to_market_name.get(group[0].market_id, "Unknown Market")
                
            pnl = 0.0
            for t in group:
                if t.side == "SELL":
                    pnl += t.price * t.size - t.fee
                else:  # BUY
                    pnl -= t.price * t.size + t.fee
                    
            if len(group) == 2:
                size = group[0].size
                is_buy = group[0].side == "BUY"
                
                if "TYPE-A" in opp_type or "TYPE-C" in opp_type:
                    if is_buy:
                        pnl += size * 1.0  # Payout from buying parity
                    else:
                        pnl -= size * 1.0  # Liability from selling parity
                elif "TYPE-B" in opp_type:
                    pass
            else:
                pnl = -sum(t.fee for t in group)

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

    @property
    def gross_pnl(self) -> float:
        """Total P&L ignoring fees."""
        if not self.trades:
            return 0.0
            
        # Group trades by opp_id
        from collections import defaultdict
        opps_map: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in self.trades:
            if t.opp_id:
                opps_map[t.opp_id].append(t)
            else:
                opps_map[f"standalone_{t.timestamp}"].append(t)
                
        opps = list(opps_map.values())
            
        total_pnl = 0.0
        for group in opps:
            if not group:
                continue
                
            pnl = 0.0
            for t in group:
                if t.side == "SELL":
                    pnl += t.price * t.size
                else:
                    pnl -= t.price * t.size
                    
            if len(group) == 2:
                opp_type = group[0].opp_type
                size = group[0].size
                is_buy = group[0].side == "BUY"
                
                if "TYPE-A" in opp_type or "TYPE-C" in opp_type:
                    if is_buy:
                        pnl += size * 1.0
                    else:
                        pnl -= size * 1.0
            else:
                pnl = 0.0  # Ignore unhedged cashflow for gross PnL
                
            total_pnl += pnl
            
        return total_pnl

    @property 
    def net_pnl(self) -> float:
        """P&L after fees."""
        return self.gross_pnl - self.total_fees_paid
