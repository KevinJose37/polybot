"""
Paper execution engine.
"""
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
from bot.arbitrage.opportunity import ArbOpportunity
from bot.risk.engine import RiskEngine, RiskKillSwitchTriggered
from bot.settings import Settings

logger = structlog.get_logger(__name__)


from bot.orderbook.local_book import LocalOrderBook

class PaperExecutor(ExecutorProtocol):
    """
    Simulated executor matching ExecutorProtocol.
    """
    def __init__(
        self,
        settings: Settings,
        risk_engine: RiskEngine,
        position_manager: PositionManager,
        fill_manager: FillManager,
        orderbooks: dict[str, LocalOrderBook],
        stats: TradingStats | None = None,
    ):
        self.settings = settings
        self.risk_engine = risk_engine
        self.position_manager = position_manager
        self.fill_manager = fill_manager
        self.orderbooks = orderbooks
        self.stats = stats or TradingStats()

    async def execute_opportunity(self, opportunity: ArbOpportunity) -> list[OrderAck]:
        """Execute a full arbitrage opportunity."""
        if self.fill_manager.is_duplicate(opportunity.opportunity_id):
            self.stats.record_dedup_rejection()
            return []
            
        for leg in opportunity.legs:
            try:
                if not self.risk_engine.validate_order(leg.market_id, leg.size, orderbooks=self.orderbooks):
                    logger.warning("paper_risk_rejected_order", market_id=leg.market_id, size=leg.size)
                    self.stats.record_risk_rejection()
                    return []
            except RiskKillSwitchTriggered as e:
                logger.critical("paper_execution_halted", reason=str(e))
                self.stats.record_risk_rejection()
                return []
            
        self.fill_manager.mark_executed(opportunity.opportunity_id)
        self.stats.record_opportunity_executed()
        
        logger.info(
            "paper_executing_opportunity", 
            opp_id=opportunity.opportunity_id, 
            type=opportunity.type.value,
            edge=f"{opportunity.edge*100:.2f}%",
            size=opportunity.size
        )

        self._current_opp = opportunity
        acks = []
        filled_legs = []
        for i, leg in enumerate(opportunity.legs):
            order = OrderRequest(
                market_id=leg.market_id,
                side=leg.side, # type: ignore
                price=leg.price,
                size=leg.size
            )
            ack = await self.place_order(order, opp=opportunity)
            acks.append(ack)
            if ack.status == "FILLED":
                filled_legs.append(i)
            elif filled_legs:
                # Leg imbalance: previous leg(s) filled but this one failed
                logger.critical(
                    "leg_imbalance_detected",
                    opp_id=opportunity.opportunity_id,
                    filled_legs=filled_legs,
                    failed_leg=i,
                    total_legs=len(opportunity.legs),
                )
                self.stats.record_leg_imbalance()
            
        return acks

    async def place_order(self, order: OrderRequest, opp: ArbOpportunity | None = None) -> OrderAck:
        """Place a single order and simulate fill."""
        try:
            if not self.risk_engine.validate_order(order.market_id, order.size, orderbooks=self.orderbooks):
                logger.warning("paper_risk_rejected_order", market_id=order.market_id, size=order.size)
                return OrderAck(order_id="failed", status="REJECTED", message="Risk Engine Rejected")
        except RiskKillSwitchTriggered as e:
            logger.critical("paper_execution_halted", reason=str(e))
            return OrderAck(order_id="failed", status="REJECTED", message=str(e))

        # 1. Inject Latency
        await inject_latency(
            self.settings.paper_trading.mean_latency_ms,
            self.settings.paper_trading.std_latency_ms
        )
        
        # 2. Fill logic via depth-weighted VWAP
        book = self.orderbooks.get(order.market_id)
        order_id = hashlib.sha256(f"{order.market_id}_{order.side}_{order.price}".encode()).hexdigest()[:16]
        
        if not book:
            logger.warning("paper_order_rejected_no_book", order_id=order_id, market=order.market_id)
            return OrderAck(order_id=order_id, status="REJECTED")

        is_filled, filled_size, vwap_price = simulate_fill(
            order.size, book, str(order.side), slippage_pct=self.settings.trading.slippage_est
        )
        
        if is_filled and filled_size > 0:
            # Calculate the actual Polymarket fee for this fill
            fee_per_unit = self.settings.trading.polymarket_fee * min(vwap_price, 1.0 - vwap_price)
            total_fee = fee_per_unit * filled_size
            self.position_manager.add_fill(
                market_id=order.market_id,
                side=order.side,
                price=vwap_price,
                size=filled_size,
                fee=total_fee
            )
            
            # Record stats
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
            
            logger.info("paper_order_filled", order_id=order_id, market=order.market_id, side=order.side, size=filled_size, price=vwap_price)
            return OrderAck(order_id=order_id, status="FILLED")
        else:
            self.stats.record_no_liquidity()
            logger.warning("paper_order_rejected_no_liquidity", order_id=order_id, market=order.market_id)
            return OrderAck(order_id=order_id, status="REJECTED")

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order."""
        return True
