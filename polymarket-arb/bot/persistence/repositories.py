"""
Repository pattern for database access.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from bot.persistence.models import TradeRecord

class TradeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_trade(self, opp_id: str, order_id: str, market_id: str, side: str, price: float, size: float, mode: str) -> TradeRecord:
        trade = TradeRecord(
            opportunity_id=opp_id,
            order_id=order_id,
            market_id=market_id,
            side=side,
            price=price,
            size=size,
            mode=mode
        )
        self.session.add(trade)
        await self.session.commit()
        return trade
