# 외부 공개 API 라우터
#
# 브라우저 화면에서 zone 오버레이와 포지션 오버레이를 조회하고 조작하는 경로 모음
# - OTP 인증
# - strategy flag 조회/변경
# - 차트 캔들 조회
# - Zone 상태 저장
# - 포지션 오버레이 수정

import asyncio
import math
import os
from datetime import datetime, timezone
from typing import List

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.services.bybit_position_tpsl import (
    BybitApiError,
    get_linear_last_price,
    get_linear_tick_size,
    get_open_linear_position,
    list_open_linear_positions,
    normalize_position_side,
    round_price_to_tick,
    update_linear_position_tpsl,
)
from app.api.services.position_overlay_snapshot import (
    build_position_overlay_snapshot,
    make_position_overlay_id,
    normalize_side_upper,
)
from app.api.ws.control import broadcast_control_event
from app.api.ws.position_overlay import (
    clear_overlay_and_broadcast,
    get_overlay_snapshot,
    patch_overlay_and_broadcast,
    upsert_overlay_and_broadcast,
)
from app.api.ws.zone_state import broadcast_zone_state
from app.auth.otp.attempts import is_blocked, register_failure, reset_attempts
from app.auth.otp.sessions import create_session, validate_session
from app.db import crud, models, schemas
from app.db.session import SessionLocal
from core.config.config_utils import get_strategy_flags_from_db
from core.persistence.candles_repo import (
    fetch_candles_for_chart,
    fetch_latest_candle_for_chart,
)
from core.utils.log_utils import log

# 화면 전반이 공통으로 쓰는 기본 심볼
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
router = APIRouter(prefix="/api", tags=["api"])
GOOGLE_OTP_SECRET = os.getenv("GOOGLE_OTP_SECRET", "")

# 브라우저 쿠키는 오래 유지하되 실제 인증 유효성은 서버 TTL이 결정
COOKIE_MAX_AGE_DAYS = 365
COOKIE_MAX_AGE_SECONDS = COOKIE_MAX_AGE_DAYS * 24 * 60 * 60

# 현재 라우터 파일 기준 backend/config/config.json 위치
CONFIG_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config",
    "config.json",
)

STRATEGYFLAG_KEYS = [
    "enable_trading",
    "enable_zone_strategy",
]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 포지션 오버레이 복구 시 side별 최신 미청산 포지션 한 건만 고르기 위함
def _pick_latest_open_position_by_side(
    db: Session,
    symbol: str,
) -> dict[str, models.Position]:
    rows = (
        db.query(models.Position)
        .filter(
            models.Position.symbol == symbol,
            models.Position.closed.is_(False),
        )
        .order_by(models.Position.entry_time.desc())
        .all()
    )

    out: dict[str, models.Position] = {}
    for row in rows:
        side_raw = row.side.value if hasattr(row.side, "value") else row.side
        side_upper = normalize_side_upper(side_raw)
        if side_upper and side_upper not in out:
            out[side_upper] = row
    return out

@router.post("/auth/otp/verify", response_model=schemas.OTPVerifyResponse)
def verify_otp(
    payload: schemas.OTPVerifyRequest,
    response: Response,
    request: Request,
):
    if not GOOGLE_OTP_SECRET:
        raise HTTPException(status_code=500, detail="OTP_NOT_CONFIGURED")

    # 시도 주체를 IP 단위로 식별해 OTP brute-force를 제한하기 위함
    client_ip = (
        request.headers.get("x-real-ip")
        or (request.client.host if request.client else "unknown")
    )

    blocked, retry_after = is_blocked(client_ip)
    if blocked:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "OTP_TOO_MANY_ATTEMPTS",
                "retry_after_seconds": retry_after,
            },
        )

    totp = pyotp.TOTP(GOOGLE_OTP_SECRET)

    # 전후 30초 정도는 허용해 기기 시간 차이와 네트워크 지연을 흡수
    if not totp.verify(payload.code, valid_window=1):
        register_failure(client_ip)

        blocked, retry_after = is_blocked(client_ip)
        if blocked:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "OTP_TOO_MANY_ATTEMPTS",
                    "retry_after_seconds": retry_after,
                },
            )

        raise HTTPException(status_code=401, detail="INVALID_OTP_CODE")

    reset_attempts(client_ip)

    session_id = create_session()

    # 브라우저는 쿠키만 들고 다니고 실제 유효성 판단은 서버가 맡음
    response.set_cookie(
        key="otp_session",
        value=session_id,
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=COOKIE_MAX_AGE_SECONDS,
    )

    return schemas.OTPVerifyResponse(ok=True)


