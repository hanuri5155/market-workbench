## backend/core/ws/candle_detector.py

import os, asyncio, json, time, requests, statistics
from datetime import datetime, timezone
from asyncio import create_task, Task
from pathlib import Path
from core.state import shared_state
from core.ws import ws_template
from core.ws.ws_template import websocket_handler
from core.utils.log_utils import log
from core.utils.file_utils import write_json_atomic
from core.persistence.candles_repo import upsert_candle
from strategies.demo_zone.incremental import incremental_update_after_rest_confirmed

# 프로젝트 루트(/backend) 절대 경로 계산
# candle_detector.py 위치: backend/core/ws/candle_detector.py
#   parents[0] = .../backend/core/ws
#   parents[1] = .../backend/core
#   parents[2] = .../backend    우리가 원하는 backend 루트
BASE_DIR = Path(__file__).resolve().parents[2]

SYMBOL = os.getenv("SYMBOL")
INTERVALS = ["15", "30", "60", "240", "1440"]
CANONICAL_TO_BYBIT_INTERVAL = {
    "1440": "D",
}
BYBIT_TO_CANONICAL_INTERVAL = {
    "D": "1440",
}


# 다양한 interval 표현(15, 15m, D)을 내부 표준값("15","30","60","240","1440")으로 정규화
def _canonical_interval(interval: str) -> str | None:
    raw = str(interval or "").strip()
    if not raw:
        return None

    if raw.lower().endswith("m") and raw[:-1].isdigit():
        raw = raw[:-1]

    upper = raw.upper()
    if upper in BYBIT_TO_CANONICAL_INTERVAL:
        return BYBIT_TO_CANONICAL_INTERVAL[upper]

    return raw if raw in INTERVALS else None


def _interval_to_bybit(interval: str) -> str:
    canonical = _canonical_interval(interval)
    if canonical is None:
        return str(interval)
    return CANONICAL_TO_BYBIT_INTERVAL.get(canonical, canonical)


def _interval_to_min(interval: str) -> int:
    canonical = _canonical_interval(interval)
    if canonical is None:
        raise ValueError(f"unsupported interval: {interval}")
    return int(canonical)

# 볼린저 설정/저장 경로
BBANDS_DIR = os.getenv("BBANDS_DIR", "storage/bbands")
BBANDS_LENGTH = int(os.getenv("BBANDS_LENGTH", "20"))   # 기본 20
BBANDS_MULT   = float(os.getenv("BBANDS_MULT", "2"))    # 기본 2

# 서버 시간 동기화 루프를 단일 인스턴스로 관리
_server_time_sync_task = None   # asyncio.Task 또는 None

pending_kline_map = {interval: {} for interval in INTERVALS}
last_confirmed_end = {interval: None for interval in INTERVALS}
confirm_miss_count = {interval: 0 for interval in INTERVALS}
server_time_offset_ms = 0  # 서버 시간 - 로컬 시간(ms)

# 서버 시간 받아오기
def get_bybit_server_time():
    try:
        r = requests.get("https://api.bybit.com/v5/market/time", timeout=3)
        return int(r.json()["time"])
    except:
        return None

# 서버 시간 보정 루틴
async def sync_server_time_periodically():
    global server_time_offset_ms
    while True:
        local = int(time.time() * 1000)
        #  블로킹 requests.get()을 이벤트 루프 밖(스레드)에서 실행
        server = await asyncio.to_thread(get_bybit_server_time)
        if server:
            server_time_offset_ms = server - local
            #log(f" [서버 시간 동기화] 오차: {server_time_offset_ms} ms")
        await asyncio.sleep(10)


# 캔들 저장 경로
def get_candle_storage_path(interval: str) -> str:
    interval = _canonical_interval(interval) or str(interval).replace("m", "")
    return str(BASE_DIR / "storage" / "candles" / f"{interval}m.json")  # ← 절대경로

