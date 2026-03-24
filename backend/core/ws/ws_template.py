## backend/core/ws/ws_template.py

import os, asyncio, json, time, websockets, hmac, hashlib, traceback, contextlib
from core.utils.log_utils import log
from dotenv import load_dotenv
load_dotenv()

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


# websockets 버전 차이를 흡수하는 안전한 닫힘 판정:
# - 구버전: ws.closed (bool)
# - 신버전(12.x): ws.state (OPEN/CLOSING/CLOSED), close_code
def _ws_is_closed(ws) -> bool:
    try:
        closed_attr = getattr(ws, "closed", None)
        if isinstance(closed_attr, bool):
            return closed_attr
    except Exception:
        pass

    state = getattr(ws, "state", None)
    if state is not None:
        name = getattr(state, "name", None) or str(state)
        if isinstance(name, str):
            if "CLOSING" in name or "CLOSED" in name:
                return True

    if getattr(ws, "close_code", None) is not None:
        return True

    return False

# 환경 설정
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_SECRET_KEY")

# 마감 캔들 평가 후 주기적 재연결 타이머 (공통 사용)
custom_reconnect_counter = {"count": 0}
WS_CUSTOM_RECONNECT_ENABLED = _env_bool("WS_CUSTOM_RECONNECT_ENABLED", False)
WS_CUSTOM_RECONNECT_PUBLIC_ONLY = _env_bool("WS_CUSTOM_RECONNECT_PUBLIC_ONLY", True)
WS_CUSTOM_RECONNECT_DELAY_SEC = max(1.0, _env_float("WS_CUSTOM_RECONNECT_DELAY_SEC", 8 * 60))

async def custom_reconnect_watchdog(ws, label: str):
    #log(f" [DEBUG] watchdog 시작됨 ← {label}")
    triggered = False  # 타이머 시작 여부
    try:
        while True:
            await asyncio.sleep(1)
            if not triggered and custom_reconnect_counter["count"] >= 2:
                triggered = True  # 중복 타이머 방지
                #log(f" [{label}] 캔들 마감 평가 2회 도달 → 8분 타이머 시작")
                await asyncio.sleep(WS_CUSTOM_RECONNECT_DELAY_SEC)
                #log(f" [{label}] 캔들 마감 평가 2회 이후 8분 경과 → 재연결 트리거")
                # ws가 이미 닫혔다면 close()가 예외를 던질 수 있으므로 가드
                log(f"🔁 [{label}] custom reconnect trigger (count={custom_reconnect_counter['count']})")
                with contextlib.suppress(Exception):
                    await ws.close(code=4000, reason="custom periodic reconnect")
                return  # 연결닫고 루프 탈출
    except asyncio.CancelledError:
        # 취소 신호면 조용히 종료
        return


