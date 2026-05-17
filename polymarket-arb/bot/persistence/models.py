"""
SQLAlchemy 2.0 ORM models.
"""
from datetime import datetime, timezone
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer, DateTime

class Base(DeclarativeBase):
    pass

class TradeRecord(Base):
    """
    Records paper and live trades.
    """
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    market_id: Mapped[str] = mapped_column(String(128))
    side: Mapped[str] = mapped_column(String(10))
    price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(20)) # "paper" or "live"
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