def save_candle_to_file(interval: str, candle: dict):
    path = get_candle_storage_path(interval)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # 기존 파일 불러오기 (없으면 빈 리스트)
    candles = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                prev = json.load(f)
                candles = prev if isinstance(prev, list) else [prev]
        except Exception:
            # atomic replace 특성상 여기 올 일은 드뭄. 그래도 안전망으로 빈 리스트 사용
            candles = []


    # 중복 저장 방지: 동일한 start가 있으면 덮어쓰기
    s = candle.get("start")
    candles = [c for c in candles if c.get("start") != s]
    candles.append(candle)

    # 정렬 보장 (start 기준)
    candles.sort(key=lambda c: c.get("start", 0))

    # interval별 상한 (15/30분은 5,000, 60분 8,000, 240분 10,000)
    canonical_interval = _canonical_interval(interval) or str(interval)
    limits = {"15": 5000, "30": 5000, "60": 8000, "240": 10000, "1440": 5000}
    MAX_CANDLES = limits.get(canonical_interval, 5000)
    candles = candles[-MAX_CANDLES:]

    #  원자적 저장
    write_json_atomic(path, candles, indent=2)

#  BBands 저장 경로
def get_bbands_storage_path(interval: str) -> str:
    canonical_interval = _canonical_interval(interval) or str(interval)
    return f"{BBANDS_DIR}/{canonical_interval}m.json"

def _load_json_list(path: str) -> list:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else [data]
        except Exception:
            return []
    return []

def save_bbands_to_file(interval: str, band: dict):
    path = get_bbands_storage_path(interval)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    arr = _load_json_list(path)

    # start 키 기준으로 덮어쓰기(중복 방지)
    s = band.get("start")
    arr = [x for x in arr if x.get("start") != s]
    arr.append(band)
    arr.sort(key=lambda x: x.get("start", 0))

    # 캔들 파일과 동일 상한 적용
    canonical_interval = _canonical_interval(interval) or str(interval)
    limits = {"15": 5000, "30": 5000, "60": 8000, "240": 10000, "1440": 5000}
    MAX_ITEMS = limits.get(canonical_interval, 5000)
    arr = arr[-MAX_ITEMS:]

    write_json_atomic(path, arr, indent=2)

def _sma(values: list) -> float:
    return sum(values) / len(values)

def _stdev(values: list) -> float:
    # TradingView/Bybit 차트는 'biased(모수, ddof=0)' 표준편차 사용으로 알려져 있음
    # Python: statistics.pstdev = 모표준편차
    return statistics.pstdev(values) if len(values) > 1 else 0.0

# closes: 최근 종가 시퀀스(길이 >= length)
# 반환: (middle, upper, lower)
def compute_bbands(closes: list, length: int = BBANDS_LENGTH, mult: float = BBANDS_MULT):
    window = closes[-length:]
    mid = _sma(window)
    sd  = _stdev(window)
    up  = mid + mult * sd
    lo  = mid - mult * sd
    return (round(mid, 2), round(up, 2), round(lo, 2))

# 캔들 파일에서 upto_start 이전 confirm 캔들 close 최근순 추출
def _get_recent_closes_for_interval(interval: str, upto_start: int) -> list:
    candles = _load_json_list(get_candle_storage_path(interval))
    # start 기준 정렬되어 저장됨(보장) → 그대로 사용
    prev = [c["close"] for c in candles if isinstance(c, dict) and c.get("start", 0) < upto_start and "close" in c]
    return prev

