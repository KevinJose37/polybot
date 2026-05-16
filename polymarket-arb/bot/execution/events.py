"""
Market event handling and persistence.
"""
import asyncio
import structlog
from typing import Any

from bot.orderbook.local_book import LocalOrderBook
from bot.orderbook.book_state import BookState
from bot.api.schemas import OrderBookSnapshot
from bot.arbitrage.scanner import ArbitrageScanner
from bot.execution.executor import ExecutorProtocol
from bot.paper_trading.stats import TradingStats
from bot.persistence.postgres import DatabaseManager
from bot.persistence.repositories import TradeRepository

logger = structlog.get_logger(__name__)


class MarketEventHandler:
    def __init__(
        self,
        orderbooks: dict[str, LocalOrderBook],
        scanner: ArbitrageScanner,
        executor: ExecutorProtocol,
        stats: TradingStats,
        recent_opportunities: list[dict],
        db: DatabaseManager,
        mode: str
    ):
        self.orderbooks = orderbooks
        self.scanner = scanner
        self.executor = executor
        self.stats = stats
        self.recent_opportunities = recent_opportunities
        self.db = db
        self.mode = mode
        self.persistence_queue: asyncio.Queue[tuple[Any, list, dict]] = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._persistence_worker())

    async def _persistence_worker(self) -> None:
        """Background worker to save trades to the database without blocking the WS loop."""
        while True:
            try:
                opp, acks, opp_data = await self.persistence_queue.get()
                try:
                    async for session in self.db.get_session():
                        repo = TradeRepository(session)
                        for leg, ack in zip(opp.legs, acks):
                            if ack.status == "FILLED":
                                await repo.add_trade(
                                    opp_id=opp.opportunity_id,
                                    order_id=ack.order_id,
                                    market_id=leg.market_id,
                                    side=leg.side,
                                    price=leg.price,
                                    size=leg.size,
                                    mode=self.mode
                                )
                        break
                except Exception as e:
                    logger.error("persistence_error", error=str(e))
                finally:
                    self.persistence_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("persistence_worker_error", error=str(e))

    async def handle_message(self, data: dict | list) -> None:
        """Handle incoming WebSocket messages."""
        if isinstance(data, list):
            messages = data
        elif isinstance(data, dict):
            messages = [data]
        else:
            return

        for msg in messages:
            event_type = msg.get("event_type", "")

            if event_type == "book":
                token_id = msg.get("asset_id")
                if not token_id:
                    continue
                book = self.orderbooks.get(token_id)
                if not book:
                    continue

                bids = [(float(b["price"]), float(b["size"])) for b in msg.get("bids", [])]
                asks = [(float(a["price"]), float(a["size"])) for a in msg.get("asks", [])]
                snapshot = OrderBookSnapshot(market_id=token_id, bids=bids, asks=asks)
                await book.apply_snapshot(snapshot)

            elif event_type == "price_change":
                for change in msg.get("price_changes", []):
                    token_id = change.get("asset_id")
                    if not token_id:
                        continue
                    book = self.orderbooks.get(token_id)
                    if not book:
                        continue

                    price = float(change["price"])
                    size = float(change["size"])
                    side = change.get("side", "").upper()

                    if side == "BUY":
                        bids = [(price, size)]
                        asks = []
                    elif side == "SELL":
                        bids = []
                        asks = [(price, size)]
                    else:
                        continue

                    ts = int(msg.get("timestamp", 0)) or 0
                    if book.state == BookState.PENDING:
                        continue
                        
                    try:
                        await book.apply_delta(bids, asks, sequence=ts)
                    except Exception as e:
                        logger.error("ws_apply_delta_error", error=str(e), market=token_id)
                        book.state = BookState.STALE

            elif event_type in ("last_trade_price", "best_bid_ask", "tick_size_change"):
                pass
            else:
                logger.debug("ws_unknown_event", event_type=event_type, keys=list(msg.keys()))

        # Run Scanner
        opportunities = self.scanner.scan(self.orderbooks)

        async def execute_and_persist(opp, opp_data):
            acks = await self.executor.execute_opportunity(opp)
            if not acks:
                opp_data["status"] = "REJECTED"
                opp_data["color"] = "red"
            else:
                all_filled = all(ack.status == "FILLED" for ack in acks)
                any_rejected = any(ack.status == "REJECTED" for ack in acks)
                if all_filled:
                    opp_data["status"] = "FILLED"
                    opp_data["color"] = "green"
                elif any_rejected:
                    opp_data["status"] = "LEG IMBALANCE"
                    opp_data["color"] = "red"
                else:
                    opp_data["status"] = "PARTIAL"
                    opp_data["color"] = "yellow"

                # Offload persistence to background queue
                self.persistence_queue.put_nowait((opp, acks, opp_data))

        for opp in opportunities:
            self.stats.record_opportunity_detected()
            opp_data = {
                "type": opp.type.value,
                "edge": opp.edge,
                "size": opp.size,
                "status": "PENDING",
                "color": "yellow"
            }
            self.recent_opportunities.append(opp_data)
            if len(self.recent_opportunities) > 8:
                self.recent_opportunities.pop(0)

            asyncio.create_task(execute_and_persist(opp, opp_data))
            
    async def shutdown(self):
        if self._worker_task:
            self._worker_task.cancel()