@router.get("/auth/otp/status", response_model=schemas.OTPStatusResponse)
def otp_status(request: Request):
    # 페이지 새로고침 뒤에도 인증 상태를 바로 확인하기 위함
    session_id = request.cookies.get("otp_session")
    if not session_id or not validate_session(session_id):
        raise HTTPException(status_code=401, detail="OTP_REQUIRED")

    return schemas.OTPStatusResponse(ok=True)

@router.get("/strategy_flags", response_model=schemas.StrategyFlagResponse)
def get_strategy_flags(db: Session = Depends(get_db)):
    db_map = crud.get_strategy_flags_map(db, STRATEGYFLAG_KEYS)

    merged = {k: bool(db_map.get(k, False)) for k in STRATEGYFLAG_KEYS}

    return schemas.StrategyFlagResponse(
        enable_trading=merged["enable_trading"],
        enable_zone_strategy=merged["enable_zone_strategy"],
    )


@router.post("/strategy_flags/enable_trading", response_model=schemas.StrategyFlagToggleResponse)
async def set_enable_trading(
    payload: schemas.StrategyFlagToggleRequest,
    db: Session = Depends(get_db),
):
    key = "enable_trading"
    value = bool(payload.value)

    crud.set_strategy_flag_bool(db, key, value)

    flags = get_strategy_flags_from_db()
    all_on = bool(flags.get("enable_trading", False))
    zone_on = all_on and bool(flags.get("enable_zone_strategy", False))

    status_all = "🟢 전체 주문" if all_on else "🔴 전체 주문"
    status_zone = "🟢 Structure Zone" if zone_on else "🔴 Structure Zone"
    status_line = f"{status_all} | {status_zone}"

    log(status_line)

    await broadcast_control_event({
        "type": "strategy_flags_updated",
        "changed": {"key": key, "value": value},
        "flags": flags,
    })

    return schemas.StrategyFlagToggleResponse(key=key, value=value)

@router.post("/strategy_flags/enable_zone_strategy", response_model=schemas.StrategyFlagToggleResponse)
async def set_enable_zone_strategy(
    payload: schemas.StrategyFlagToggleRequest,
    db: Session = Depends(get_db),
):
    key = "enable_zone_strategy"
    value = bool(payload.value)

    crud.set_strategy_flag_bool(db, key, value)

    flags = get_strategy_flags_from_db()
    all_on = bool(flags.get("enable_trading", False))
    zone_on = all_on and bool(flags.get("enable_zone_strategy", False))

    status_all = "🟢 전체 주문" if all_on else "🔴 전체 주문"
    status_zone = "🟢 Structure Zone" if zone_on else "🔴 Structure Zone"
    log(f"{status_all} | {status_zone}")

    await broadcast_control_event({
        "type": "strategy_flags_updated",
        "changed": {"key": "enable_zone_strategy", "value": value},
        "flags": flags,
    })

    return schemas.StrategyFlagToggleResponse(key="enable_zone_strategy", value=value)



@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/summary", response_model=schemas.Summary)
def summary(db: Session = Depends(get_db)):
    return crud.get_summary(db)

@router.get("/positions", response_model=List[schemas.PositionOut])
def positions(db: Session = Depends(get_db)):
    data = crud.list_positions(db)
    out = []
    for p in data:
        side = p.side.value if hasattr(p.side, "value") else p.side
        side_upper = "LONG" if side.lower() == "long" else "SHORT"
        out.append(schemas.PositionOut(
            id=int(p.id),
            symbol=p.symbol,
            side=side_upper,
            qty=float(p.entry_qty),
            entryPrice=float(p.entry_price),
            currentPrice=float(p.exit_price_last or p.entry_price),
            pnl=float(p.pnl_net or 0),
            updatedAt=p.exit_time or p.entry_time
        ))
    return out