# 실시간/마감 공통: candle에 담긴 close(실시간 갱신 값 포함)를 사용해
# 해당 캔들의 밴드 계산 후 저장
# - 마감 전(confirm=False) → 밴드도 계속 덮어쓰기
# - 마감(confirm=True) → 확정값으로 저장
def update_bbands(interval: str, candle: dict):
    try:
        start = int(candle["start"])
        end   = int(candle["end"])
        close_now = float(candle["close"])  # 실시간 WS close(진행중 봉도 갱신됨)

        prev_closes = _get_recent_closes_for_interval(interval, start)
        # 직전 확정 봉 (length-1)개 + 현재 봉 close 합치기
        series = (prev_closes + [close_now])[-max(BBANDS_LENGTH, 1):]

        if len(series) >= BBANDS_LENGTH:
            mid, up, lo = compute_bbands(series, BBANDS_LENGTH, BBANDS_MULT)
            band = {
                "start": start,
                "end": end,
                "confirm": bool(candle.get("confirm", False)),
                "mid": mid,
                "up": up,
                "lo": lo
            }
            save_bbands_to_file(interval, band)
            # 파일 저장 후, 진행중 봉이라도 메모리 밴드 즉시 반영 (intrabar 업데이트)
            shared_state.bbands_map[interval] = {
                "start": start,
                "mid": mid, "up": up, "lo": lo
            }
            # 주의: last_confirmed_candle 은 확정 시(print_candle)에서만 갱신 유지
            #log(f" [BBANDS LIVE] {interval}m mid/up/lo = {mid}/{up}/{lo}")
    except Exception as e:
        log(f"⚠️ [BBANDS] 업데이트 실패(interval={interval}): {e}")

# 서버 시작 시 기존 캔들 파일로부터 전 구간 BBands 백필
def backfill_bbands(interval: str):
    candles = _load_json_list(get_candle_storage_path(interval))
    closes  = [float(c["close"]) for c in candles if isinstance(c, dict) and "close" in c]
    out = []
    for i in range(BBANDS_LENGTH - 1, len(candles)):
        window = closes[i - BBANDS_LENGTH + 1 : i + 1]
        mid, up, lo = compute_bbands(window, BBANDS_LENGTH, BBANDS_MULT)
        c = candles[i]
        out.append({
            "start": int(c["start"]),
            "end": int(c["end"]),
            "confirm": bool(c.get("confirm", False)),
            "mid": mid,
            "up": up,
            "lo": lo
        })
    write_json_atomic(get_bbands_storage_path(interval), out, indent=2)

# REST 재조회
def fetch_rest_kline(start_ts: int, interval: str):
    bybit_interval = _interval_to_bybit(interval)
    endpoint = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": SYMBOL,
        "interval": bybit_interval,
        "start": int(start_ts),   #  특정 캔들을 확실히 포함시키기
        "limit": 3,
    }
    try:
        #  timeout 명시
        resp = requests.get(endpoint, params=params, timeout=3)
        data = resp.json()
        if resp.status_code == 200 and data.get("retCode") == 0:
            for k in data["result"]["list"]:
                if int(k[0]) == start_ts:
                    return {
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                    }
    except Exception as e:
        log(f"⚠️ [Candle Detector] REST 캔들 조회 실패: {e}")
    return None