#  인증 메시지 생성
def generate_auth_payload():
    expires = int((time.time() + 10) * 1000)
    message = f"GET/realtime{expires}"
    signature = hmac.new(
        BYBIT_API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return {
        "op": "auth",
        "args": [BYBIT_API_KEY, expires, signature]
    }

#  ping 루프
async def keep_alive(ws, label: str):
    try:
        while True:
            if _ws_is_closed(ws):
                return
            await ws.send(json.dumps({"op": "ping"}))  # 애플리케이션 ping
            pong_waiter = ws.ping()                    # WebSocket 프로토콜 ping
            await pong_waiter
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        reason = getattr(asyncio.current_task(), "_cancel_reason", "unknown")
        if reason == "reauth":
            log(f"🔄 [keep_alive] 인증 갱신을 위한 종료 ← {label}")
        elif reason == "unknown":
            log(f"❌ [keep_alive] 비정상 종료 ← {label} (cancelled, reason=unknown)")
        else:
            log(f"⚠️ [keep_alive] 종료됨 ← {label} (cancelled, reason={reason})")
    except Exception as e:
        log(f"⚠️ [keep_alive] Ping 실패 또는 WebSocket 종료 감지: {e} ← {label}")

#  세션 갱신을 위한 재인증 타이머 (45분)
REAUTH_INTERVAL = 45 * 60  # 45분

#  템플릿 진입점
async def websocket_handler(
    url: str,
    subscribe_args: list,
    label: str,
    message_handler,
    reconnect_delay: float = 5.0,
    auth_required: bool = True,
    enable_custom_reconnect: bool | None = None,
):
    while True:
        reauth_required = False  # 인증 갱신 여부 플래그
        try:
            async with websockets.connect(
                url, 
                ping_interval=None, 
                open_timeout=30, #  기본 10초 → 30초로 완화
                ) as ws:
                custom_reconnect_counter["count"] = 0
                if auth_required:
                    try:
                        await ws.send(json.dumps(generate_auth_payload()))
                        auth_resp = await asyncio.wait_for(ws.recv(), timeout=5)  #  타임아웃 적용
                        auth_data = json.loads(auth_resp)

                        if not auth_data.get("success", False):
                            log(f"❌ [{label}] WebSocket 인증 실패 → 응답: {auth_data}")
                            await asyncio.sleep(reconnect_delay)
                            continue
                        # else:
                        #     log(f" [{label}] 인증 성공")
                    except asyncio.TimeoutError:
                        log(f"⚠️ [{label}] 인증 응답 타임아웃 발생 (5초 이내 응답 없음)")
                        await asyncio.sleep(reconnect_delay)
                        continue
                    except Exception as e:
                        log(f"❌ [{label}] 인증 처리 중 예외 발생: {e}")
                        await asyncio.sleep(reconnect_delay)
                        continue

                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": subscribe_args
                }))

                ping_task = asyncio.create_task(keep_alive(ws, label=label))
                ping_task._cancel_reason = "reauth"  # 기본값 지정

                should_enable_custom_reconnect = (
                    enable_custom_reconnect
                    if enable_custom_reconnect is not None
                    else (
                        WS_CUSTOM_RECONNECT_ENABLED
                        and (not WS_CUSTOM_RECONNECT_PUBLIC_ONLY or not auth_required)
                    )
                )
                reconnect_task = None
                if should_enable_custom_reconnect:
                    reconnect_task = asyncio.create_task(custom_reconnect_watchdog(ws, label=label))
                
                # 인증이 필요한 스트림만 ping-pong 확인
                if auth_required:
                    try:
                        await ws.send(json.dumps({"op": "ping"}))
                        while True:
                            pong_resp = await asyncio.wait_for(ws.recv(), timeout=5)  #  핑-퐁 응답에도 타임아웃 적용
                            data = json.loads(pong_resp)
                            if data.get("op") == "pong":
                                log(f"🔐 [{label}] Private WebSocket 인증 및 구독 완료")
                                break
                    except asyncio.TimeoutError:
                        log(f"⚠️ [{label}] ping-pong 응답 타임아웃 발생")
                        await asyncio.sleep(reconnect_delay)
                        continue
                    except Exception as e:
                        log(f"❌ [{label}] ping-pong 처리 중 예외 발생: {e}")
                        await asyncio.sleep(reconnect_delay)
                        continue
                else:
                    log(f"📡 [{label}] Public WebSocket 연결 및 구독 완료")

                # 어떤 경로로든(정상/예외/취소) 빠질 때 하위 태스크를 반드시 정리
                try:
                    while True:
                        message = await ws.recv()
                        await message_handler(ws, message)
                except websockets.exceptions.ConnectionClosedError as e:
                    if e.code == 4000:
                        #log(f" [{label}] 인증 갱신을 위한 WebSocket 재시작 → 즉시 재연결")
                        reauth_required = True  # 즉시 재연결 플래그 설정
                    else:
                        log(f"❌ [{label}] WebSocket 종료됨 → code: {e.code}, reason: {e.reason}")
                except asyncio.CancelledError:
                    log(f"🛑 [{label}] websocket_handler 취소 감지 → 정리로 이동")
                    raise
                except Exception:
                    log(f"❌ [{label}] 내부 오류: \n{traceback.format_exc()}")
                finally:
                    # 하위 태스크 일괄 취소
                    for t in (ping_task, reconnect_task):
                        if t and not t.done():
                            t.cancel()
                    # 취소된 태스크가 정말 끝날 때까지 기다림
                    for t in (ping_task, reconnect_task):
                        if t:
                            # 태스크 종료 중 generator 종료 예외도 함께 정리
                            with contextlib.suppress(asyncio.CancelledError, Exception, GeneratorExit):
                                await t
                    # 소켓이 아직 열려 있으면 닫기
                    with contextlib.suppress(Exception):
                        if not _ws_is_closed(ws):
                            await ws.close()

        except asyncio.CancelledError:
            # 상위(main.py)에서 cancel되면 CancelledError를 재-raise하여 전파
            log(f"🛑 [{label}] websocket_handler 취소 감지 — 상위로 전파")
            raise
        except Exception:
            log(f"❌ [{label}] WebSocket 연결 실패:\n{traceback.format_exc()}")

        # 인증 갱신 시에는 즉시 재연결, 그렇지 않으면 sleep
        if not reauth_required:
            log(f"🔁 [{label}] {reconnect_delay}초 후 재연결 시도 중...")
            await asyncio.sleep(reconnect_delay)
        else:
            pass
