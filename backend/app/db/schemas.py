## backend/app/db/schemas.py

from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class Summary(BaseModel):
    equity: float
    pnl24h: float
    positionsOpen: int

class PositionOut(BaseModel):
    id: int
    symbol: str
    side: str
    qty: float
    entryPrice: float
    currentPrice: float
    pnl: float
    updatedAt: datetime

    class Config:
        from_attributes = True

class EquityPoint(BaseModel):
    t: datetime
    equity: float

    class Config:
        from_attributes = True


class ZoneStateBase(BaseModel):
    symbol: str
    intervalMin: int
    startTime: datetime
    side: str          # "LONG" / "SHORT"
    isActive: bool
    entryOverride: float | None = None  # 사용자가 직접 덮어쓴 진입가

class ZoneBase(BaseModel):
    symbol: str
    intervalMin: int
    startTime: datetime
    endTime: datetime | None = None
    side: str                # "LONG" / "SHORT"
    baseEntry: float
    baseSl: float
    baseUpper: float
    baseLower: float
    isBroken: bool = False

class ZoneOut(BaseModel):
    # 프론트 차트가 사용하는 Structure Zone payload 형태
    id: str
    symbol: str
    intervalMin: int
    tf: str                 # "15" | "30" | "60" | "240"
    side: str               # "LONG" | "SHORT"

    startTs: int            # ms epoch
    endTs: int | None = None

    entry: float
    sl: float
    upper: float
    lower: float

    isBroken: bool
    isActive: bool

    # 원본 값. override 해제나 비교 기준으로 사용
    baseEntry: float
    baseSl: float
    baseUpper: float
    baseLower: float

    # 현재 적용 중인 override 값
    entryOverride: float | None = None


class ZoneStateOut(ZoneStateBase):
    class Config:
        from_attributes = True


# OTP 인증
class OTPVerifyRequest(BaseModel):
    code: str  # 6자리 숫자 문자열


class OTPVerifyResponse(BaseModel):
    ok: bool = True


class OTPStatusResponse(BaseModel):
    ok: bool = True


# 전략 온오프 응답
# /api/strategy_flags 전체 조회용 응답
class StrategyFlagResponse(BaseModel):
    enable_trading: bool
    enable_zone_strategy: bool


# 특정 설정 토글용 요청 바디
class StrategyFlagToggleRequest(BaseModel):
    value: bool


class StrategyFlagToggleResponse(BaseModel):
    key: str
    value: bool


# 차트 포지션 오버레이
# 차트 포지션 오버레이(Entry/SL/TP, 리스크/리워드 존)용 최소 데이터
class PositionOverlayOut(BaseModel):
    id: str
    symbol: str
    strategy: str | None = None
    side: str                 # "LONG" | "SHORT"

    entryTs: int              # epoch ms (UTC)
    entryPrice: float

    # Structure Zone: 윅 SL 발동 전에는 slAvailable=False, slPrice=None
    slAvailable: bool = False
    slPrice: float | None = None

    # TP 미설정이면 tpAvailable=False, tpPrice=None
    tpAvailable: bool = False
    tpPrice: float | None = None
    closed: bool = False
    exitTs: int | None = None


# 워처/전략 → FastAPI 내부 통지용 payload
class PositionOverlayEventIn(BaseModel):
    action: str               # "update" | "clear"
    overlay: PositionOverlayOut | None = None
    id: str | None = None
    exitTs: int | None = None


class PositionTpslModifyRequest(BaseModel):
    field: Literal["tp", "sl"]
    price: float


class PositionTpslModifyResponse(BaseModel):
    ok: bool = True
    overlay: PositionOverlayOut
    field: Literal["tp", "sl"]
    requestedPrice: float
    appliedPrice: float
    tickSize: float
