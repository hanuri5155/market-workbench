# FastAPI 진입점
#
# 차트 위 zone 후보와 포지션 오버레이를 브라우저에서 바로 다루게 하는 API/WS 진입점
# - 외부 공개 API 라우터 연결
# - 내부 runtime 전용 WebSocket 연결
# - candle detector, position watcher가 호출하는 내부 POST 엔드포인트 제공

import asyncio
import os
import time
from contextlib import asynccontextmanager, suppress

from fastapi import Body, FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.api.ws.control import (
    register_control_client,
    unregister_control_client,
)
from app.api.ws.position_overlay import (
    clear_overlay_and_broadcast,
    register_position_overlay_client,
    unregister_position_overlay_client,
    upsert_overlay_and_broadcast,
)
from app.api.ws.zone_state import (
    broadcast_zone_state,
    register_client,
    unregister_client,
)
from app.auth.otp.middleware import OTPAuthMiddleware
from app.db import crud
from app.db.session import SessionLocal, engine
from core.operations.event_loop_watchdog import start_event_loop_lag_watchdog
from core.utils.log_utils import log

@asynccontextmanager
async def lifespan(app: FastAPI):
    watchdog_task = None

    # API 프로세스가 DB와 통신 가능한지만 먼저 확인하기 위함
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")

    # 응답 지연 원인을 추적할 수 있게 이벤트 루프 watchdog를 같이 띄움
    watchdog_task = asyncio.create_task(start_event_loop_lag_watchdog("api"))

    yield

    if watchdog_task and not watchdog_task.done():
        watchdog_task.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog_task


# zone delta를 브라우저가 순서대로 처리할 수 있게 시퀀스를 붙이기 위함
_ZONE_DELTA_SEQ = 0


def _next_zone_delta_seq() -> int:
    global _ZONE_DELTA_SEQ
    _ZONE_DELTA_SEQ += 1
    return _ZONE_DELTA_SEQ

app = FastAPI(
    title="Market Workbench API",
    version="0.1.0",
    lifespan=lifespan,
)

# 브라우저 진입 호스트만 허용하도록 CORS Origin을 정리하기 위함
env_origins = os.getenv("CORS_ORIGINS", "")
if env_origins:
    origins = [o.strip() for o in env_origins.split(",") if o.strip()]
