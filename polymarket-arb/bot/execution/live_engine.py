"""
Live execution engine for order signing and placement.
"""
import structlog
import asyncio

from bot.execution.executor import ExecutorProtocol
from bot.risk.engine import RiskEngine, RiskKillSwitchTriggered
from bot.execution.fill_manager import FillManager
from bot.execution.position_manager import PositionManager
from bot.api.polymarket import PolymarketRESTClient
from bot.api.schemas import OrderRequest, OrderAck
from bot.api.signer import sign_order
from bot.arbitrage.opportunity import ArbOpportunity
from bot.paper_trading.stats import TradingStats
from bot.utils.math import polymarket_taker_fee
from bot.settings import Settings

logger = structlog.get_logger(__name__)

class LiveExecutor(ExecutorProtocol):
    """
    Live executor matching ExecutorProtocol.
    Uses real credentials to sign and place orders via the Polymarket CLOB API.
    """
    def __init__(
        self, 
        settings: Settings, 
        risk_engine: RiskEngine, 
        fill_manager: FillManager,
        api_client: PolymarketRESTClient,
        orderbooks: dict | None = None,
        position_manager: PositionManager | None = None,
        stats: TradingStats | None = None,
    ):
        self.settings = settings
        self.risk_engine = risk_engine
        self.fill_manager = fill_manager
        self.api_client = api_client
        self.orderbooks = orderbooks or {}
        self.position_manager = position_manager or PositionManager()
        self.stats = stats or TradingStats()

    async def execute_opportunity(self, opportunity: ArbOpportunity) -> list[OrderAck]:
        """Execute a full arbitrage opportunity."""
        if self.fill_manager.is_duplicate(opportunity.opportunity_id):
            self.stats.record_dedup_rejection()
            return []
            
        logger.warning(
            "live_executing_opportunity", 
            opp_id=opportunity.opportunity_id, 
            type=opportunity.type.value,
            edge=f"{opportunity.edge*100:.2f}%",
            size=opportunity.size
        )
        
        # 1. Validate against risk engine for all legs before ANY execution
        for leg in opportunity.legs:
            try:
                if not self.risk_engine.validate_order(leg.market_id, leg.size, orderbooks=self.orderbooks):
                    logger.warning("risk_rejected_order", market_id=leg.market_id, size=leg.size)
                    self.stats.record_risk_rejection()
                    return []
            except RiskKillSwitchTriggered as e:
                logger.critical("execution_halted", reason=str(e))
                self.stats.record_risk_rejection()
                return []
                
        self.fill_manager.mark_executed(opportunity.opportunity_id)
        self.stats.record_opportunity_executed()
        
        acks = []
        filled_legs = []
        # In a real FOK arb, we would ideally batch these or execute concurrently
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
        """Sign and place a single order."""
        try:
            if not self.risk_engine.validate_order(order.market_id, order.size, orderbooks=self.orderbooks):
                logger.warning("risk_rejected_order", market_id=order.market_id, size=order.size)
                return OrderAck(order_id="failed", status="REJECTED", message="Risk Engine Rejected")
        except RiskKillSwitchTriggered as e:
            logger.critical("execution_halted", reason=str(e))
            return OrderAck(order_id="failed", status="REJECTED", message=str(e))

        try:
            signature = sign_order(
                private_key=self.settings.api.private_key.get_secret_value(),
                exchange_address=self.settings.network.exchange_address,
                chain_id=self.settings.network.chain_id,
                maker=self.settings.api.host_address,
                signer=self.settings.api.host_address,
                token_id=order.market_id,
                side=order.side,
                size=str(order.size),
                price=str(order.price)
            )
            
            order_id = "live_" + signature[:8]
            
            # Submit to Polymarket CLOB API
            logger.info("live_order_signed", market_id=order.market_id, side=order.side, price=order.price, size=order.size)
            self.fill_manager.add_inflight_order(order_id, {"market": order.market_id, "size": order.size, "side": order.side})
            
            response = await self.api_client.place_order({
                "order_id": order_id,
                "market_id": order.market_id,
                "side": order.side,
                "price": order.price,
                "size": order.size,
                "signature": signature,
            })
            
            status = response.get("status", "FILLED")
            self.fill_manager.remove_inflight_order(order_id)
            
            if status == "FILLED":
                # Track position for exposure/drawdown calculations
                fee = polymarket_taker_fee(order.price, order.size, self.settings.trading.polymarket_fee)
                self.position_manager.add_fill(order.market_id, order.side, order.price, order.size, fee=fee)
                
                opp_type = opp.type.value if opp else ""
                opp_edge = opp.edge if opp else 0.0
                self.stats.record_fill(
                    market_id=order.market_id,
                    side=order.side,
                    price=order.price,
                    size=order.size,
                    fee=fee,
                    opp_type=opp_type,
                    opp_edge=opp_edge,
                )

            return OrderAck(order_id=order_id, status=status)
            
        except Exception as e:
            logger.error("live_order_failed", error=str(e), market_id=order.market_id)
            self.stats.record_no_liquidity()
            return OrderAck(order_id="failed", status="REJECTED", message=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order."""
        try:
            await self.api_client.cancel_order(order_id)
        except Exception as e:
            logger.error("cancel_order_failed", order_id=order_id, error=str(e))
        self.fill_manager.remove_inflight_order(order_id)
        return True