# REST 검증 (지연 실행용)
async def delayed_rest_check(interval: str, start_ts: int, delay_ms: int, original_kline: dict):
    await asyncio.sleep(delay_ms / 1000)

    #  블로킹 REST 호출을 스레드로 넘김
    rest = await asyncio.to_thread(fetch_rest_kline, start_ts, interval)
    if not rest:
        return

    # 1) 차이 계산 (로그 용도)
    mismatch = False
    diffs = {}
    for key in ['open', 'high', 'low', 'close']:
        rest_val = rest[key]
        local_val = original_kline[key]
        diff = abs(rest_val - local_val)
        diffs[key] = diff
        if diff > 0.01:  #  오차 기준 (0.01 이상일 때만 경고 로그)
            log(f"❗ [Candle Detector] [{interval}m] {key.upper()} 차이: WS={local_val}, REST={rest_val}, Δ={diff:.4f}")
            mismatch = True

    # 2) REST 값을 기준으로 항상 캔들/BBands/DB 덮어쓰기
    try:
        fixed = dict(original_kline)
        fixed["open"] = rest["open"]
        fixed["high"] = rest["high"]
        fixed["low"]  = rest["low"]
        fixed["close"] = rest["close"]

        # DB 보정 (항상 REST 기준으로 동기화)
        interval_min = _interval_to_min(interval)
        await asyncio.to_thread(
            upsert_candle,
            symbol=SYMBOL,
            interval_min=interval_min,
            start_ms=fixed["start"],
            open_=fixed["open"],
            high=fixed["high"],
            low=fixed["low"],
            close=fixed["close"],
            volume=None,
            turnover=None,
            source="bybit_rest_fix",
        )

        # JSON + BBands 보정 (항상 REST 기준으로 동기화)
        await asyncio.to_thread(save_candle_to_file, interval, fixed)
        await asyncio.to_thread(update_bbands, interval, fixed)

        if mismatch:
            log(
                f"🛠️ [Candle Detector] [{interval}m] REST 기준으로 캔들/BBands 보정 완료 (start={fixed['start']}, "
                f"ΔOHL C={diffs['open']:.4f}/{diffs['high']:.4f}/{diffs['low']:.4f}/{diffs['close']:.4f})"
            )
        else:
            log(
                f"✅ [Candle Detector] [{interval}m] REST 값으로 캔들/BBands 동기화 완료 (start={fixed['start']}, "
                f"ΔOHL C={diffs['open']:.4f}/{diffs['high']:.4f}/{diffs['low']:.4f}/{diffs['close']:.4f})"
            )
    except Exception as e:
        log(f"⚠️ [Candle Detector] REST 동기화 처리 중 오류: {e}")
        return  # 아래 HTTP 전송까지 막기

    #  3) FastAPI로 HTTP POST (candle 포함)
    try:
        tf_int = _interval_to_min(interval)

        payload = {
            "symbol": SYMBOL,
            "tf": tf_int,
            "from": int(fixed["start"]),  # 시작 시각(ms)
            "to": int(fixed["end"]),      # 끝 시각(ms)
            "candle": {
                "start": int(fixed["start"]),
                "end": int(fixed["end"]),
                "open": float(fixed["open"]),
                "high": float(fixed["high"]),
                "low": float(fixed["low"]),
                "close": float(fixed["close"]),
                "confirm": True,
            },
        }

        # Structure Zone 증분 갱신(delta)은 별도 이벤트 전달
        # candle_rest_confirmed payload에는 포함하지 않는다(이벤트 계약 분리 완료)
        delta = None
        try:
            delta = incremental_update_after_rest_confirmed(
                symbol=SYMBOL,
                interval_min=tf_int,
                candle=payload["candle"],
            )
            created_n = len(delta.get('created') or []) if isinstance(delta, dict) else 0
            broken_n  = len(delta.get('broken')  or []) if isinstance(delta, dict) else 0
            log(f"🧩 [Zone][{tf_int}m] bot-side delta: created={created_n}, broken={broken_n}")
        except Exception as e:
            log(f"⚠️ [Zone][{tf_int}m] bot-side incremental_update 실패: {e}")

        # Structure Zone delta 전용 엔드포인트로만 전송
        if isinstance(delta, dict):
            try:
                box_delta_payload = {
                    "symbol": SYMBOL,
                    "tf": tf_int,
                    "delta": {
                        "created": delta.get("created") or [],
                        "broken":  delta.get("broken")  or [],
                    },
                }
                box_delta_url = os.getenv(
                    "ZONE_DELTA_NOTIFY_URL",
                    "http://127.0.0.1:8000/internal/zones/delta",
                )
                resp2 = await asyncio.to_thread(
                    requests.post,
                    box_delta_url,
                    json=box_delta_payload,
                    timeout=2,
                )
                if resp2.status_code != 200:
                    log(f"[CANDLE DETECTOR] ❌ box-delta notify 실패: {resp2.status_code} {resp2.text}")
                else:
                    log(
                        f"[CANDLE DETECTOR] ✅ box-delta notify 성공: tf={tf_int}, "
                        f"created={len(box_delta_payload['delta']['created'])}, "
                        f"broken={len(box_delta_payload['delta']['broken'])}"
                    )
            except Exception as e:
                log(f"[CANDLE DETECTOR] ❌ box-delta notify 예외: {e}")

        #  URL 하드코딩 제거(포트/리버스프록시 구성 바뀌어도 대응)
        url = os.getenv(
            "CANDLE_REST_NOTIFY_URL",
            "http://127.0.0.1:8000/internal/candle-rest-confirmed",
        )
        resp = await asyncio.to_thread(
            requests.post,
            url,
            json=payload,
            timeout=2,
        )

        if resp.status_code != 200:
            log(
                f"⚠️ [Candle Detector] /internal/candle-rest-confirmed 호출 실패: "
                f"status={resp.status_code}, body={resp.text}"
            )
        else:
            log(
                f"[Candle Detector] [{interval}m] REST 검증 완료 신호 HTTP 전송 완료: "
                f"symbol={SYMBOL}, tf={tf_int}, from={fixed['start']}, to={fixed['end']}"
            )

    except Exception as e:
        log(f"⚠️ [Candle Detector] REST 검증 HTTP 통지 중 오류: {e}")


