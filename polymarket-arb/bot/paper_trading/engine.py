"""Paper execution engine."""
import structlog
import hashlib
import asyncio

from bot.execution.executor import ExecutorProtocol
from bot.execution.position_manager import PositionManager
from bot.execution.fill_manager import FillManager
from bot.paper_trading.latency import inject_latency
from bot.paper_trading.slippage import apply_slippage
from bot.paper_trading.fills import simulate_fill
from bot.paper_trading.stats import TradingStats
from bot.api.schemas import OrderRequest, OrderAck
from bot.arbitrage.opportunity import ArbOpportunity, ArbType
from bot.risk.engine import RiskEngine, RiskKillSwitchTriggered
from bot.monitoring.forensic import ForensicLogger
from bot.settings import Settings
from bot.utils.clocks import current_timestamp_ms

logger = structlog.get_logger(__name__)


from bot.orderbook.local_book import LocalOrderBook

class PaperExecutor(ExecutorProtocol):
    """Simulated executor matching ExecutorProtocol."""
    def __init__(
        self,
        settings: Settings,
        risk_engine: RiskEngine,
        position_manager: PositionManager,
        fill_manager: FillManager,
        orderbooks: dict[str, LocalOrderBook],
        stats: TradingStats | None = None,
        fee_rates: dict[str, float] | None = None,
        forensic: ForensicLogger | None = None,
    ):
        self.settings = settings
        self.risk_engine = risk_engine
        self.position_manager = position_manager
        self.fill_manager = fill_manager
        self.orderbooks = orderbooks
        self.stats = stats or TradingStats()
        self.fee_rates = fee_rates or {}
        self.forensic = forensic

    async def execute_opportunity(self, opportunity: ArbOpportunity) -> list[OrderAck]:
        """Execute a full arbitrage opportunity."""
        if self.fill_manager.check_and_mark(opportunity.opportunity_id):
            self.stats.record_dedup_rejection()
            return []

        # ── Atomic capacity reservation ──
        # Compute total notional for ALL legs and validate that the combined
        # exposure fits within limits BEFORE executing any leg.
        total_notional = sum(leg.size * leg.price for leg in opportunity.legs)
        if not self.risk_engine.reserve_exposure(total_notional):
            self.stats.record_risk_rejection()
            return []

        try:
            for leg in opportunity.legs:
                try:
                    if not self.risk_engine.validate_order(
                        leg.market_id, leg.size, price=leg.price, orderbooks=self.orderbooks, check_portfolio=False, side=leg.side
                    ):
                        self.stats.record_risk_rejection()
                        return []
                except RiskKillSwitchTriggered:
                    self.stats.record_risk_rejection()
                    return []
            self.stats.record_opportunity_executed()
            
            logger.info(
                "paper_exec", 
                opp_id=opportunity.opportunity_id[:8], 
                type=opportunity.type.value,
                edge=f"{opportunity.edge*100:.2f}%",
                size=f"${opportunity.size:.2f}",
            )

            acks = []
            filled_legs = []
            fill_details = []
            matched_size: float | None = None
            exec_start_ms = current_timestamp_ms()
            leg_fill_times: list[int] = []

            for i, leg in enumerate(opportunity.legs):
                # For parity arb, enforce matched sizing
                leg_size = leg.size
                if matched_size is not None and opportunity.type in (ArbType.PARITY, ArbType.EXHAUSTIVE):
                    leg_size = matched_size
                
                order = OrderRequest(
                    market_id=leg.market_id,
                    side=leg.side, # type: ignore
                    price=leg.price,
                    size=leg_size
                )
                leg_start = current_timestamp_ms()
                ack = await self.place_order(order, opp=opportunity, check_portfolio=False)
                leg_end = current_timestamp_ms()
                acks.append(ack)

                if ack.status == "FILLED":
                    # Use the actual fill size returned by place_order,
                    # NOT the cumulative position size.
                    actual_fill_size = ack.filled_size
                    if matched_size is None:
                        matched_size = actual_fill_size

                    # Capture fill details for forensic log
                    fee_rate = self.fee_rates.get(leg.market_id, self.settings.trading.polymarket_fee)
                    from bot.utils.math import polymarket_taker_fee
                    fee = polymarket_taker_fee(ack.fill_price, actual_fill_size, fee_rate, side=str(leg.side))
                    fill_details.append({
                        "fill_price": ack.fill_price,
                        "filled_size": actual_fill_size,
                        "fee": fee,
                        "latency_ms": leg_end - leg_start,
                    })
                    leg_fill_times.append(leg_end)
                    filled_legs.append(i)
                else:
                    fill_details.append({
                        "fill_price": 0.0, "filled_size": 0.0, "fee": 0.0,
                        "latency_ms": leg_end - leg_start,
                    })
                    if filled_legs:
                        logger.warning(
                            "leg_imbalance",
                            opp_id=opportunity.opportunity_id[:8],
                            filled=filled_legs, failed=i,
                        )
                        self.stats.record_leg_imbalance()
                        
                        # Unwind previously filled legs
                        for filled_idx in filled_legs:
                            filled_leg = opportunity.legs[filled_idx]
                            filled_detail = fill_details[filled_idx]
                            actual_filled_size = filled_detail["filled_size"]
                            if actual_filled_size > 0:
                                unwind_side = "SELL" if filled_leg.side == "BUY" else "BUY"
                                unwind_price = 0.001 if unwind_side == "SELL" else 0.999
                                unwind_order = OrderRequest(
                                    market_id=filled_leg.market_id,
                                    side=unwind_side,
                                    price=unwind_price,
                                    size=actual_filled_size
                                )
                                logger.critical("unwinding_leg", market_id=filled_leg.market_id, side=unwind_side, size=actual_filled_size)
                                asyncio.create_task(self.place_order(unwind_order, check_portfolio=False, ignore_kill_switch=True))
                                
                        logger.error("leg_imbalance_unwinding", opp_id=opportunity.opportunity_id[:8], failed_leg=i, filled_legs=filled_legs)

            # Forensic log
            if self.forensic:
                legging_gap = (leg_fill_times[-1] - leg_fill_times[0]) if len(leg_fill_times) >= 2 else 0
                self.forensic.log_executed_opportunity(
                    opp=opportunity, acks=acks, orderbooks=self.orderbooks,
                    fee_rates=self.fee_rates,
                    slippage_est=self.settings.trading.slippage_est,
                    kelly_multiplier=self.settings.trading.kelly_fraction_multiplier,
                    fill_details=fill_details,
                    legging_gap_ms=legging_gap,
                )
                
            return acks
        finally:
            self.risk_engine.release_exposure(total_notional)

    async def place_order(self, order: OrderRequest, opp: ArbOpportunity | None = None, check_portfolio: bool = True, ignore_kill_switch: bool = False) -> OrderAck:
        """Place a single order and simulate fill."""
        try:
            if not self.risk_engine.validate_order(
                order.market_id, order.size, price=order.price, orderbooks=self.orderbooks, check_portfolio=check_portfolio, side=order.side, ignore_kill_switch=ignore_kill_switch
            ):
                return OrderAck(order_id="failed", status="REJECTED", message="Risk Engine Rejected")
        except RiskKillSwitchTriggered as e:
            return OrderAck(order_id="failed", status="REJECTED", message=str(e))

        # 1. Generate order ID and track as inflight (mirrors LiveExecutor)
        book = self.orderbooks.get(order.market_id)
        order_id = hashlib.sha256(f"{order.market_id}_{order.side}_{order.price}".encode()).hexdigest()[:16]

        if not book:
            return OrderAck(order_id=order_id, status="REJECTED")

        self.fill_manager.add_inflight_order(order_id, {
            "market": order.market_id, "size": order.size, "side": order.side,
        })

        try:
            # 2. Inject Latency
            start_lat = current_timestamp_ms()
            await inject_latency(
                self.settings.paper_trading.mean_latency_ms,
                self.settings.paper_trading.std_latency_ms
            )
            latency_ms = current_timestamp_ms() - start_lat

            # 3. Fill logic via depth-weighted VWAP
            is_filled, filled_size, vwap_price = simulate_fill(
                order.size, book, str(order.side), slippage_pct=self.settings.trading.slippage_est, order_type="IOC", latency_ms=latency_ms, limit_price=order.price
            )

            if is_filled and filled_size > 0:
                fee_rate = self.fee_rates.get(order.market_id, self.settings.trading.polymarket_fee)
                from bot.utils.math import polymarket_taker_fee
                total_fee = polymarket_taker_fee(vwap_price, filled_size, fee_rate, side=str(order.side))
                self.position_manager.add_fill(
                    market_id=order.market_id,
                    side=order.side,
                    price=vwap_price,
                    size=filled_size,
                    fee=total_fee
                )

                opp_type = opp.type.value if opp else ""
                opp_edge = opp.edge if opp else 0.0
                opp_id = opp.opportunity_id if opp else ""
                self.stats.record_fill(
                    market_id=order.market_id,
                    side=order.side,
                    price=vwap_price,
                    size=filled_size,
                    fee=total_fee,
                    opp_type=opp_type,
                    opp_edge=opp_edge,
                    opp_id=opp_id,
                )

                return OrderAck(
                    order_id=order_id,
                    status="FILLED",
                    filled_size=filled_size,
                    fill_price=vwap_price,
                )
            else:
                self.stats.record_no_liquidity()
                return OrderAck(order_id=order_id, status="REJECTED")
        finally:
            self.fill_manager.remove_inflight_order(order_id)

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order."""
        return True