else:
    origins = [
        "http://localhost:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 화면 진입 보호는 OTP 미들웨어가 맡고, 내부 런타임 엔드포인트만 예외 처리
app.add_middleware(
    OTPAuthMiddleware,
    allow_paths=[
        "/api/health",
        "/api/auth/otp/verify",
        "/api/auth/otp/status",
        "/internal/candle-rest-confirmed",  
        "/internal/mtf-ma-source-candle",
        "/internal/position-overlay-event",
        "/internal/zones/delta",
        "/internal/zones/state-sync",
    ],
)


@app.get("/healthz", include_in_schema=False)
def healthz():
    # 인프라 liveness 체크는 의존성 검사 없이 즉시 200만 돌려주기 위함
    return {"status": "ok", "ts": int(time.time() * 1000)}


# 차트 화면이 Structure Zone 상태를 실시간으로 받기 위함
# 실제 사용 형태는 서버 -> 브라우저 단방향 push에 가까움
@app.websocket("/ws/zones")
async def zone_state_ws(ws: WebSocket):
    await register_client(ws)
    try:
        while True:
            try:
                await ws.receive_text()
            except Exception:
                break
    finally:
        await unregister_client(ws)


# 차트의 Entry/SL/TP 오버레이를 실시간으로 맞추기 위함
# 연결 직후 현재 상태 스냅샷 1회 전송으로 재접속 누락 감소
@app.websocket("/ws/position-overlay")
async def position_overlay_ws(ws: WebSocket):
    await register_position_overlay_client(ws)
    try:
        while True:
            try:
                await ws.receive_text()
            except Exception:
                break
    finally:
        await unregister_position_overlay_client(ws)

# 봇 runtime이 strategy_flags 변경을 즉시 반영할 수 있게 하기 위함
@app.websocket("/ws/control")
async def control_ws(ws: WebSocket):
    await register_control_client(ws)
    try:
        while True:
            try:
                await ws.receive_text()
            except Exception:
                break
    finally:
        await unregister_control_client(ws)

# candle detector가 REST 기준 확정봉을 차트 쪽에 전달할 때 쓰는 내부 엔드포인트
@app.post("/internal/candle-rest-confirmed")
async def internal_candle_rest_confirmed(payload: dict = Body(...)):
    try:
        symbol = payload.get("symbol")
        tf = payload.get("tf")
        from_ts = payload.get("from")
        to_ts = payload.get("to")
        candle = payload.get("candle")

        if not symbol or tf is None or from_ts is None or to_ts is None or candle is None:
            log(f"[API] /internal/candle-rest-confirmed 잘못된 payload 수신: {payload}")
            raise HTTPException(status_code=422, detail="invalid payload: candle is required")

        tf_int = int(tf)

        event = {
            "type": "candle_rest_confirmed",
            "symbol": symbol,
            "tf": str(tf_int),
            "from": from_ts,
            "to": to_ts,
            "candle": candle,
        }

        await broadcast_zone_state(event)

        log(
            f"[API] /internal/candle-rest-confirmed 브로드캐스트 완료: "
            f"symbol={symbol}, tf={tf}, from={from_ts}, to={to_ts}, candle={candle}"
        )
        return {"ok": True}
    except Exception as e:
        log(f"[API] /internal/candle-rest-confirmed 처리 중 오류: {e}")
        return {"ok": False, "reason": "exception"}


# candle detector가 240m/1440m 진행 중 봉을 MTF MA 소스로 브라우저에 전달할 때 쓰는 내부 엔드포인트
@app.post("/internal/mtf-ma-source-candle")
async def internal_mtf_ma_source_candle(payload: dict = Body(...)):
    try:
        symbol = payload.get("symbol")
        tf = payload.get("tf")
        candle = payload.get("candle")

        if not symbol or tf is None or candle is None:
            log(f"[API] /internal/mtf-ma-source-candle 잘못된 payload 수신: {payload}")
            raise HTTPException(status_code=422, detail="invalid payload: symbol/tf/candle are required")

        tf_str = str(int(tf))
        if tf_str not in {"240", "1440"}:
            log(f"[API] /internal/mtf-ma-source-candle 미지원 tf 수신: {payload}")
            raise HTTPException(status_code=422, detail="invalid payload: unsupported tf")

        event = {
            "type": "mtf_ma_source_candle",
            "symbol": symbol,
            "tf": tf_str,
            "candle": candle,
        }

        await broadcast_zone_state(event)
        return {"ok": True}
    except Exception as e:
        log(f"[API] /internal/mtf-ma-source-candle 처리 중 오류: {e}")
        return {"ok": False, "reason": "exception"}


# candle detector가 새 Zone 생성/깨짐 결과를 브라우저로 퍼뜨릴 때 쓰는 내부 엔드포인트
@app.post("/internal/zones/delta")
async def internal_zone_delta(payload: dict = Body(...)):
    try:
        symbol = payload.get("symbol")
        tf = payload.get("tf")
        if symbol is None or tf is None:
            raise ValueError("missing symbol/tf")
        tf_int = int(tf)

        raw_delta = payload.get("delta")

        # 중첩 payload로 들어오더라도 실제 delta dict만 꺼내 쓰기 위함
        if isinstance(raw_delta, dict) and isinstance(raw_delta.get("delta"), dict):
            raw_delta = raw_delta.get("delta")

        if not isinstance(raw_delta, dict):
            raise ValueError("delta must be an object")

        created = raw_delta.get("created") or []
        broken = raw_delta.get("broken") or []
        if not isinstance(created, list) or not isinstance(broken, list):
            raise ValueError("created/broken must be arrays")

        event = {
            "type": "zone_delta",
            "symbol": symbol,
            "tf": str(tf_int),
            "delta": {"created": created, "broken": broken},
            "server_ts": int(time.time() * 1000),
            "seq": _next_zone_delta_seq(),
        }

        await broadcast_zone_state(event)
        log(
            f"[API] /internal/zones/delta 브로드캐스트 완료: "
            f"symbol={symbol}, tf={tf_int}, created={len(created)}, broken={len(broken)}, seq={event['seq']}"
        )
        return {"ok": True, "seq": event["seq"]}
    except Exception as e:
        log(f"[API] /internal/zones/delta 처리 중 오류: {e}")
        return {"ok": False, "reason": "exception"}


# 브라우저 전체에 특정 타임프레임 Zone 상태를 다시 동기화할 때 쓰는 내부 엔드포인트
@app.post("/internal/zones/state-sync")
async def internal_zone_state_sync(payload: dict = Body(...)):
    db = None
    try:
        symbol = payload.get("symbol")
        tf = payload.get("tf")
        if symbol is None or tf is None:
            raise ValueError("missing symbol/tf")
        tf_int = int(tf)

        db = SessionLocal()
        rows = crud.list_zone_state(db, symbol, tf_int)

        boxes = []
        for r in rows:
            entry_override_value = float(r.entry_override) if r.entry_override is not None else None
            boxes.append(
                {
                    "symbol": r.symbol,
                    "intervalMin": r.interval_min,
                    "startTime": r.start_time.isoformat(),
                    "side": r.side,
                    "isActive": r.is_active,
                    "entryOverride": entry_override_value,
                }
            )

        event = {
            "type": "zone_state_sync",
            "symbol": symbol,
            "tf": str(tf_int),
            "boxes": boxes,
        }

        await broadcast_zone_state(event)
        log(
            f"[API] /internal/zones/state-sync 브로드캐스트 완료: "
            f"symbol={symbol}, tf={tf_int}, boxes={len(boxes)}"
        )
        return {"ok": True, "count": len(boxes)}
    except Exception as e:
        log(f"[API] /internal/zones/state-sync 처리 중 오류: {e}")
        return {"ok": False, "reason": "exception"}
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


# position watcher와 strategy runtime이 오버레이 변경을 전달할 때 쓰는 내부 엔드포인트
@app.post("/internal/position-overlay-event")
async def internal_position_overlay_event(payload: dict = Body(...)):
    try:
        action = payload.get("action")
        if action == "update":
            overlay = payload.get("overlay")
            if not isinstance(overlay, dict) or not overlay.get("id"):
                log(f"[API] /internal/position-overlay-event 잘못된 overlay payload: {payload}")
                raise HTTPException(status_code=422, detail="invalid payload: overlay.id required")

            await upsert_overlay_and_broadcast(overlay)
            return {"ok": True}

        if action == "clear":
            oid = payload.get("id")
            if not oid:
                log(f"[API] /internal/position-overlay-event 잘못된 clear payload: {payload}")
                raise HTTPException(status_code=422, detail="invalid payload: id required")

            exit_ts = payload.get("exitTs")
            try:
                exit_ts = int(exit_ts) if exit_ts is not None else None
            except Exception:
                exit_ts = None

            await clear_overlay_and_broadcast(str(oid), exit_ts=exit_ts)
            return {"ok": True}

        log(f"[API] /internal/position-overlay-event action 미지원: {payload}")
        raise HTTPException(status_code=422, detail="invalid action")

    except HTTPException:
        raise
    except Exception as e:
        log(f"[API] /internal/position-overlay-event 처리 중 오류: {e}")
        return {"ok": False, "reason": "exception"}


app.include_router(router)