# 봉 출력 + 검증 예약
async def print_candle(interval: str, candle: dict):
    start = candle["start"]
    end = candle["end"]
    open_ = candle["open"]
    high = candle["high"]
    low = candle["low"]
    close = candle["close"]
    confirm = candle["confirm"]

    now = int(time.time() * 1000) + server_time_offset_ms
    delay_until = end + 700  # 실제 마감 이후
    delay_ms = max(0, delay_until - now)

    #  1) 캔들을 DB에 저장
    try:
        # '15', '30', '60', '240' 같은 문자열을 분 단위 정수로 변환
        interval_min = _interval_to_min(interval)
        await asyncio.to_thread(
            upsert_candle,
            symbol=SYMBOL,
            interval_min=interval_min,
            start_ms=start,
            open_=open_,
            high=high,
            low=low,
            close=close,
            # volume/turnover는 현재 None으로 저장
            volume=None,
            turnover=None,
            source="bybit_ws",
        )
    except Exception as e:
        log(f"⚠️ [Candle Detector] DB 캔들 저장 실패(interval={interval}): {e}")

    #  2) 기존 JSON + BBands 흐름 유지 (차트/BBands가 아직 JSON 기준이라서)
    await asyncio.to_thread(save_candle_to_file, interval, candle)
    await asyncio.to_thread(update_bbands, interval, candle)  # 마감 확정 BBands 저장
    # =====[NEW] BBands 계산 → 파일 저장 + shared_state 반영 =====
    # upto_start 이전의 종가 length개를 모아 밴드 계산
    # =====[FIX] shared_state 갱신도 현재 close 포함해서 동일 기준으로 =====
    try:
        prev = await asyncio.to_thread(_get_recent_closes_for_interval, interval, candle["start"])
        series = (prev + [float(candle["close"])])[-BBANDS_LENGTH:]
        if len(series) >= BBANDS_LENGTH:
            mid, up, lo = compute_bbands(series, BBANDS_LENGTH, BBANDS_MULT)
            shared_state.bbands_map[interval] = {
                "start": candle["start"],
                "mid": mid, "up": up, "lo": lo
            }
            shared_state.last_confirmed_candle[interval] = candle
    except Exception:
        pass
    asyncio.create_task(delayed_rest_check(interval, start, delay_ms, candle))


def safe_float(val, label=""):
    try:
        if val is None or str(val).strip().lower() == "none" or str(val).strip() == "":
            raise ValueError(f"[Candle Detector] {label} 필드가 None 또는 비어있음")
        return float(val)
    except Exception as e:
        log(f"❌ [Candle Detector] float 변환 실패: {label}={val} → {e}")
        return None

