import json
import asyncio

from core.state import shared_state
from core.utils.side_utils import normalize_bybit_side
from core.utils.log_utils import log
from core.notifications.position_overlay_notify import notify_position_overlay_update
from core.ws.handlers.store_adapter import (
    safe_float as _safe_float,
    resolve_open_position_key_for_update as _resolve_open_position_key_for_update,
)
from core.utils.qty_step_config import QTY_STEP as _QTY_STEP, floor_to_step_qty as _floor_to_step_qty

# position 스트림 중복 알림 억제용 캐시
# key: (symbol, display_side) -> (size, tp, sl)
_LAST_POSITION_STATE: dict[tuple[str, str], tuple[float, float, float]] = {}


# Bybit position 스트림 -> 실행 캐시와 오버레이 보정용
# 거래소에서 TP/SL을 바꾸면 UI도 같은 값으로 맞추기 위함

async def handle_position_message(ws, message: str):
    try:
        data = json.loads(message)
    except Exception:
        return

    if data.get("topic") != "position":
        return

    dirty = False
    def mark_dirty():
        nonlocal dirty
        dirty = True

    try:
        for pos in data.get("data", []) or []:
            try:
                symbol = pos.get("symbol")
                if not symbol:
                    continue

                # Bybit position.side는 보통 'Buy'/'Sell'
                raw_side = pos.get("side")
                display_side = normalize_bybit_side(raw_side)
                if display_side not in ("Long", "Short"):
                    continue

                size = _safe_float(pos.get("size") or pos.get("positionSize") or pos.get("qty"), 0.0)
                tp = _safe_float(pos.get("takeProfit"), 0.0)
                sl = _safe_float(pos.get("stopLoss"), 0.0)

                # size=0 정리는 execution 스트림에서 담당
                if size <= 0:
                    continue

                key = _resolve_open_position_key_for_update(symbol, display_side)
                if not key:
                    # execution_store가 아직 없는 타이밍이면 스킵
                    continue

                info = shared_state.execution_data_store.get(key)
                if not isinstance(info, dict) or info.get("closed", False):
                    continue

                changed = False

                # current_size 보정용
                cur = _safe_float(info.get("current_size"), 0.0)
                if abs(cur - size) >= float(_QTY_STEP) / 2:
                    info["current_size"] = _floor_to_step_qty(size)
                    changed = True

                # 거래소 평균가가 더 최신이면 보정
                avg = _safe_float(pos.get("avgPrice") or pos.get("entryPrice") or pos.get("avgEntryPrice"), 0.0)
                if avg > 0:
                    ep = _safe_float(info.get("entry_price"), 0.0)
                    if ep <= 0 or abs(ep - avg) / max(avg, 1e-9) > 0.0005:
                        info["entry_price"] = avg
                        changed = True

                # TP/SL 갱신
                strategy = str(info.get("strategy") or "").lower()
                prev_tp = _safe_float(info.get("tp_price") or info.get("tp_full_price"), 0.0)
                # sl_price 의미 분리
                # manual: 거래소 stopLoss 그대로 사용
                # zone_strategy: 내부 기준 SL 유지
                prev_sl = _safe_float(info.get("sl_price"), 0.0)
                prev_ex_sl = _safe_float(info.get("exchange_sl_price"), 0.0)

                if tp > 0 and abs(tp - prev_tp) / max(tp, 1e-9) > 1e-9:
                    info["tp_price"] = tp
                    info["tp_full_price"] = tp  # overlay builder가 tp_full_price 우선이므로 같이 세팅
                    changed = True

                # tp가 0으로 바뀌는(해제) 경우도 반영
                if tp <= 0 and prev_tp > 0:
                    info["tp_price"] = 0.0
                    info["tp_full_price"] = 0.0
                    changed = True

                # 거래소 stopLoss는 별도 필드로도 저장
                if sl > 0 and abs(sl - prev_ex_sl) / max(sl, 1e-9) > 1e-9:
                    info["exchange_sl_price"] = sl
                    info["exchange_sl_available"] = True
                    changed = True

                if sl <= 0 and prev_ex_sl > 0:
                    info["exchange_sl_price"] = 0.0
                    info["exchange_sl_available"] = False
                    changed = True

                # manual 포지션만 sl_price 자체를 거래소 값과 동기화
                if strategy == "manual":
                    if sl > 0 and abs(sl - prev_sl) / max(sl, 1e-9) > 1e-9:
                        info["sl_price"] = sl
                        changed = True
                    if sl <= 0 and prev_sl > 0:
                        info["sl_price"] = 0.0
                        changed = True

                if changed:
                    shared_state.execution_data_store[key] = info
                    mark_dirty()

                    # 변화 감지 캐시 업데이트
                    cache_key = (symbol, display_side)
                    _LAST_POSITION_STATE[cache_key] = (size, tp, sl)

                    # 오버레이 갱신
                    # 거래소 TP/SL이 있으면 우선 반영
                    try:
                        kwargs = {
                            "override_tp_price": (tp if tp > 0 else None),
                        }

                        if sl > 0:
                            kwargs["override_sl_price"] = sl
                            kwargs["override_sl_available"] = True
                        else:
                            # manual은 거래소 SL이 없으면 risk 영역을 숨기는 게 자연스러움
                            if strategy == "manual":
                                kwargs["override_sl_available"] = False
                                kwargs["override_sl_price"] = None
                            # zone_strategy는 내부 기준 sl_price를 유지
                            # stopLoss=0일 때는 override 자체를 생략

                        asyncio.create_task(notify_position_overlay_update(key, **kwargs))
                    except Exception as e:
                        log(f"⚠️ [PositionOverlay] position-stream notify 실패: {e}")

            except Exception:
                continue
    finally:
        if dirty:
            try:
                shared_state.save_execution_data_store(shared_state.execution_data_store)
            except Exception:
                pass
