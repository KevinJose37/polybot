"""Live execution engine for order signing and placement."""
import structlog
import asyncio

from bot.execution.executor import ExecutorProtocol
from bot.risk.engine import RiskEngine, RiskKillSwitchTriggered
from bot.execution.fill_manager import FillManager
from bot.execution.position_manager import PositionManager
from bot.api.polymarket import PolymarketRESTClient
from bot.api.schemas import OrderRequest, OrderAck
from bot.api.signer import sign_order
from bot.arbitrage.opportunity import ArbOpportunity, ArbType
from bot.paper_trading.stats import TradingStats
from bot.utils.math import polymarket_taker_fee
from bot.monitoring.forensic import ForensicLogger
from bot.settings import Settings
from bot.utils.clocks import current_timestamp_ms

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
        fee_rates: dict[str, float] | None = None,
        forensic: ForensicLogger | None = None,
    ):
        self.settings = settings
        self.risk_engine = risk_engine
        self.fill_manager = fill_manager
        self.api_client = api_client
        self.orderbooks = orderbooks or {}
        self.position_manager = position_manager or PositionManager()
        self.stats = stats or TradingStats()
        self.fee_rates = fee_rates or {}
        self.forensic = forensic

    async def execute_opportunity(self, opportunity: ArbOpportunity) -> list[OrderAck]:
        """Execute a full arbitrage opportunity."""
        if self.fill_manager.is_duplicate(opportunity.opportunity_id):
            self.stats.record_dedup_rejection()
            if self.forensic:
                self.forensic.log_skipped_opportunity(
                    opp=opportunity, reason="dedup_window",
                    details="Opportunity already executed within dedup window",
                    filters_passed=[], filters_failed=["dedup"],
                    orderbooks=self.orderbooks,
                )
            return []
            
        logger.info(
            "live_exec", 
            opp_id=opportunity.opportunity_id[:8], 
            type=opportunity.type.value,
            edge=f"{opportunity.edge*100:.2f}%",
            size=f"${opportunity.size:.2f}",
        )
        
        # 1. Validate against risk engine for all legs before ANY execution
        for leg in opportunity.legs:
            try:
                if not self.risk_engine.validate_order(leg.market_id, leg.size, orderbooks=self.orderbooks):
                    self.stats.record_risk_rejection()
                    if self.forensic:
                        self.forensic.log_skipped_opportunity(
                            opp=opportunity, reason="risk_rejected",
                            details=f"Risk engine rejected leg {leg.market_id}",
                            filters_passed=["dedup", "min_edge"],
                            filters_failed=["risk_engine"],
                            orderbooks=self.orderbooks,
                        )
                    return []
            except RiskKillSwitchTriggered as e:
                self.stats.record_risk_rejection()
                if self.forensic:
                    self.forensic.log_skipped_opportunity(
                        opp=opportunity, reason="kill_switch",
                        details=str(e),
                        filters_passed=[], filters_failed=["kill_switch"],
                        orderbooks=self.orderbooks,
                    )
                return []
                
        self.fill_manager.mark_executed(opportunity.opportunity_id)
        self.stats.record_opportunity_executed()
        
        acks = []
        filled_legs = []
        fill_details = []
        matched_size: float | None = None
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
            ack = await self.place_order(order, opp=opportunity)
            leg_end = current_timestamp_ms()
            acks.append(ack)

            if ack.status == "FILLED":
                if matched_size is None:
                    pos = self.position_manager.get_position(leg.market_id)
                    matched_size = abs(pos.size)
                
                fee_rate = self.fee_rates.get(leg.market_id, self.settings.trading.polymarket_fee)
                fee = polymarket_taker_fee(leg.price, leg_size, fee_rate, side=str(leg.side))
                fill_details.append({
                    "fill_price": leg.price,
                    "filled_size": leg_size,
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

    async def place_order(self, order: OrderRequest, opp: ArbOpportunity | None = None) -> OrderAck:
        """Sign and place a single order."""
        try:
            if not self.risk_engine.validate_order(order.market_id, order.size, orderbooks=self.orderbooks):
                return OrderAck(order_id="failed", status="REJECTED", message="Risk Engine Rejected")
        except RiskKillSwitchTriggered as e:
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
            
            logger.info("live_order_signed", market_id=order.market_id[:10], side=order.side, price=order.price, size=order.size)
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
                fee_rate = self.fee_rates.get(order.market_id, self.settings.trading.polymarket_fee)
                fee = polymarket_taker_fee(order.price, order.size, fee_rate, side=order.side)
                self.position_manager.add_fill(order.market_id, order.side, order.price, order.size, fee=fee)
                
                opp_type = opp.type.value if opp else ""
                opp_edge = opp.edge if opp else 0.0
                opp_id = opp.opportunity_id if opp else ""
                self.stats.record_fill(
                    market_id=order.market_id,
                    side=order.side,
                    price=order.price,
                    size=order.size,
                    fee=fee,
                    opp_type=opp_type,
                    opp_edge=opp_edge,
                    opp_id=opp_id,
                )

            return OrderAck(order_id=order_id, status=status)
            
        except Exception as e:
            logger.error("live_order_failed", error=str(e), market_id=order.market_id[:10])
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