#  WebSocket 메시지 핸들러
async def handle_kline(ws, message: str):
    # 1) JSON 파싱
    try:
        data = json.loads(message)
    except Exception as e:
        log(f"❌ [Candle Detector] JSON 파싱 오류: {e} → 원본: {message}")
        return
    
    # 2) 데이터 유효성 체크 및 float 변환
    try:
        if "topic" not in data or not data["topic"].startswith("kline."):
            return
        
        interval_token = data["topic"].split(".")[1]
        interval = _canonical_interval(interval_token)
        if interval is None or interval not in INTERVALS:
            log(f"⚠️ [Candle Detector] 지원되지 않는 interval 감지됨: {interval_token}")
            return
        
        if "data" not in data or not isinstance(data["data"], list) or len(data["data"]) == 0:
            log(f"⚠️ [Candle Detector] 메시지에 유효한 kline 데이터 없음 → 무시: {data}")
            return

        kline = data["data"][0]
        if not isinstance(kline, dict):
            log(f"⚠️ [Candle Detector] kline 데이터가 dict 아님 → 무시: {kline}")
            return

        required_keys = ["open", "close", "high", "low", "start", "end"]
        for k in required_keys:
            v = kline.get(k)
            if v is None or str(v).lower() == "none" or str(v).strip() == "":
                log(f"⚠️ [Candle Detector] 필드 누락 또는 비정상: {k}={v} → 무시: {kline}")
                return

        candle = {
            "start": int(kline["start"]),
            "end": int(kline["end"]),
            "confirm": bool(kline.get("confirm", False)),
            "open": safe_float(kline.get("open"), "open"),
            "close": safe_float(kline.get("close"), "close"),
            "high": safe_float(kline.get("high"), "high"),
            "low": safe_float(kline.get("low"), "low"),
        }

        if None in [candle["open"], candle["close"], candle["high"], candle["low"]]:
            log(f"⚠️ [Candle Detector] float 변환 실패로 candle 무시됨 → {candle}")
            return
        
        # 진행 중 봉이라도 DB candles 테이블에 계속 upsert
        # → FastAPI /candles/latest/{tf} 가 이 값을 바로 읽을 수 있게 함
        try:
            interval_min = _interval_to_min(interval)
            await asyncio.to_thread(
                upsert_candle,
                symbol=SYMBOL,
                interval_min=interval_min,
                start_ms=candle["start"],
                open_=candle["open"],
                high=candle["high"],
                low=candle["low"],
                close=candle["close"],
                volume=None,
                turnover=None,
                source="bybit_ws_live",  # 구분용 태그
            )
        except Exception as e:
            log(f"⚠️ [Candle Detector] DB 실시간 캔들 upsert 실패(interval={interval}): {e}")
        
        # 프론트 차트용: 최신 캔들 스냅샷을 shared_state 에 저장
        try:
            # interval 은 "15" / "30" / "60" / "240" 문자열
            shared_state.update_latest(interval, candle)
        except Exception as e:
            log(f"⚠️ [Candle Detector] 최신 캔들 shared_state 저장 실패(interval={interval}): {e}")
        
        # 실시간 BBands 업데이트
        await asyncio.to_thread(update_bbands, interval, candle)

    except Exception as e:
        log(f"❌ [Candle Detector] kline 파싱 또는 float 변환 실패: {e} → 원본: {data}")
        return

    # 3) 평가 / 저장 처리
    try:
        if candle["confirm"]:
            if last_confirmed_end[interval] is None or candle["end"] > last_confirmed_end[interval]:
                last_confirmed_end[interval] = candle["end"]
                log(f"📡 [Candle Detector] [{interval}m] 마감 confirm 수신됨")

                #  1) 먼저 디스크에 저장(atomic)
                await print_candle(interval, candle)
                
                if interval == "15":
                    try:
                        ws_template.custom_reconnect_counter["count"] += 1
                    except Exception as e:
                        log(f"⚠️ [Candle Detector] custom_reconnect_counter 증가 실패: {e}")
            return  # confirm True 처리 후 끝냄

        # 아직 confirm=False → 대기 리스트에 추가
        pending_kline_map[interval][candle["start"]] = candle

    except Exception as e:
        log(f"❌ [Candle Detector] 평가/저장 처리 중 오류: {e} → candle: {candle}")


