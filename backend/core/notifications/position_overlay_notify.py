## backend/core/utils/position_overlay_notify.py

import os, asyncio, requests
from datetime import datetime, timezone
from typing import Any
from core.state import shared_state
from core.utils.log_utils import log
from core.utils.time_utils import parse_utc_datetime_str_to_ms


DEFAULT_NOTIFY_URL = "http://127.0.0.1:8000/internal/position-overlay-event"

# 포지션 단위 오버레이 ID 고정용
def _make_position_overlay_id(symbol: str, side_upper: str) -> str:
    return f"pos:{symbol}:{side_upper}"


# 거래소/내부 표기를 LONG/SHORT로 정규화용
def _normalize_side_to_upper(side: str | None) -> str:
    if not side:
        return "?"
    s = str(side).strip().lower()
    if s in ("long", "buy"):
        return "LONG"
    if s in ("short", "sell"):
        return "SHORT"
    # 이미 LONG/SHORT 형태면
    if s in ("long", "short"):
        return s.upper()
    return str(side).upper()


# execution_data_store -> 오버레이 최소 payload 변환용
def build_position_overlay_from_store(
    order_link_id: str,
    *,
    override_sl_price: float | None = None,
    override_sl_available: bool | None = None,
    override_tp_price: float | None = None,
) -> dict[str, Any] | None:
    base = shared_state.execution_data_store.get(order_link_id)
    if not isinstance(base, dict):
        return None

    symbol = base.get("symbol")
    if not symbol:
        return None

    side_upper = _normalize_side_to_upper(
        base.get("side_upper") or base.get("side") or base.get("display_side") or base.get("pos_side")
    )
    if side_upper not in ("LONG", "SHORT"):
        side_upper = str(side_upper).upper()

    overlay_id = _make_position_overlay_id(str(symbol), side_upper)

    # 포지션 단위 레코드 우선 사용용
    info = shared_state.execution_data_store.get(overlay_id)
    if not isinstance(info, dict):
        info = base

    strategy = info.get("strategy") or base.get("strategy")

    # 진입 시각은 overlay 레코드 우선 사용용
    entry_ts_ms = info.get("entry_ts_ms")
    try:
        entry_ts_ms = int(entry_ts_ms) if entry_ts_ms is not None else 0
    except Exception:
        entry_ts_ms = 0
    if entry_ts_ms <= 0:
        parsed = parse_utc_datetime_str_to_ms(info.get("entry_time"), fmt="%Y-%m-%d %H:%M:%S")
        if parsed:
            entry_ts_ms = parsed

    # 진입가는 거래소 평단 우선 사용용
    try:
        entry_price = float(info.get("entry_price") or 0.0)
    except Exception:
        entry_price = 0.0
    if entry_price <= 0:
        return None

    # TP는 거래소 반영값 우선 사용용
    tp_price = None
    if override_tp_price is not None:
        tp_price = override_tp_price
    else:
        tp_price = info.get("tp_full_price") or info.get("tp_price")

    try:
        tp_price = float(tp_price) if tp_price is not None else 0.0
    except Exception:
        tp_price = 0.0
    tp_available = tp_price > 0

    # SL은 거래소 반영값 우선 사용용
    sl_available = False
    if override_sl_available is not None:
        sl_available = bool(override_sl_available)
    else:
        if str(strategy) == "zone_strategy":
            sl_available = bool(info.get("wick_sl_active"))
        else:
            try:
                sl_available = float(info.get("sl_price") or 0.0) > 0
            except Exception:
                sl_available = False

    sl_price: float | None = None

    if sl_available:
        if override_sl_price is not None:
            sl_price = float(override_sl_price)
        else:
            if str(strategy) == "zone_strategy":
                try:
                    v = float(info.get("wick_sl_price") or 0.0)
                    sl_price = v if v > 0 else None
                except Exception:
                    sl_price = None
            else:
                try:
                    v = float(info.get("sl_price") or 0.0)
                    sl_price = v if v > 0 else None
                except Exception:
                    sl_price = None

    overlay = {
        "id": str(overlay_id),
        "symbol": str(symbol),
        "strategy": str(strategy) if strategy else None,
        "side": side_upper,
        "entryTs": int(entry_ts_ms) if entry_ts_ms else int(datetime.now(tz=timezone.utc).timestamp() * 1000),
        "entryPrice": float(entry_price),
        "tpAvailable": bool(tp_available),
        "tpPrice": float(tp_price) if tp_available else None,
        "slAvailable": bool(sl_available),
        "slPrice": float(sl_price) if sl_available else None,
        "closed": bool(info.get("closed", False)),
        "exitTs": None,
    }
    return overlay


async def _post_json(url: str, payload: dict[str, Any], timeout_sec: float = 2.0):
    def _do_post():
        return requests.post(url, json=payload, timeout=timeout_sec)

    try:
        await asyncio.to_thread(_do_post)
    except Exception as e:
        log(f"⚠️ [PositionOverlay] 내부 POST 실패: {e} url={url} payload={payload}")


async def notify_position_overlay_update(
    order_link_id: str,
    *,
    override_sl_price: float | None = None,
    override_sl_available: bool | None = None,
    override_tp_price: float | None = None,
):
    url = os.getenv("POSITION_OVERLAY_NOTIFY_URL", DEFAULT_NOTIFY_URL)
    overlay = build_position_overlay_from_store(
        order_link_id,
        override_sl_price=override_sl_price,
        override_sl_available=override_sl_available,
        override_tp_price=override_tp_price,
    )
    if not overlay:
        return

    payload = {"action": "update", "overlay": overlay}
    await _post_json(url, payload)


async def notify_position_overlay_clear(order_link_id: str, *, exit_ts_ms: int | None = None):
    url = os.getenv("POSITION_OVERLAY_NOTIFY_URL", DEFAULT_NOTIFY_URL)
    overlay = build_position_overlay_from_store(str(order_link_id))
    overlay_id = str(overlay.get("id")) if overlay else str(order_link_id)

    payload = {
        "action": "clear",
        "id": overlay_id,
        "exitTs": int(exit_ts_ms) if exit_ts_ms is not None else None,
    }
    await _post_json(url, payload)
