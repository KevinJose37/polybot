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
        if self.fill_manager.check_and_mark(opportunity.opportunity_id):
            self.stats.record_dedup_rejection()
            return []
            
        logger.info(
            "live_exec", 
            opp_id=opportunity.opportunity_id[:8], 
            type=opportunity.type.value,
            edge=f"{opportunity.edge*100:.2f}%",
            size=f"${opportunity.size:.2f}",
        )
        
        # ── Atomic capacity reservation ──
        # Compute total notional for ALL legs and validate that the combined
        # exposure fits within limits BEFORE executing any leg.
        total_notional = sum(leg.size * leg.price for leg in opportunity.legs)
        if not self.risk_engine.reserve_exposure(total_notional):
            self.stats.record_risk_rejection()
            return []

        try:
            # 1. Validate against risk engine for all legs before ANY execution
            for leg in opportunity.legs:
                try:
                    if not self.risk_engine.validate_order(
                        leg.market_id, leg.size, price=leg.price, orderbooks=self.orderbooks, check_portfolio=False
                    ):
                        self.stats.record_risk_rejection()
                        return []
                except RiskKillSwitchTriggered:
                    self.stats.record_risk_rejection()
                    return []

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
                ack = await self.place_order(order, opp=opportunity, check_portfolio=False)
                leg_end = current_timestamp_ms()
                acks.append(ack)

                if ack.status == "FILLED":
                    # Use the actual fill size returned by place_order.
                    # place_order() now guards against zero fills, so filled_size
                    # should always be positive when status is FILLED.
                    actual_fill_size = ack.filled_size
                    if matched_size is None:
                        matched_size = actual_fill_size
                    
                    fee_rate = self.fee_rates.get(leg.market_id, self.settings.trading.polymarket_fee)
                    fee = polymarket_taker_fee(ack.fill_price or leg.price, actual_fill_size, fee_rate, side=str(leg.side))
                    fill_details.append({
                        "fill_price": ack.fill_price or leg.price,
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
                        self.risk_engine.activate_kill_switch(f"Unhedged leg imbalance on opp {opportunity.opportunity_id[:8]}. Leg {i} failed after {filled_legs} filled.")

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

    async def place_order(self, order: OrderRequest, opp: ArbOpportunity | None = None, check_portfolio: bool = True) -> OrderAck:
        """Sign and place a single order."""
        try:
            if not self.risk_engine.validate_order(
                order.market_id, order.size, price=order.price, orderbooks=self.orderbooks, check_portfolio=check_portfolio
            ):
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
            
            try:
                response = await self.api_client.place_order({
                    "order_id": order_id,
                    "market_id": order.market_id,
                    "side": order.side,
                    "price": order.price,
                    "size": order.size,
                    "signature": signature,
                })
            finally:
                # Always remove inflight regardless of API success/failure
                self.fill_manager.remove_inflight_order(order_id)
            
            # ── Determine fill status ──
            # Default to UNKNOWN (conservative) — never assume FILLED on missing key.
            # A missing status field likely means a malformed or error response.
            if "error" in response:
                status = "REJECTED"
                logger.warning("api_returned_error", error=response.get("error"), order_id=order_id)
            else:
                status = response.get("status", "UNKNOWN")
                if status not in ("FILLED", "MATCHED", "LIVE"):
                    logger.warning("unexpected_order_status", status=status, order_id=order_id)
                    status = "REJECTED"
            
            # Extract actual fill price and size from API response, falling back to request values
            fill_price = float(response.get("avg_price") or response.get("fill_price") or order.price)
            filled_size = float(response.get("filled_size") or response.get("size") or order.size)

            # Guard against zero-fill responses being recorded as fills
            if status == "FILLED" and filled_size <= 0:
                logger.warning("zero_fill_anomaly", order_id=order_id, raw_response=response)
                status = "REJECTED"
            
            if status == "FILLED":
                fee_rate = self.fee_rates.get(order.market_id, self.settings.trading.polymarket_fee)
                fee = polymarket_taker_fee(fill_price, filled_size, fee_rate, side=order.side)
                # Use actual fill price/size, not request values
                self.position_manager.add_fill(order.market_id, order.side, fill_price, filled_size, fee=fee)
                
                opp_type = opp.type.value if opp else ""
                opp_edge = opp.edge if opp else 0.0
                opp_id = opp.opportunity_id if opp else ""
                self.stats.record_fill(
                    market_id=order.market_id,
                    side=order.side,
                    price=fill_price,
                    size=filled_size,
                    fee=fee,
                    opp_type=opp_type,
                    opp_edge=opp_edge,
                    opp_id=opp_id,
                )

            return OrderAck(
                order_id=order_id,
                status=status,
                filled_size=filled_size if status == "FILLED" else 0.0,
                fill_price=fill_price if status == "FILLED" else 0.0,
            )

        except ConnectionError as e:
            # Network / connection issues — distinct from liquidity
            logger.error("live_order_network_error", error=str(e), market_id=order.market_id[:10])
            self.stats.record_risk_rejection()
            return OrderAck(order_id="failed", status="REJECTED", message=f"Network error: {e}")
        except ValueError as e:
            # Signer validation errors (bad token_id, price out of range, etc.)
            logger.error("live_order_signing_error", error=str(e), market_id=order.market_id[:10])
            self.stats.record_risk_rejection()
            return OrderAck(order_id="failed", status="REJECTED", message=f"Signing error: {e}")
        except Exception as e:
            logger.error("live_order_failed", error=str(e), market_id=order.market_id[:10])
            self.stats.record_risk_rejection()
            return OrderAck(order_id="failed", status="REJECTED", message=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order. Returns True on success, False on failure."""
        try:
            result = await self.api_client.cancel_order(order_id)
            self.fill_manager.remove_inflight_order(order_id)
            return result
        except Exception as e:
            logger.error("cancel_order_failed", order_id=order_id, error=str(e))
            self.fill_manager.remove_inflight_order(order_id)
            return False