# 차트 진입/WS 재연결 시점의 canonical 복구 경로
# - Bybit 조회 성공 시: 거래소의 현재 오픈 포지션만 기준으로 오버레이를 재구성
# - execution_data_store/DB는 전략, entryTs, 내부 SL 같은 메타데이터 보강 용도로만 사용
# - Bybit 조회 실패 시에만 execution_data_store/DB 기준으로 보수 폴백
@router.get("/position-overlays/snapshot", response_model=List[schemas.PositionOverlayOut])
async def position_overlay_snapshot(
    symbol: str = Query(SYMBOL),
    db: Session = Depends(get_db),
):
    symbol_upper = str(symbol or SYMBOL).upper()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    latest_open_by_side = _pick_latest_open_position_by_side(db, symbol_upper)

    bybit_rows: list[dict] = []
    bybit_ok = False

    try:
        bybit_rows = await asyncio.to_thread(list_open_linear_positions, symbol_upper)
        bybit_ok = True
    except BybitApiError as e:
        log(f"⚠️ [PositionOverlay] snapshot Bybit 조회 실패(ret): {e.ret_msg or str(e)}")
    except Exception as e:
        log(f"⚠️ [PositionOverlay] snapshot Bybit 조회 실패: {e}")

    overlays = build_position_overlay_snapshot(
        symbol=symbol_upper,
        bybit_rows=bybit_rows,
        bybit_ok=bybit_ok,
        latest_open_db_by_side=latest_open_by_side,
        now_ms=now_ms,
    )
    overlays_by_id = {str(overlay.get("id")): overlay for overlay in overlays if overlay.get("id")}

    # 차트 WS 스냅샷과 TP/SL 수정 API가 같은 상태를 보도록 메모리 오버레이도 갱신
    for overlay in overlays:
        try:
            await upsert_overlay_and_broadcast(dict(overlay))
        except Exception as e:
            log(f"⚠️ [PositionOverlay] snapshot upsert 실패: {e}")

    for side_upper in ("LONG", "SHORT"):
        target_id = make_position_overlay_id(symbol_upper, side_upper)
        if target_id in overlays_by_id:
            continue
        try:
            await clear_overlay_and_broadcast(target_id)
        except Exception as e:
            log(f"⚠️ [PositionOverlay] snapshot clear 실패: {e}")

    return [schemas.PositionOverlayOut(**o) for o in overlays]