#  confirm: True 안 온 봉 확인 루틴
async def monitor_pending_candles(interval: str):
    while True:
        now = int(time.time() * 1000) + server_time_offset_ms
        expired = []
        for start, candle in pending_kline_map[interval].items():
            if now >= candle["end"] + 1200 and (last_confirmed_end[interval] is None or candle["end"] > last_confirmed_end[interval]):
                last_confirmed_end[interval] = candle["end"]
                
                #  confirm이 결국 안 온 봉
                confirm_miss_count[interval] += 1
                log(f"❗ [Candle Detector] [{interval}m] confirm=True 미도착 봉 감지됨 → 누적 {confirm_miss_count[interval]}개")

                ts = datetime.fromtimestamp(candle["start"] / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                log(f"🕓 [Candle Detector] 마감봉 임시 평가 수행 (시작시각={ts} UTC)")

                await print_candle(interval, candle)
                
                #  15분봉일 경우 reconnect 카운트만 유지
                if interval == "15":
                    try:
                        ws_template.custom_reconnect_counter["count"] += 1
                        log(f"🔁 [Candle Detector] 15분봉 confirm 미도착 → reconnect 카운트 증가 (현재값: {ws_template.custom_reconnect_counter['count']})")
                    except Exception as e:
                        log(f"⚠️ [Candle Detector] reconnect 카운트 증가 실패: {e}")

                expired.append(start)
        for s in expired:
            del pending_kline_map[interval][s]
        await asyncio.sleep(1)

#  WebSocket 실행 및 재시작 관리
async def launch_candle_detectors():
    # 서버 시작 시 1회 BBands 백필
    try:
        for iv in INTERVALS:
            backfill_bbands(iv)
        log("✅ [BBANDS] 기존 캔들 기반 백필 완료")
    except Exception as e:
        log(f"⚠️ [BBANDS] 백필 중 오류: {e}")

    # 서버 시간 동기화 루프를 단 1회만 시작
    global _server_time_sync_task
    if _server_time_sync_task is None or _server_time_sync_task.done():
        _server_time_sync_task = create_task(sync_server_time_periodically())
        log("⏱️ [Candle Detector] 서버 시간 동기화 루프 시작(단일).")

    for interval in INTERVALS:
        bybit_interval = _interval_to_bybit(interval)
        topic = f"kline.{bybit_interval}.{SYMBOL}"
        label = f"candle_ws_{interval}m"

        # 기존 WebSocket task가 있으면 종료
        existing_task: Task = shared_state.current_kline_tasks.get(interval)
        if existing_task and not existing_task.done():
            log(f"🔁 [Candle Detector] 기존 {interval}분봉 WebSocket 감지기 Task 취소 중...")
            existing_task.cancel()
            try:
                await existing_task
            except asyncio.CancelledError:
                log(f"🛑 [Candle Detector] 이전 {interval}분봉 WebSocket 감지기 정상 종료됨")

        async def runner(interval=interval, topic=topic, label=label):
            await asyncio.gather(
                websocket_handler(
                    url="wss://stream.bybit.com/v5/public/linear",
                    subscribe_args=[topic],
                    label=label,
                    message_handler=handle_kline,
                    auth_required=False
                ),
                monitor_pending_candles(interval),
            )

        task = create_task(runner())
        shared_state.current_kline_tasks[interval] = task
        shared_state.current_intervals[interval] = interval
