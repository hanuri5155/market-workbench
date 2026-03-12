## backend/app/db/models.py

from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import BigInteger, Integer, String, DateTime, Enum as SAEnum, Numeric, Boolean, ForeignKey, JSON
from datetime import datetime
import enum

Base = declarative_base()

class Strategy(enum.Enum):
    zone_strategy = "zone_strategy"
    manual = "manual"

class Side(enum.Enum):
    Long = "Long"
    Short = "Short"

class FillType(enum.Enum):
    ENTRY = "ENTRY"
    TP = "TP"
    SL = "SL"
    EXIT = "EXIT"
    FUNDING = "FUNDING"
    OTHER = "OTHER"

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    exchange: Mapped[str] = mapped_column(String(32), default="bybit")
    timezone: Mapped[str] = mapped_column(String(32), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime)

class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    mode: Mapped[str] = mapped_column(SAEnum("live", "simulation", name="mode"))
    config_snapshot: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    session_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sessions.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20))
    strategy: Mapped[Strategy] = mapped_column(SAEnum(Strategy, values_callable=lambda e: [i.value for i in e]))
    side: Mapped[Side] = mapped_column(SAEnum(Side, values_callable=lambda e: [i.value for i in e]))
    order_link_id: Mapped[str | None] = mapped_column(String(100))
    parent_order_link_id: Mapped[str | None] = mapped_column(String(100))
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    exit_price_last: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    entry_price: Mapped[float] = mapped_column(Numeric(18, 6))
    entry_qty: Mapped[float] = mapped_column(Numeric(20, 8))
    entry_value: Mapped[float] = mapped_column(Numeric(20, 8))
    leverage: Mapped[float | None] = mapped_column(Numeric(10, 2))
    sl_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    tp_partition: Mapped[int | None] = mapped_column(Integer)
    fee_open: Mapped[float | None] = mapped_column(Numeric(20, 8), default=0)
    fee_close: Mapped[float | None] = mapped_column(Numeric(20, 8), default=0)
    fee_total: Mapped[float | None] = mapped_column(Numeric(20, 8), default=0)
    pnl_gross: Mapped[float | None] = mapped_column(Numeric(20, 8))
    pnl_net: Mapped[float | None] = mapped_column(Numeric(20, 8))
    duration_sec: Mapped[int | None] = mapped_column(Integer)

class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("positions.id"))
    fill_time: Mapped[datetime] = mapped_column(DateTime)
    price: Mapped[float] = mapped_column(Numeric(18, 6))
    qty: Mapped[float] = mapped_column(Numeric(20, 8))
    pnl_gross: Mapped[float] = mapped_column(Numeric(20, 8))
    fee: Mapped[float] = mapped_column(Numeric(20, 8))
    fill_type: Mapped[FillType] = mapped_column(SAEnum(FillType, values_callable=lambda e: [i.value for i in e]))
    stage_code: Mapped[int | None] = mapped_column(Integer)

# Structure Zone 원본+상태 통합 테이블
#
# zone_state 하나로 원본과 상태를 함께 관리
# end_time != NULL 이면 broken 상태로 판단
class ZoneState(Base):

    __tablename__ = "zone_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval_min: Mapped[int] = mapped_column(Integer, nullable=False)

    # DB datetime(3) 과 대응 (tz는 UTC naive로 통일)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # "LONG" / "SHORT"
    side: Mapped[str] = mapped_column(String(8), nullable=False)

    # 박스 원본(base)
    base_entry: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    base_sl: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    base_upper: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    base_lower: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)

    # 사용자 상태(state)
    entry_override: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# 전역 전략 온오프 플래그 저장용
class StrategyFlag(Base):
    __tablename__ = "strategy_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    bool_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