@router.post(
    "/positions/{overlay_id}/tpsl",
    response_model=schemas.PositionTpslModifyResponse,
)
async def modify_position_tpsl(
    overlay_id: str,
    payload: schemas.PositionTpslModifyRequest,
):
    overlay = await get_overlay_snapshot(overlay_id)
    if not overlay:
        raise HTTPException(status_code=404, detail="position overlay not found")

    if bool(overlay.get("closed")):
        raise HTTPException(status_code=409, detail="position already closed")

    symbol = str(overlay.get("symbol") or SYMBOL)
    try:
        side = normalize_position_side(str(overlay.get("side") or ""))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid overlay side")

    requested_price = float(payload.price)
    if not math.isfinite(requested_price) or requested_price <= 0:
        raise HTTPException(status_code=422, detail="price must be a positive number")

    try:
        tick_size = await asyncio.to_thread(get_linear_tick_size, symbol)
        applied_price, applied_price_text = round_price_to_tick(
            requested_price,
            tick_size,
        )
    except BybitApiError as e:
        status = 400 if e.ret_code is not None else 502
        raise HTTPException(status_code=status, detail=e.ret_msg or str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        position = await asyncio.to_thread(get_open_linear_position, symbol, side)
    except BybitApiError as e:
        status = 400 if e.ret_code is not None else 502
        raise HTTPException(status_code=status, detail=e.ret_msg or str(e))

    if not position:
        raise HTTPException(status_code=409, detail="open position not found")

    ref_price = None
    for k in ("markPrice", "lastPrice"):
        try:
            raw = position.get(k)
            if raw is None:
                continue
            v = float(raw)
            if math.isfinite(v) and v > 0:
                ref_price = v
                break
        except Exception:
            continue
    if ref_price is None:
        try:
            ref_price = await asyncio.to_thread(get_linear_last_price, symbol)
        except BybitApiError:
            ref_price = None

    # LONG: TP > 현재가, SL < 현재가 / SHORT: TP < 현재가, SL > 현재가
    if ref_price is not None and math.isfinite(ref_price):
        if payload.field == "tp":
            if side == "LONG" and applied_price <= ref_price:
                raise HTTPException(
                    status_code=422,
                    detail=f"TP must be above current price ({ref_price}) for LONG",
                )
            if side == "SHORT" and applied_price >= ref_price:
                raise HTTPException(
                    status_code=422,
                    detail=f"TP must be below current price ({ref_price}) for SHORT",
                )
        else:
            if side == "LONG" and applied_price >= ref_price:
                raise HTTPException(
                    status_code=422,
                    detail=f"SL must be below current price ({ref_price}) for LONG",
                )
            if side == "SHORT" and applied_price <= ref_price:
                raise HTTPException(
                    status_code=422,
                    detail=f"SL must be above current price ({ref_price}) for SHORT",
                )

    try:
        await asyncio.to_thread(
            update_linear_position_tpsl,
            symbol=symbol,
            side=side,
            tp_price_text=(applied_price_text if payload.field == "tp" else None),
            sl_price_text=(applied_price_text if payload.field == "sl" else None),
        )
    except BybitApiError as e:
        status = 400 if e.ret_code is not None else 502
        raise HTTPException(status_code=status, detail=e.ret_msg or str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    patch = (
        {"tpPrice": applied_price, "tpAvailable": True}
        if payload.field == "tp"
        else {"slPrice": applied_price, "slAvailable": True}
    )

    updated_overlay = await patch_overlay_and_broadcast(str(overlay_id), patch)
    if not updated_overlay:
        updated_overlay = {**overlay, **patch, "id": str(overlay_id)}

    return schemas.PositionTpslModifyResponse(
        ok=True,
        overlay=schemas.PositionOverlayOut(**updated_overlay),
        field=payload.field,
        requestedPrice=requested_price,
        appliedPrice=applied_price,
        tickSize=tick_size,
    )

@router.get("/metrics/equity", response_model=List[schemas.EquityPoint])
def equity_metrics(
    from_: datetime | None = Query(None, alias="from"),
    to_: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db)
):
    return crud.list_equity(db, start=from_, end=to_)


# ==========================
# Candles endpoints (DB 기반)
# ==========================
# tf: '15'|'30'|'60'|'240'|'1440'
#
# limit:
#   - 지정하면 해당 개수만 (최신부터) 반환
#   - 지정하지 않으면(None) 해당 타임프레임 전체 캔들을 반환
#
# before:
#   - ms 단위 epoch. 이 값보다 이전(start_time < before) 캔들만 조회
#   - 첫 페이지(최신 구간)는 before 없이 호출
@router.get("/candles/{tf}")
def get_candles(
    tf: str,
    limit: int = Query(300, ge=1, le=5000),
    before: int | None = Query(
        None,
        description="이 epoch ms 이전의 캔들만 조회(start_time < before)",
    ),
):
    if tf not in {"15", "30", "60", "240", "1440"}:
        raise HTTPException(status_code=400, detail="unsupported timeframe")

    interval_min = int(tf)

    candles = fetch_candles_for_chart(
        symbol=SYMBOL,
        interval_min=interval_min,
        limit=limit,           # None이면 전체
        before_ms=before,
    )
    return candles


# 반환: 최신 1개 캔들(dict). 없으면 {}
@router.get("/candles/latest/{tf}")
def get_latest_candle(tf: str):
    if tf not in {"15", "30", "60", "240", "1440"}:
        raise HTTPException(status_code=400, detail="unsupported timeframe")

    interval_min = int(tf)
    latest = fetch_latest_candle_for_chart(
        symbol=SYMBOL,
        interval_min=interval_min,
    )
    return latest or {}

@router.get("/zones/state", response_model=List[schemas.ZoneStateOut])
def list_zone_state(
    tf: str = Query(..., description="타임프레임: 15 / 30 / 60 / 240"),
    symbol: str = Query("BTCUSDT"),
    db: Session = Depends(get_db),
):
    if tf not in {"15", "30", "60", "240"}:
        raise HTTPException(status_code=400, detail="unsupported timeframe")

    interval_min = int(tf)
    rows = crud.list_zone_state(db, symbol, interval_min)
    # Pydantic에서 from_attributes=True 이므로 row를 그대로 반환해도 됨
    out: List[schemas.ZoneStateOut] = []
    for r in rows:
        out.append(
            schemas.ZoneStateOut(
                symbol=r.symbol,
                intervalMin=r.interval_min,
                startTime=r.start_time,
                side=r.side,
                isActive=r.is_active,
                entryOverride=r.entry_override,
            )
        )
    return out


# Structure Zone 상태 토글용 엔드포인트
# (symbol, intervalMin, startTime, side)를 키로 upsert
#
# ✅ DB에 저장한 뒤,
#    같은 symbol + intervalMin 에 해당하는 모든 박스 상태를 조회해서
#    WebSocket으로 한 번에 브로드캐스트
@router.post("/zones/state", response_model=schemas.ZoneStateOut)
async def upsert_zone_state(
    payload: schemas.ZoneStateBase,
    db: Session = Depends(get_db),
):
    # 1) DB upsert
    try:
        row = crud.upsert_zone_state(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # 2) 현재 TF 전체 박스 상태를 다시 읽어와서 WebSocket으로 브로드캐스트
    try:
      symbol = payload.symbol
      interval_min = int(payload.intervalMin)

      rows = crud.list_zone_state(db, symbol, interval_min)
      boxes = []
      for r in rows:
          #  Decimal → float/None 변환
          if r.entry_override is None:
              entry_override_value = None
          else:
              entry_override_value = float(r.entry_override)
          boxes.append(
              {
                  "symbol": r.symbol,
                  "intervalMin": r.interval_min,
                  "startTime": r.start_time.isoformat(),  # datetime → 문자열
                  "side": r.side,
                  "isActive": r.is_active,
                  "entryOverride": entry_override_value,
              }
          )

      event = {
          "type": "zone_state_sync",
          "symbol": symbol,
          "tf": str(interval_min),
          "boxes": boxes,
      }

      # 현재 핸들러는 async이므로 await 호출
      await broadcast_zone_state(event)

    except Exception as e:
      # 브로드캐스트 실패해도 HTTP 응답은 정상적으로 반환
      import logging
      logging.getLogger(__name__).warning(
          "zone state broadcast failed: %s", e
      )

    # 3) 클라이언트에게는 변경된 1개 row만 응답(기존과 동일)
    return schemas.ZoneStateOut(
        symbol=row.symbol,
        intervalMin=row.interval_min,
        startTime=row.start_time,
        side=row.side,
        isActive=row.is_active,
        entryOverride=row.entry_override,
    )

@router.get("/zones", response_model=List[schemas.ZoneOut])
def get_zone_boxes(
    symbol: str = Query("BTCUSDT"),
    interval_min: int = Query(..., alias="intervalMin", description="15/30/60/240"),
    from_ms: int | None = Query(None, alias="from", description="start_time >= from (epoch ms)"),
    to_ms: int | None = Query(None, alias="to", description="start_time <= to (epoch ms)"),
    db: Session = Depends(get_db),
):
    if interval_min not in (15, 30, 60, 240):
        raise HTTPException(status_code=400, detail="unsupported timeframe")

    start_time_from = None
    start_time_to = None

    # DB는 UTC naive로 통일해서 쓰고 있으므로, filter용 datetime도 naive로 맞춤
    if from_ms is not None:
        start_time_from = datetime.fromtimestamp(from_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    if to_ms is not None:
        start_time_to = datetime.fromtimestamp(to_ms / 1000, tz=timezone.utc).replace(tzinfo=None)

    zones = crud.list_zones_with_state(
        db,
        symbol=symbol,
        interval_min=interval_min,
        start_time_from=start_time_from,
        start_time_to=start_time_to,
    )
    return zones
