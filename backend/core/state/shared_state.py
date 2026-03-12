## backend/core/state/shared_state.py

# 프로세스 전체에서 함께 쓰는 런타임 상태 모음
#
# 여기 있는 값은 API, WebSocket 핸들러, 전략 runtime, UI 동기화 로직의 공동 참조 대상
# 파일 기반 상태(execution_data_store)와 메모리 캐시를 한곳에서 관리하기 위함

import os, json, time
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv
from core.persistence.execution_store import (
    load_execution_data_store as _load_execution_data_store,
    save_execution_data_store as _save_execution_data_store,
)
load_dotenv()

# backend/core/state/shared_state.py 기준으로 계산한 backend 루트 경로
BACKEND_DIR = Path(__file__).resolve().parents[2]

EXECUTION_DATA_STORE_PATH = os.getenv("EXECUTION_DATA_STORE_PATH")

# 다른 스레드에서 coroutine을 main loop로 넘길 때 참조하는 이벤트 루프
main_event_loop = None

# config watcher가 갱신하는 최신 설정 스냅샷
current_config = {}

# DB strategy_flags 테이블을 읽어 메모리에 유지하는 전략 on/off 상태
strategy_flags = {
    "enable_trading": False,
    "enable_zone_strategy": False,
}
strategy_flags_updated_at = 0.0

# 데모 전략이 마지막으로 만든 샘플 zone 보관용
last_demo_zone = None

# zone 상태 WS 수신 직후 프론트 강제 갱신 플래그
zone_levels_force_refresh = False
zone_levels_force_refresh_reason = ""
zone_state_ws_connected = False

# zone 종료 처리 실패 직후 재진입이 연속으로 일어나지 않게 막는 임시 차단 정보
zone_parent_block_until = {}
zone_side_block_until = {}

# 펀딩 예고 패널에서 바로 읽는 최신 스냅샷
funding_snapshot = {}

# 차트와 전략 계산이 함께 참조하는 지표/캔들 캐시
bbands_map = {
    "15": {"start": None, "mid": None, "up": None, "lo": None},
    "30": {"start": None, "mid": None, "up": None, "lo": None},
    "60": {"start": None, "mid": None, "up": None, "lo": None},
    "240":{"start": None, "mid": None, "up": None, "lo": None},
}
last_confirmed_candle = {
    "15": None, "30": None, "60": None, "240": None
}

# 최근 TP 체결 시각. 잔량 dust 보정이 너무 자주 겹치지 않게 쓰는 디바운스 기준
last_tp_fill_at = {}

# sessions 테이블에 기록된 현재 프로세스의 세션 id
session_id = None

# 전략별 동시 처리 슬롯과 캔들 작업 추적용 캐시
priority_sl_preempt = {}
current_kline_tasks = {}
current_intervals = {}

# 심볼별 최신 가격 캐시. 값이 없으면 0.0으로 시작
latest_price_map = defaultdict(float)

def get_last_price(symbol: str):
    return latest_price_map.get(symbol)

# 가격이 변경되었으면 상태를 갱신하고 True 반환
# 동일하면 False 반환
def update_price(symbol: str, price: float) -> bool:
    old_price = latest_price_map.get(symbol)
    if old_price != price:
        latest_price_map[symbol] = price 
        return True
    return False

def load_execution_data_store():
    return _load_execution_data_store(EXECUTION_DATA_STORE_PATH)

# execution_data_store는 주문/포지션 진행 상태를 JSON 파일로 유지하는 저장소
# 봇 재시작 시 직전 진행 상태 복구 기준
_raw = load_execution_data_store()
execution_data_store = _raw.get("store", {})
last_execution_order_id = _raw.get("meta", {}).get("last_active_order")

def save_execution_data_store(data):
    return _save_execution_data_store(
        EXECUTION_DATA_STORE_PATH,
        data,
        last_active_order=last_execution_order_id,
    )

# position_watcher와 execution handler가 "현재 포지션"으로 보는 키
# 재시작 직후에는 저장된 last_active_order를 복구 시작점으로 사용
current_position_link_id = last_execution_order_id

# interval별 마지막 캔들 캐시와 마지막으로 본 캔들 식별자
_kline_cache = {}
_kline_last_seen_id = {}

# 캔들 파일 구조가 달라도 마지막 캔들 dict 하나로 정규화
def _parse_last_candle(data):
    if isinstance(data, list):
        return data[-1] if data else None
    if isinstance(data, dict):
        if isinstance(data.get("candles"), list) and data["candles"]:
            return data["candles"][-1]
        return data  # candles 래퍼가 없으면 단일 캔들 객체로 간주
    return None

# storage/candles/<tf>m.json에서 최신 마감 캔들 1개 조회
#
# - wait_new=True면 직전에 본 캔들과 다른 항목이 들어올 때까지 잠시 대기
# - 파일이 쓰이는 도중 읽으면 깨질 수 있어 mtime/size를 한 번 더 확인
# - 끝까지 불안정하면 이전 캐시값으로 폴백
def get_latest_closed_kline_ws(interval: int, *, wait_new: bool=False,
                               timeout: float=1.5, poll: float=0.1):
    path = str(BACKEND_DIR / "storage" / "candles" / f"{interval}m.json")
    if not os.path.exists(path):
        return _kline_cache.get(interval)

    start_ts = time.time()
    last_id = _kline_last_seen_id.get(interval)

    while True:
        try:
            st1 = os.stat(path)
            mtime1, size1 = st1.st_mtime_ns, st1.st_size

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            c = _parse_last_candle(data)
            if not isinstance(c, dict):
                raise ValueError("No candle found")

            # 쓰는 도중 읽은 상황을 피하기 위해 mtime/size를 한 번 더 확인
            st2 = os.stat(path)
            if st2.st_mtime_ns != mtime1 or st2.st_size != size1:
                if time.time() - start_ts < timeout:
                    time.sleep(poll)
                    continue

            cid = c.get("end") or c.get("start")
            # 직전에 본 캔들과 같으면 새 마감 캔들이 들어올 때까지 짧게 대기
            if wait_new and last_id is not None and cid == last_id:
                if time.time() - start_ts < timeout:
                    time.sleep(poll)
                    continue

            _kline_cache[interval] = c
            _kline_last_seen_id[interval] = cid
            return c

        except Exception as e:
            if time.time() - start_ts < timeout:
                time.sleep(poll)
                continue
            try:
                from core.utils.log_utils import log
                log(f"⚠️ [shared_state] kline 읽기 불안정, 캐시 폴백 사용(interval={interval}): {e}")
            except Exception:
                pass
            return _kline_cache.get(interval)

# 시뮬레이션 모드에서 현재 열린 포지션 추적용
simulated_position = {
    "is_open": False,
    "side": None,
    "entry_price": None
}

from typing import Dict, Optional

# 프론트가 보는 "진행 중 캔들" 부분 데이터 캐시
LATEST_CANDLE_PARTIAL: Dict[str, dict] = {}

def update_latest(tf: str, partial: dict) -> None:
    LATEST_CANDLE_PARTIAL[tf] = partial

def get_latest(tf: str) -> Optional[dict]:
    return LATEST_CANDLE_PARTIAL.get(tf)
