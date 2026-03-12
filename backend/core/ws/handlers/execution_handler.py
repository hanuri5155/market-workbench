# Bybit execution 스트림을 내부 포지션 상태로 해석하는 핵심 핸들러
#
# 이 파일을 읽을 때 먼저 보면 되는 흐름
# 1) 체결 1건을 open, tp, sl, manual close 중 어디로 볼지 분류
# 2) execution_data_store와 position_fills를 같은 기준으로 갱신
# 3) Zone 상태, 포지션 오버레이, 최종 알림을 필요한 시점에만 갱신

import asyncio
import json
import os
import time
from datetime import datetime, timezone

from core.state import shared_state
from core.utils.time_utils import utc_ms_to_compact_str, parse_utc_compact_str_to_dt
from core.utils.side_utils import normalize_bybit_side
from core.ws.handlers.store_adapter import (
    manual_position_key as _manual_position_key,
    safe_float as _safe_float,
    resolve_position_key_for_close as _resolve_position_key_for_close_impl,
)
from core.ws.handlers.execution_common import (
    estimate_open_fee,
    format_strategy_name,
    send_final_position_alert,
)
from core.ws.handlers.execution_funding import handle_funding_execution
from core.ws.handlers.execution_zone_hooks import (
    _notify_zone_state_sync,
    _finalize_zone_after_debounce,
)
from core.trading.execution_store_ops import (
    recalc_current_size_from_fills as _recalc_current_size_from_fills_impl,
)
from core.utils.tp_utils import format_signed_4f_with_comma, format_4f, format_1f_with_comma, format_4f_with_comma
from core.notifications.alert_utils import send_positions_telegram_alert
from core.utils.log_utils import log
from core.notifications.position_overlay_notify import notify_position_overlay_update, notify_position_overlay_clear
from core.utils.qty_step_config import QTY_STEP as _QTY_STEP, floor_to_step_qty as _floor_to_step_qty
from core.persistence.positions_repo import (
    insert_fill_by_order_link_id,
    finalize_position_close_by_order_link_id,
    upsert_entry_and_add_fee,
)
from core.persistence.zone_state_repo import (
    fetch_zone_base_sl_by_key,
    deactivate_zone_state_by_key,
)
from core.utils.zone_ids import (
    is_zone_order_link_id,
    zone_parent_from_order_link_id,
    parse_zone_order_link_id,
    parse_zone_parent_order_link_id,
)

# 진입 직후 TP/Zone 후처리를 짧게 늦춰 한 번만 실행하기 위한 태스크 저장소
_finalize_tasks = {}
_EPS = 1e-9
_ZONE_REENTRY_GUARD_SEC = float(os.getenv("ZONE_REENTRY_GUARD_SEC", "180"))
_EXECUTION_RAW_LOG = str(os.getenv("EXECUTION_RAW_LOG", "0")).strip().lower() in ("1", "true", "yes", "on")

def _recalc_current_size_from_fills(info: dict) -> float:
    return _recalc_current_size_from_fills_impl(info, floor_qty=_floor_to_step_qty)

def _cancel_finalize_task(key: str):
    t = _finalize_tasks.pop(key, None)
    if t and not t.done():
        t.cancel()


# 표시용 side 문자열(Long/Short)을 내부 비교용 LONG/SHORT로 맞추기 위함
def _zone_side_up_from_display(pos_side: str | None) -> str | None:
    if pos_side == "Long":
        return "LONG"
    if pos_side == "Short":
        return "SHORT"
    return None


# orderLinkId만 보고도 Zone 전략 주문인지 빠르게 판별하기 위함
def _is_zone_strategy_key(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    return (
        is_zone_order_link_id(value)
        or "zone_strategy" in value
        or "demo_zone" in value
        or value.startswith("zonebox|")
    )


# 체결 키에서 Zone 부모 주문 키와 파싱된 문맥 정보를 복구하기 위함
def _resolve_zone_parent_and_meta(
    position_key: str | None,
    used_key: str | None,
    info: dict | None,
) -> tuple[str | None, dict | None]:
    parent_olid = None
    try:
        if isinstance(info, dict):
            parent_olid = info.get("parent_order_link_id")
    except Exception:
        parent_olid = None

    if not parent_olid and isinstance(position_key, str):
        parent_olid = zone_parent_from_order_link_id(position_key)

    if not parent_olid and isinstance(position_key, str):
        parsed_pos = parse_zone_order_link_id(position_key)
        if parsed_pos:
            parent_olid = parsed_pos.get("parent_order_link_id")

    if not parent_olid and isinstance(used_key, str):
        parsed_used = parse_zone_order_link_id(used_key)
        if parsed_used:
            parent_olid = parsed_used.get("parent_order_link_id")

    meta = parse_zone_parent_order_link_id(parent_olid) if parent_olid else None
    return parent_olid, meta


# Zone 종료 직후 같은 자리 재진입을 잠시 막기 위한 guard를 설정하기 위함
def _arm_zone_reentry_guard_for_exit(
    *,
    position_key: str | None,
    used_key: str | None,
    info: dict | None,
    symbol: str | None,
    pos_side: str | None = None,
    reason: str = "",
) -> tuple[str | None, dict | None]:
    parent_olid, meta = _resolve_zone_parent_and_meta(position_key, used_key, info)

    side_up = None
    guard_symbol = None
    if isinstance(meta, dict):
        side_up = meta.get("side")
        guard_symbol = meta.get("symbol")

    if side_up not in ("LONG", "SHORT"):
        display_side = info.get("display_side") if isinstance(info, dict) else None
        side_up = _zone_side_up_from_display(display_side or pos_side)

    if not guard_symbol and isinstance(info, dict):
        guard_symbol = info.get("symbol")
    if not guard_symbol:
        guard_symbol = symbol

    if parent_olid or (guard_symbol and side_up in ("LONG", "SHORT")):
        _set_zone_reentry_guard(
            parent_olid=parent_olid,
            symbol=guard_symbol,
            side_up=side_up,
            reason=reason,
        )

    return parent_olid, meta


def _set_zone_reentry_guard(
    *,
    parent_olid: str | None = None,
    symbol: str | None = None,
    side_up: str | None = None,
    reason: str = "",
):
    # 같은 Zone이 종료 직후 다시 켜지는 루프를 막기 위한 메모리 가드
    try:
        ttl = max(float(_ZONE_REENTRY_GUARD_SEC), 1.0)
        until = time.time() + ttl

        parent_map = getattr(shared_state, "zone_parent_block_until", None)
        if not isinstance(parent_map, dict):
            parent_map = {}
            shared_state.zone_parent_block_until = parent_map

        side_map = getattr(shared_state, "zone_side_block_until", None)
        if not isinstance(side_map, dict):
            side_map = {}
            shared_state.zone_side_block_until = side_map

        if parent_olid:
            parent_map[str(parent_olid)] = until

        if symbol and side_up in ("LONG", "SHORT"):
            side_key = f"{str(symbol)}|{side_up}"
            side_map[side_key] = until

        log(
            f"🛡️ [Structure Zone] 재진입 가드 설정: reason={reason or 'unknown'} "
            f"parent={parent_olid} symbol={symbol} side={side_up} ttl={int(ttl)}s"
        )
    except Exception as e:
        log(f"⚠️ [Structure Zone] 재진입 가드 설정 실패: {e}")


# 종료 주문 키가 비어도 열린 Zone 포지션이 1건뿐이면 그 키를 복구하기 위함
def _recover_zone_position_key(symbol: str, pos_side: str) -> tuple[str | None, str]:
    try:
        candidates: list[str] = []
        for k, v in list(shared_state.execution_data_store.items()):
            if not isinstance(v, dict):
                continue
            if v.get("closed"):
                continue
            if str(v.get("strategy") or "").lower() != "zone_strategy":
                continue
            if str(v.get("symbol") or "") != str(symbol or ""):
                continue
            if str(v.get("display_side") or "") != str(pos_side or ""):
                continue
            if _safe_float(v.get("current_size"), 0.0) <= 0:
                continue
            candidates.append(str(k))

        if len(candidates) == 1:
            return candidates[0], "unique_open_zone_candidate"
        if len(candidates) >= 2:
            return None, f"ambiguous_open_zone_candidates:{len(candidates)}"
        return None, "no_open_zone_candidate"
    except Exception as e:
        return None, f"recover_exception:{e}"

def _resolve_position_key_for_close(symbol: str, pos_side: str, used_key: str | None):
    return _resolve_position_key_for_close_impl(
        symbol,
        pos_side,
        used_key,
    )


# execution 메시지 한 묶음을 읽고 체결 1건씩 내부 상태에 반영하기 위함
async def handle_execution_message(ws, message: str):
    data = json.loads(message)
    if data.get("topic") != "execution":
        return
    
    for exec_data in data.get("data", []):
        dirty = False

        # execution_data_store를 실제로 바꾼 체결만 파일로 저장하기 위함
        def mark_dirty():
            nonlocal dirty
            dirty = True

        def save_if_dirty():
            nonlocal dirty
            if dirty:
                shared_state.save_execution_data_store(shared_state.execution_data_store)
                dirty = False
        try:
            if _EXECUTION_RAW_LOG:
                log(
                    f"🧾 [Bybit 체결 데이터 수신] orderId={exec_data.get('orderId')} "
                    f"→ 전체: {json.dumps(exec_data, indent=2, ensure_ascii=False)}"
                )
            execPnl = float(exec_data.get("execPnl", 0))
            exec_fee = float(exec_data.get("execFee", 0))
            exec_qty = float(exec_data.get("execQty", 0))
            order_qty  = float(exec_data.get("orderQty", 0) or 0)
            leaves_qty = float(exec_data.get("leavesQty", 0) or 0)
            exec_price = float(exec_data.get("execPrice", 0))
            formatted_exec = "{:,}".format(exec_price)
            exec_value = float(exec_data.get("execValue", 0))
            formatted_value = "{:,}".format(exec_value)
            exec_time_ms = exec_data.get("execTime", 0)
            exec_time = utc_ms_to_compact_str(exec_time_ms) or ""
            open_fee_rate = float(exec_data.get("feeRate", 0))
            stop_order_type = exec_data.get("stopOrderType", "")
            side = exec_data.get("side")
            display_side = normalize_bybit_side(side) or side
            symbol = exec_data.get("symbol")
            exec_type = exec_data.get("execType", "")
            order_type = exec_data.get("orderType", "")
            create_type = exec_data.get("createType", "")
            is_maker = bool(exec_data.get("isMaker", False))
            order_link_id = exec_data.get("orderLinkId")
            order_id = exec_data.get("orderId")
            # orderLinkId가 없으면 orderId를 대체 키로 써 수동 주문도 같은 흐름으로 처리
            used_key = order_link_id or order_id
            closed_size = exec_data.get("closedSize")
            # "0.000" 같은 문자열도 안전하게 비교하기 위해 float로 정규화
            try:
                closed_size_f = float(closed_size or 0)
            except (TypeError, ValueError):
                closed_size_f = 0.0

            if not used_key:
                log("⚠️ orderLinkId와 orderId 모두 없음 → 체결 정보 스킵")
                continue
            
            is_normal_stop = stop_order_type in ("", "UNKNOWN", None)
            is_open_position = (
                used_key
                and closed_size_f == 0.0
                and execPnl == 0
                and exec_qty > 0.0   
                and is_normal_stop
                and exec_type != "Funding"
            )

            is_close_position = (
                used_key
                and closed_size_f > 0.0
                and exec_qty > 0.0
                and is_normal_stop
                and exec_type != "Funding"
            )


            # open 체결은 진입가, 수량, 포지션 오버레이 시작점 계산 기준
            if is_open_position:
                # 공개 버전에서는 Zone 전략과 manual 두 분류만 유지
                strategy = "zone_strategy" if _is_zone_strategy_key(used_key) else "manual"

                # 수동 주문은 orderId가 달라도 side별 한 오버레이로 합쳐 보여주기 위함
                raw_used_key = used_key
                if strategy == "manual":
                    mk = _manual_position_key(symbol, display_side)
                    used_key = mk

                    # 원본 orderId는 추적용 목록으로만 남기고 UI key는 side 단위로 통일
                    try:
                        if used_key in shared_state.execution_data_store and isinstance(shared_state.execution_data_store[used_key], dict):
                            ids = shared_state.execution_data_store[used_key].get("manual_source_order_ids")
                            if not isinstance(ids, list):
                                ids = []
                            if raw_used_key and raw_used_key not in ids:
                                ids.append(str(raw_used_key))
                            shared_state.execution_data_store[used_key]["manual_source_order_ids"] = ids
                            mark_dirty()
                    except Exception:
                        pass

                # 같은 포지션에 추가 진입된 체결이면 평균가와 수량을 누적
                existing = shared_state.execution_data_store.get(used_key)
                if existing and not existing.get("closed", False):
                    old_qty = float(existing.get("entry_size", 0.0))
                    new_qty = _floor_to_step_qty(old_qty + exec_qty)

                    new_exec_value = exec_price * exec_qty
                    old_value = float(existing.get("exec_value", 0.0))
                    old_fee   = float(existing.get("open_fee", 0.0))

                    new_value = old_value + new_exec_value
                    new_fee   = old_fee + exec_fee

                    # 추가 진입이 들어와도 평균 진입가가 맞게 보이도록 가중 평균 사용
                    prev_price = float(existing.get("entry_price", exec_price))
                    wavg_price = (prev_price * old_qty + exec_price * exec_qty) / (new_qty if new_qty > 0 else 1)

                    existing["entry_price"]  = wavg_price
                    existing["entry_size"]   = new_qty
                    existing["current_size"] = _floor_to_step_qty(float(existing.get("current_size", 0.0)) + exec_qty)
                    existing["open_fee"]     = new_fee
                    existing["exec_value"]   = new_value
                    existing["entry_time"]   = existing.get("entry_time", exec_time)
                    existing["open_fee_rate"]= (new_fee / new_value) if new_value else open_fee_rate 
                    mark_dirty()

                    # 차트와 알림에서 같은 레버리지를 보도록 side 기준 값을 저장
                    cfg = shared_state.current_config or {}
                    lev_raw = cfg.get("buy_leverage") if side == "Buy" else cfg.get("sell_leverage")
                    lev = float(lev_raw) if lev_raw is not None else None

                    # positions 테이블은 체결 delta를 계속 누적하는 방식으로 관리
                    try:
                        entry_time_dt = parse_utc_compact_str_to_dt(exec_time)
                        parent_olid = zone_parent_from_order_link_id(used_key) if strategy == "zone_strategy" else None
                        upsert_entry_and_add_fee(
                            account_id=getattr(shared_state, "account_id", 1),
                            session_id=getattr(shared_state, "session_id", None),
                            symbol=symbol,
                            strategy=strategy,
                            side=display_side,
                            order_link_id=used_key,
                            parent_order_link_id=parent_olid,
                            entry_price=exec_price,
                            entry_qty=exec_qty,
                            leverage=lev,
                            tp_partition=None,
                            sl_price=shared_state.execution_data_store.get(used_key, {}).get("sl_price"),
                            delta_fee_open=exec_fee,
                            entry_time_utc=entry_time_dt
                        )
                    except Exception as e:
                        log(f"⚠️ [DB] upsert_entry_and_add_fee 실패: {e}")

                    # 디바운스 재스케줄 (TP는 한 번만)
                    if existing.get("strategy") == "zone_strategy":
                        task = _finalize_tasks.get(used_key)
                        if task and not task.done():
                            task.cancel()
                        _finalize_tasks[used_key] = asyncio.create_task(
                            _finalize_zone_after_debounce(used_key, symbol, side)
                        )
                    save_if_dirty()
                    # 부분 체결은 알림과 TP 후처리 생략
                    continue
                
                # 3) 최초 체결이면 신규 레코드 생성 + 디바운스 TP 예약
                # execution_data_store는 orderLinkId 기준으로 저장
                parent_olid = zone_parent_from_order_link_id(used_key) if strategy == "zone_strategy" else None
                
                # zone 주문이면 키 메타를 파싱해 base_sl을 같이 저장
                zone_interval_min = None
                zone_base_sl = None
                if strategy == "zone_strategy" and is_zone_order_link_id(used_key):
                    try:
                        zone_meta = parse_zone_order_link_id(used_key)
                        if zone_meta:
                            zone_interval_min = zone_meta["interval_min"]
                            zone_base_sl = fetch_zone_base_sl_by_key(
                                symbol=zone_meta["symbol"],
                                interval_min=zone_meta["interval_min"],
                                start_ms=zone_meta["start_ts"],
                                side=zone_meta["side"],
                            )
                    except Exception as e:
                        log(f"⚠️ [Structure Zone] 기본 SL 조회 실패: {e}")
                
                shared_state.execution_data_store[used_key] = {
                    "entry_price": exec_price,
                    "side": side,
                    "display_side": display_side,
                    "symbol": symbol,
                    "strategy": strategy,
                    "exec_value": exec_value,
                    "entry_size": exec_qty,
                    "current_size": exec_qty,
                    "open_fee": exec_fee,
                    "entry_time": exec_time,
                    "entry_ts_ms": int(exec_data.get("execTime") or 0),
                    "overlay_cleared": False,
                    "open_fee_rate": (exec_fee / exec_value) if exec_value else open_fee_rate,
                    "tp_configured": False,
                    "parent_order_link_id": parent_olid,
                    "interval_min": zone_interval_min,
                    "sl_price": zone_base_sl,
                }
                mark_dirty()
                log(f"🚀 [포지션 진입 감지] orderLinkId={used_key}, strategy={strategy}")
                
                # side 기준 레버리지 반영
                cfg = shared_state.current_config or {}
                lev_raw = cfg.get("buy_leverage") if side == "Buy" else cfg.get("sell_leverage")
                lev = float(lev_raw) if lev_raw is not None else None

                # DB: 이번 체결(delta)을 positions에 누적 업서트 (최초도 동일 루틴)
                try:
                    entry_time_dt = parse_utc_compact_str_to_dt(exec_time)
                    parent_olid = zone_parent_from_order_link_id(used_key) if strategy == "zone_strategy" else None
                    upsert_entry_and_add_fee(
                        account_id=getattr(shared_state, "account_id", 1),
                        session_id=getattr(shared_state, "session_id", None),
                        symbol=symbol,
                        strategy=strategy,
                        side=display_side,
                        order_link_id=used_key,
                        parent_order_link_id=parent_olid,
                        entry_price=exec_price,
                        entry_qty=exec_qty,
                        leverage=lev,
                        tp_partition=None,
                        sl_price=shared_state.execution_data_store[used_key].get("sl_price"),
                        delta_fee_open=exec_fee,
                        entry_time_utc=entry_time_dt
                    )
                except Exception as e:
                    log(f"⚠️ [DB] upsert_entry_and_add_fee 실패: {e}")

                shared_state.last_execution_order_id = used_key
                shared_state.current_position_link_id = used_key
                mark_dirty()
                save_if_dirty()

                #  포지션 오버레이: 진입 감지 즉시 update 통지(Entry/TP 즉시 렌더용)
                try:
                    asyncio.create_task(notify_position_overlay_update(used_key))
                except Exception as e:
                    log(f"⚠️ [PositionOverlay] 진입 update notify 실패: {e}")

                if strategy == "zone_strategy":
                    _finalize_tasks[used_key] = asyncio.create_task(
                        _finalize_zone_after_debounce(used_key, symbol, side)
                    )
                
                strategy_label = format_strategy_name(strategy)
                # 알림에 표시할 수량은 orderQty 우선, 없으면 execQty
                qty_for_alert = order_qty if order_qty > 0 else exec_qty

                alert_msg = (
                    f"<b>🚀 {strategy_label}</b>\n"
                    f"<b>{symbol}</b> <code>{display_side}</code>\n\n"
                    f"<b>Open Value:</b> <code>{formatted_value} USD</code>\n"
                    f"<b>Quantity:</b> <code>{qty_for_alert} BTC</code>\n"
                    f"<b>Entry Price:</b> <code>{formatted_exec}</code>"
                )
                send_positions_telegram_alert(alert_msg, parse_mode="HTML")

            # 시장가 종료 체결 감지
            if is_close_position:
                # =========================[TP(Limit/Maker) 분류]=========================
                # 워처가 낸 reduce-only 시장가 TP는 stopOrderType이 비어 옴
                # orderLinkId 태그 기준으로 TP 분류
                is_tp_market = isinstance(used_key, str) and "__tp_mkt__" in used_key
                if is_tp_market:
                    pos_side = "Short" if side == "Buy" else "Long"
                    position_key = _resolve_position_key_for_close(symbol, pos_side, used_key)
                    if not position_key:
                        log(f"❌ [TP(Market) 처리 중단] 대상 포지션 없음: symbol={symbol}, pos_side={pos_side}, key={used_key}")
                        continue
                    if position_key not in shared_state.execution_data_store:
                        log(f"❌ [TP(Market) 처리 중단] execution_data_store에 '{position_key}' 정보 없음")
                        continue

                    filled_fills = shared_state.execution_data_store[position_key].setdefault("filled_fills", [])
                    position_fills = shared_state.execution_data_store[position_key].setdefault("position_fills", {})

                    label = f"TP_{exec_time}"

                    # 수수료/손익
                    entry_price = shared_state.execution_data_store[position_key].get("entry_price")
                    open_fee_rate = shared_state.execution_data_store[position_key].get("open_fee_rate")
                    open_fee = estimate_open_fee(entry_price, exec_qty, open_fee_rate)
                    close_fee = exec_fee
                    total_fee = open_fee + close_fee
                    pnl = format_signed_4f_with_comma(execPnl - total_fee)

                    log(f"✨ [TP(Market) 감지] {position_key} → {label} @ {exec_price} → pnl={pnl}, open_fee={format_4f(open_fee)}, close_fee={format_4f(close_fee)}")

                    # 체결 저장
                    filled_fills.append(label)
                    position_fills[label] = {
                        "pnl": execPnl,
                        "close_fee": close_fee,
                        "price": exec_price,
                        "qty": exec_qty,
                        "fill_time": exec_time
                    }
                    # 최근 TP 시각 기록(잔량 보정 디바운스에 활용)
                    shared_state.last_tp_fill_at[position_key] = time.time()

                    # TP fill을 position_fills에 이미 저장했으므로
                    # recalc는 '이번 TP 반영 후 잔량'이다. 여기서 exec_qty를 또 빼면 2번 차감됨
                    pinfo = shared_state.execution_data_store[position_key]
                    stored = float(pinfo.get("current_size", 0.0))
                    recalc_after = _recalc_current_size_from_fills(pinfo)

                    # 역순/지연 체결이 있어도 더 진행된 잔량만 반영
                    patched = min(stored, recalc_after)

                    if abs(patched - stored) >= float(_QTY_STEP) / 2:
                        log(f"♻️ [QTY-RECON] TP(Market): stored={stored}, recalc_after={recalc_after} → apply={patched}")

                    pinfo["current_size"] = max(patched, 0.0)
                    mark_dirty()
                    save_if_dirty()

                    # 알림
                    strategy = shared_state.execution_data_store[position_key].get("strategy", "unknown")
                    strategy_label = format_strategy_name(strategy)
                    exit_price = format_1f_with_comma(exec_price)
                    alert_msg = (
                        f"<b>✨ {strategy_label}</b>\n"
                        f"<code>{label}</code>\n\n"
                        f"<b>Closed P&amp;L:</b> <code>{pnl} USD</code>\n"
                        f"<b>Exit Price:</b> <code>{exit_price}</code>\n"
                        f"<b>Quantity:</b> <code>{exec_qty} BTC</code>\n"
                        f"<b>Fee:</b> <code>{format_4f_with_comma(total_fee)} USD</code>"
                    )
                    send_positions_telegram_alert(alert_msg, parse_mode="HTML")

                    # DB: TP fill
                    try:
                        insert_fill_by_order_link_id(
                            position_key,
                            fill_time_utc=parse_utc_compact_str_to_dt(exec_time),
                            price=exec_price,
                            qty=exec_qty,
                            pnl_gross=execPnl,
                            fee=close_fee,
                            fill_type="TP",
                            stage_code=None
                        )
                    except Exception as e:
                        log(f"⚠️ [DB] TP(Market) fill 기록 실패: {e}")

                    # 전량 종료 시 마무리
                    if shared_state.execution_data_store[position_key]["current_size"] <= _EPS:
                        shared_state.execution_data_store[position_key]["closed"] = True
                        shared_state.execution_data_store[position_key]["exit_time"] = exec_time
                        #  포지션 오버레이: 종료 감지 즉시 clear 통지
                        try:
                            pinfo = shared_state.execution_data_store.get(position_key, {})
                            if isinstance(pinfo, dict) and not pinfo.get("overlay_cleared"):
                                pinfo["overlay_cleared"] = True
                                shared_state.execution_data_store[position_key] = pinfo
                                asyncio.create_task(notify_position_overlay_clear(position_key, exit_ts_ms=int(exec_data.get("execTime") or 0)))
                        except Exception as e:
                            log(f"⚠️ [PositionOverlay] clear notify 실패: {e}")

                        if strategy == "zone_strategy":
                            # TP로 전량 종료된 경우 박스는 유지(재진입 허용)
                            # wick SL 플래그는 exec_store에서 정리
                            try:
                                pinfo = shared_state.execution_data_store.get(position_key, {})
                                if isinstance(pinfo, dict):
                                    pinfo["last_exit_type"] = "TP"
                                    pinfo["last_exit_time"] = datetime.now(timezone.utc).isoformat()
                                    pinfo.pop("wick_sl_active", None)
                                    pinfo.pop("wick_sl_cid", None)
                                    pinfo.pop("wick_sl_price", None)
                                    shared_state.execution_data_store[position_key] = pinfo
                            except Exception as e:
                                log(f"⚠️ [Structure Zone] TP 후 실행 캐시 정리 실패: {e}")

                        try:
                            finalize_position_close_by_order_link_id(
                                position_key,
                                exit_time_utc=parse_utc_compact_str_to_dt(exec_time)
                            )
                        except Exception as e:
                            log(f"⚠️ [DB] positions 종료 업데이트 실패: {e}")
                        mark_dirty()
                        save_if_dirty()
                        send_final_position_alert(exec_price, position_key=position_key)
                        _cancel_finalize_task(position_key)

                    # EXIT 분기로 내려가지 않도록 종료
                    continue

                # ========================[/TP(Limit/Maker) 분류]=========================

                # HedgeMode 청산 체결 side 해석용
                # Buy면 Short 감소, Sell이면 Long 감소
                pos_side = "Short" if side == "Buy" else "Long"
                # TP/SL과 동일한 매핑 헬퍼 사용
                position_key = _resolve_position_key_for_close(symbol, pos_side, used_key)

                if not position_key:
                    # zone 종료 체결은 열린 후보 1건일 때만 복구 허용
                    is_zone_close = _is_zone_strategy_key(used_key)
                    if is_zone_close:
                        recovered_key, reason = _recover_zone_position_key(symbol, pos_side)
                        if recovered_key:
                            position_key = recovered_key
                            log(
                                f"✅ [Structure Zone] POSITION_KEY_RECOVERY_OK: "
                                f"symbol={symbol}, pos_side={pos_side}, recovered={recovered_key}, reason={reason}"
                            )
                        else:
                            side_up = _zone_side_up_from_display(pos_side)
                            _set_zone_reentry_guard(
                                symbol=symbol,
                                side_up=side_up,
                                reason=f"position_key_unresolved:{reason}",
                            )
                            log(
                                f"⚠️ [Structure Zone] POSITION_KEY_RECOVERY_FAIL: "
                                f"symbol={symbol}, pos_side={pos_side}, used_key={used_key}, reason={reason}"
                            )
                    log(f"❌ [포지션 종료 처리 중단] 대상 포지션을 찾을 수 없음 (pos_side={pos_side}, symbol={symbol}, used_key={used_key})")
                    should_send_final_alert = False
                    continue  # ← return(핸들러 종료) 금지, 이번 체결만 스킵

                # 종료 판단은 '진입 기준' 전략으로
                entry_info = shared_state.execution_data_store.get(position_key, {})
                entry_strategy = (entry_info.get("strategy") or "manual").lower()
                # 종료 주문 키가 전략 전용 키가 아니면 수동 종료로 분류
                closing_key = used_key or ""
                manual_exit = not _is_zone_strategy_key(closing_key)
                
                # 현재 수량 반영 (부분 청산 고려) — 감산 전 리컨실 수행
                info = shared_state.execution_data_store[position_key]
                stored_cur = float(info.get("current_size", 0.0))
                recalc_cur = _recalc_current_size_from_fills(info)

                # 한 스텝(0.001) 절반 이상 차이나면 리컨실 적용
                if abs(recalc_cur - stored_cur) >= float(_QTY_STEP) / 2:
                    # 역순/지연 체결 대비: '더 진행된 쪽(더 작은 값)'을 채택
                    patched = min(stored_cur, recalc_cur)
                    log(f"♻️ [QTY-RECON] mismatch detected: stored={stored_cur}, recalc={recalc_cur} → apply={patched}")
                    stored_cur = patched
                    info["current_size"] = patched

                cur = stored_cur
                new_cur = _floor_to_step_qty(cur - exec_qty)
                info["current_size"] = max(new_cur, 0.0)

                log(f"🧭 [Exit-Classifier] classification={'Manual_Exit' if manual_exit else f'{format_strategy_name(entry_strategy)}_Exit'}, "
                    f"stopOrderType={stop_order_type}, createType={create_type}, isMaker={is_maker}, orderLinkId={order_link_id}, usedKey={used_key}, "
                    f"pos_side={'Short' if side=='Buy' else 'Long'}, qty_left_before={cur}, exec_qty={exec_qty}, qty_left_after={new_cur}")
                
                filled_fills = shared_state.execution_data_store[position_key].setdefault("filled_fills", [])
                position_fills = shared_state.execution_data_store[position_key].setdefault("position_fills", {})

                fully_closed = new_cur <= 0.0 + _EPS
                if fully_closed:
                    label = f"{entry_strategy.title()}_Exit" if not manual_exit else "Manual_Exit"
                else:
                    # 실행시각을 붙여 유니크 키 보장
                    label = f"Manual_Reduce_{exec_time}"

                # 진입가를 이용한 진입 수수료 계산
                entry_price = shared_state.execution_data_store[position_key].get("entry_price")
                open_fee_rate = shared_state.execution_data_store[position_key].get("open_fee_rate")
                open_fee = estimate_open_fee(entry_price, exec_qty, open_fee_rate)
                close_fee = exec_fee
                total_fee = open_fee + close_fee

                filled_fills.append(label)
                position_fills[label] = {
                    "pnl": execPnl,
                    "close_fee": close_fee,
                    "price": exec_price,
                    "qty": exec_qty,
                    "fill_time": exec_time
                }

                # 수동 부분청산 알림 (Manual Reduce)
                if manual_exit and not fully_closed:
                    strategy = shared_state.execution_data_store[position_key].get("strategy", "unknown")
                    strategy_label = format_strategy_name(strategy)
                    pnl_txt = format_signed_4f_with_comma(execPnl - total_fee)
                    exit_price_txt = format_1f_with_comma(exec_price)
                    alert_msg = (
                        f"<b>✨ {strategy_label}</b>\n"
                        f"<code>TP_Manual Reduce</code>\n\n"
                        f"<b>Closed P&amp;L:</b> <code>{pnl_txt} USD</code>\n"
                        f"<b>Exit Price:</b> <code>{exit_price_txt}</code>\n"
                        f"<b>Quantity:</b> <code>{exec_qty} BTC</code>\n"
                        f"<b>Fee:</b> <code>{format_4f_with_comma(total_fee)} USD</code>"
                    )
                    send_positions_telegram_alert(alert_msg, parse_mode="HTML")

                # zone 상태 갱신은 완전 청산일 때만 수행
                if fully_closed and entry_strategy == "zone_strategy":
                    try:
                        # entry 시점에 exec_store에 저장된 base_sl (없으면 0)
                        sl = float(entry_info.get("sl_price") or 0.0)
                        ds = entry_info.get("display_side")
                        is_long = (ds == "Long")
                    
                        # 종료 체결가가 SL 바깥인가?
                        out_of_range = (
                            (is_long and sl > 0 and exec_price < sl) or
                            ((not is_long) and sl > 0 and exec_price > sl)
                        )
                        
                        entry_info["last_exit_type"] = "ManualSL" if out_of_range and manual_exit else ("Manual" if manual_exit else "Market")
                        entry_info["last_exit_time"] = datetime.now(timezone.utc).isoformat()

                        if out_of_range and sl > 0:
                            # 동적 손절(시장가 청산) 경로는 stopOrderType=UNKNOWN으로 들어옴
                            # 여기서 zone 비활성화 -> 즉시 재진입 루프 차단
                            parent_olid, meta = _arm_zone_reentry_guard_for_exit(
                                position_key=position_key,
                                used_key=used_key,
                                info=entry_info,
                                symbol=symbol,
                                reason="market_sl_fill_start",
                            )
                            if not meta:
                                log(f"⚠️ [Structure Zone] 시장가 SL 종료 zone 비활성화 스킵: parent_olid={parent_olid}")
                            else:
                                ok = deactivate_zone_state_by_key(
                                    symbol=meta["symbol"],
                                    interval_min=meta["interval_min"],
                                    start_ms=meta["start_ts"],
                                    side=meta["side"],
                                )
                                if ok is True:
                                    log(f"✅ [Structure Zone] 시장가 SL 종료 zone 비활성화 완료: {meta}")
                                elif ok is False:
                                    # 대상 없음: 이미 broken/inactive 처리된 no-op 가능성 높음
                                    log(f"ℹ️ [Structure Zone] 시장가 SL 종료 zone 비활성화 대상 없음(no-op): {meta}")
                                else:
                                    # DB 예외로 판정된 경우에만 fail-safe 가드 강화
                                    _set_zone_reentry_guard(
                                        parent_olid=parent_olid,
                                        symbol=meta["symbol"],
                                        side_up=meta["side"],
                                        reason="deactivate_db_error",
                                    )
                                    log(f"⚠️ [Structure Zone] 시장가 SL 종료 zone 비활성화 실패(DB): {meta}")
                                shared_state.zone_levels_force_refresh = True
                                try:
                                    asyncio.create_task(
                                        _notify_zone_state_sync(meta["symbol"], meta["interval_min"])
                                    )
                                except Exception as e:
                                    log(f"⚠️ [Structure Zone] state-sync notify task 실패: {e}")
                            entry_info["last_exit_reason"] = "exit_out_of_range"
                        else:                
                            # SL 안쪽에서의 종료(수동 익절 포함)는 박스를 유지하고 wick SL 플래그만 정리
                            entry_info.pop("wick_sl_active", None)
                            entry_info.pop("wick_sl_cid", None)
                            entry_info.pop("wick_sl_price", None)
                            entry_info["last_exit_reason"] = "manual_exit_in_range" if manual_exit else "auto_exit_in_range"

                        shared_state.execution_data_store[position_key] = entry_info
                        mark_dirty()
                    except Exception as e:
                        log(f"⚠️ [Structure Zone] zone 상태(DB) 갱신 실패: {e}")
                
                # DB: EXIT/TP fill 삽입 (수동 부분익절을 'TP'로 집계하려면 이곳에서 분기)
                try:
                    fill_type = "EXIT"
                    if manual_exit and not fully_closed and (execPnl - total_fee) >= 0:
                        fill_type = "TP"  # 수익인 수동 부분청산은 부분익절로 집계
                    insert_fill_by_order_link_id(
                        position_key,
                        fill_time_utc=parse_utc_compact_str_to_dt(exec_time),
                        price=exec_price,
                        qty=exec_qty,
                        pnl_gross=execPnl,
                        fee=close_fee,
                        fill_type=fill_type,
                        stage_code=None
                    )
                    # TP로 취급된 경우 dust-보정 디바운스를 동일하게 적용
                    if fill_type == "TP":
                        shared_state.last_tp_fill_at[position_key] = time.time()
                except Exception as e:
                    log(f"⚠️ [DB] EXIT fill 기록 실패: {e}")

                # DB: 포지션 종료 집계 업데이트는 전량 청산시에만
                if fully_closed:
                    shared_state.execution_data_store[position_key]["closed"] = True
                    shared_state.execution_data_store[position_key]["exit_time"] = exec_time

                    #  포지션 오버레이: 종료 감지 즉시 clear 통지
                    try:
                        pinfo = shared_state.execution_data_store.get(position_key, {})
                        if isinstance(pinfo, dict) and not pinfo.get("overlay_cleared"):
                            pinfo["overlay_cleared"] = True
                            shared_state.execution_data_store[position_key] = pinfo
                            asyncio.create_task(notify_position_overlay_clear(position_key, exit_ts_ms=int(exec_data.get("execTime") or 0)))
                    except Exception as e:
                        log(f"⚠️ [PositionOverlay] clear notify 실패: {e}")

                    try:
                        finalize_position_close_by_order_link_id(
                            position_key,
                            exit_time_utc=parse_utc_compact_str_to_dt(exec_time)
                        )
                    except Exception as e:
                        log(f"⚠️ [DB] positions 종료 업데이트 실패: {e}")

                    _cancel_finalize_task(position_key)
                    
                mark_dirty()
                
                # 최종 알림 전송을 위해 가격/플래그를 저장
                alert_price = exec_price
                should_send_final_alert = fully_closed
            else:
                should_send_final_alert = False

            # TP 체결 감지
            # stopOrderType이 TakeProfit 또는 PartialTakeProfit이면 동일 처리
            if stop_order_type and "TakeProfit" in str(stop_order_type):
                # (HedgeMode) TP 체결의 side는 '청산 주문의 방향' → 숏TP=Buy, 롱TP=Sell
                pos_side = "Short" if side == "Buy" else "Long"
                position_key = _resolve_position_key_for_close(symbol, pos_side, used_key)

                if not position_key:
                    log(f"❌ [TP 체결 처리 중단] 대상 포지션을 찾을 수 없음 (pos_side={pos_side}, symbol={symbol}, used_key={used_key})")
                    continue  # 이번 체결은 스킵

                # 포지션 캐시 존재 여부 확인
                if position_key not in shared_state.execution_data_store:
                    log(f"❌ [TP 체결 처리 중단] execution_data_store에 '{position_key}' 정보 없음")
                    continue
                
                filled_fills = shared_state.execution_data_store[position_key].setdefault("filled_fills", [])
                position_fills = shared_state.execution_data_store[position_key].setdefault("position_fills", {})

                label = f"TP_{exec_time}"

                # 진입가를 이용한 진입 수수료 계산
                entry_price = shared_state.execution_data_store[position_key].get("entry_price")
                open_fee_rate = shared_state.execution_data_store[position_key].get("open_fee_rate")
                open_fee = estimate_open_fee(entry_price, exec_qty, open_fee_rate)

                close_fee = exec_fee
                total_fee = open_fee + close_fee

                # 손익 계산
                pnl = format_signed_4f_with_comma(execPnl - total_fee)

                log(f"✨ [TP(Limit) 감지] {position_key} → {label} HIT @ {exec_price} → pnl={pnl}, open_fee={format_4f(open_fee)}, close_fee={format_4f(close_fee)} "
                    f"(stopOrderType={stop_order_type}, createType={create_type}, isMaker={is_maker}, orderType={order_type}, orderLinkId={order_link_id})")

                # TP 체결 정보 저장
                filled_fills.append(label)
                position_fills[label] = {
                    "pnl": execPnl,
                    "close_fee": close_fee,
                    "price": exec_price,
                    "qty": exec_qty,
                    "fill_time": exec_time
                }

                # 최근 TP 시각 기록(잔량 보정 디바운스에 활용)
                shared_state.last_tp_fill_at[position_key] = time.time()

                # TP fill을 position_fills에 이미 저장했으므로
                # recalc는 '이번 TP 반영 후 잔량'이다. 여기서 exec_qty를 또 빼면 2번 차감됨
                pinfo = shared_state.execution_data_store[position_key]
                stored = float(pinfo.get("current_size", 0.0))
                recalc_after = _recalc_current_size_from_fills(pinfo)

                patched = min(stored, recalc_after)

                if abs(patched - stored) >= float(_QTY_STEP) / 2:
                    log(f"♻️ [QTY-RECON] TP(Limit): stored={stored}, recalc_after={recalc_after} → apply={patched}")

                pinfo["current_size"] = max(patched, 0.0)
                mark_dirty()
                save_if_dirty()
                strategy = shared_state.execution_data_store[position_key].get("strategy", "unknown")
                strategy_label = format_strategy_name(strategy)
                exit_price = format_1f_with_comma(exec_price)
                alert_msg = (
                    f"<b>✨ {strategy_label}</b>\n"
                    f"<code>{label}</code>\n\n"
                    f"<b>Closed P&amp;L:</b> <code>{pnl} USD</code>\n"
                    f"<b>Exit Price:</b> <code>{exit_price}</code>\n"
                    f"<b>Quantity:</b> <code>{exec_qty} BTC</code>\n"
                    f"<b>Fee:</b> <code>{format_4f_with_comma(total_fee)} USD</code>"
                )
                send_positions_telegram_alert(alert_msg, parse_mode="HTML")    
                
                # DB: TP fill 삽입
                try:
                    insert_fill_by_order_link_id(
                        position_key,
                        fill_time_utc=parse_utc_compact_str_to_dt(exec_time),
                        price=exec_price,
                        qty=exec_qty,
                        pnl_gross=execPnl,
                        fee=close_fee,
                        fill_type="TP",
                        stage_code=None
                    )
                except Exception as e:
                    log(f"⚠️ [DB] TP fill 기록 실패: {e}")

                # TP 종료 감지: current_size 기준으로 판별
                if shared_state.execution_data_store[position_key]["current_size"] <= _EPS: # 극미량 반올림 오차 흡수
                    shared_state.execution_data_store[position_key]["closed"] = True
                    shared_state.execution_data_store[position_key]["exit_time"] = exec_time

                    #  포지션 오버레이: 종료 감지 즉시 clear 통지
                    try:
                        pinfo = shared_state.execution_data_store.get(position_key, {})
                        if isinstance(pinfo, dict) and not pinfo.get("overlay_cleared"):
                            pinfo["overlay_cleared"] = True
                            shared_state.execution_data_store[position_key] = pinfo
                            asyncio.create_task(notify_position_overlay_clear(position_key, exit_ts_ms=int(exec_data.get("execTime") or 0)))
                    except Exception as e:
                        log(f"⚠️ [PositionOverlay] clear notify 실패: {e}")
                    
                    # zone 전략은 TP 종료 후에도 zone 자체는 유지
                    if strategy == "zone_strategy":
                        # TP로 전량 종료 → 박스는 유지(재진입 허용), wick SL 플래그만 정리
                        try:
                            pinfo = shared_state.execution_data_store.get(position_key, {})
                            if isinstance(pinfo, dict):
                                pinfo["last_exit_type"] = "TP"
                                pinfo["last_exit_time"] = datetime.now(timezone.utc).isoformat()
                                pinfo.pop("wick_sl_active", None)
                                pinfo.pop("wick_sl_cid", None)
                                pinfo.pop("wick_sl_price", None)
                                shared_state.execution_data_store[position_key] = pinfo
                        except Exception as e:
                            log(f"⚠️ [Structure Zone] TP(Limit) 후 실행 캐시 정리 실패: {e}")
                    try:
                        finalize_position_close_by_order_link_id(
                            position_key,
                            exit_time_utc=parse_utc_compact_str_to_dt(exec_time)
                        )
                    except Exception as e:
                        log(f"⚠️ [DB] positions 종료 업데이트 실패: {e}")
                    mark_dirty()
                    save_if_dirty()
                    send_final_position_alert(exec_price, position_key=position_key)
                    _cancel_finalize_task(position_key)
                    
            # SL 체결 감지
            elif stop_order_type == "StopLoss":
                # (HedgeMode) SL 체결의 side 역시 '청산 주문의 방향'
                pos_side = "Short" if side == "Buy" else "Long"
                position_key = _resolve_position_key_for_close(symbol, pos_side, used_key)
                if not position_key:
                    log(f"❌ [SL 체결 처리 중단] 대상 포지션을 찾을 수 없음 (pos_side={pos_side}, symbol={symbol}, used_key={used_key})")
                    continue  # 이번 체결은 스킵

                if position_key not in shared_state.execution_data_store:
                    log(f"❌ [SL 체결 처리 중단] execution_data_store에 '{position_key}' 정보 없음")
                    continue

                position_info = shared_state.execution_data_store.get(position_key, {})
                strategy = position_info.get("strategy", "unknown")
                zone_parent_olid = None
                zone_meta = None
                if strategy == "zone_strategy":
                    zone_parent_olid, zone_meta = _arm_zone_reentry_guard_for_exit(
                        position_key=position_key,
                        used_key=used_key,
                        info=position_info,
                        symbol=symbol,
                        pos_side=pos_side,
                        reason="stoploss_fill_start",
                    )

                shared_state.execution_data_store[position_key]["closed"] = True  #  종료 마킹
                shared_state.execution_data_store[position_key]["exit_time"] = exec_time

                #  포지션 오버레이: 종료 감지 즉시 clear 통지
                try:
                    pinfo = shared_state.execution_data_store.get(position_key, {})
                    if isinstance(pinfo, dict) and not pinfo.get("overlay_cleared"):
                        pinfo["overlay_cleared"] = True
                        shared_state.execution_data_store[position_key] = pinfo
                        asyncio.create_task(notify_position_overlay_clear(position_key, exit_ts_ms=int(exec_data.get("execTime") or 0)))
                except Exception as e:
                    log(f"⚠️ [PositionOverlay] clear notify 실패: {e}")

                label = "SL"
                strategy_label = format_strategy_name(strategy)
                
                filled_fills = shared_state.execution_data_store[position_key].setdefault("filled_fills", [])
                position_fills = shared_state.execution_data_store[position_key].setdefault("position_fills", {})

                if strategy == "zone_strategy":
                    # StopLoss 전량 종료 시 박스는 깨지지 않았더라도 비활성화하여 재진입 차단
                    try:
                        pinfo = shared_state.execution_data_store.get(position_key, {})
                        if isinstance(pinfo, dict):
                            pinfo.pop("wick_sl_active", None)
                            pinfo.pop("wick_sl_cid", None)
                            pinfo.pop("wick_sl_price", None)
                            shared_state.execution_data_store[position_key] = pinfo
                    except Exception as e:
                        log(f"⚠️ [Structure Zone] SL 후 실행 캐시 정리 실패: {e}")

                    parent_olid = zone_parent_olid
                    meta = zone_meta
                    if not parent_olid or not meta:
                        parent_olid, meta = _resolve_zone_parent_and_meta(
                            position_key,
                            used_key,
                            shared_state.execution_data_store.get(position_key, {}),
                        )
                    if not meta:
                        log(f"⚠️ [Structure Zone] SL 종료 zone 비활성화 스킵: parent_olid={parent_olid}")
                    else:
                        ok = deactivate_zone_state_by_key(
                            symbol=meta["symbol"],
                            interval_min=meta["interval_min"],
                            start_ms=meta["start_ts"],
                            side=meta["side"],
                        )
                        if ok is True:
                            log(f"✅ [Structure Zone] SL 종료 zone 비활성화 완료: {meta}")
                        elif ok is False:
                            log(f"ℹ️ [Structure Zone] SL 종료 zone 비활성화 대상 없음(no-op): {meta}")
                        else:
                            _set_zone_reentry_guard(
                                parent_olid=parent_olid,
                                symbol=meta["symbol"],
                                side_up=meta["side"],
                                reason="stoploss_deactivate_db_error",
                            )
                            log(f"⚠️ [Structure Zone] SL 종료 zone 비활성화 실패(DB): {meta}")
                        shared_state.zone_levels_force_refresh = True
                        try:
                            asyncio.create_task(
                                _notify_zone_state_sync(meta["symbol"], meta["interval_min"])
                            )
                        except Exception as e:
                            log(f"⚠️ [Structure Zone] state-sync notify task 실패: {e}")

                # 진입가*수량*수수료율로 open_fee 계산
                entry_price = shared_state.execution_data_store[position_key].get("entry_price")
                open_fee_rate = shared_state.execution_data_store[position_key].get("open_fee_rate")
                open_fee = estimate_open_fee(entry_price, exec_qty, open_fee_rate)

                close_fee = exec_fee
                total_fee = open_fee + close_fee
                pnl = format_signed_4f_with_comma(execPnl - total_fee)

                log(f"🛑 [SL 감지] {position_key} {label} @ {exec_price} → pnl={pnl}, open_fee={format_4f(open_fee)}, close_fee={format_4f(close_fee)}")

                # SL 체결 정보 저장
                filled_fills.append(label)
                position_fills[label] = {
                    "pnl": execPnl,
                    "close_fee": close_fee,
                    "price": exec_price,
                    "qty": exec_qty,
                    "fill_time": exec_time
                }
                mark_dirty()
                save_if_dirty()
                exit_price = format_1f_with_comma(exec_price)
                alert_msg = (
                    f"<b>🛑 {strategy_label}</b>\n"
                    f"<code>{label}</code>\n\n"
                    f"<b>Closed P&amp;L:</b> <code>{pnl} USD</code>\n"
                    f"<b>Exit Price:</b> <code>{exit_price}</code>\n"
                    f"<b>Quantity:</b> <code>{exec_qty} BTC</code>\n"
                    f"<b>Fee:</b> <code>{format_4f_with_comma(total_fee)} USD</code>"
                )
                send_positions_telegram_alert(alert_msg, parse_mode="HTML") 

                # DB: SL fill 삽입
                try:
                    insert_fill_by_order_link_id(
                        position_key,
                        fill_time_utc=parse_utc_compact_str_to_dt(exec_time),
                        price=exec_price,
                        qty=exec_qty,
                        pnl_gross=execPnl,
                        fee=close_fee,
                        fill_type="SL",
                        stage_code=None
                    )
                except Exception as e:
                    log(f"⚠️ [DB] SL fill 기록 실패: {e}")

                # DB: 포지션 종료 집계 업데이트
                try:
                    finalize_position_close_by_order_link_id(
                        position_key,
                        exit_time_utc=parse_utc_compact_str_to_dt(exec_time)
                    )
                except Exception as e:
                    log(f"⚠️ [DB] positions 종료 업데이트 실패: {e}")

                send_final_position_alert(exec_price, position_key=position_key)
                _cancel_finalize_task(position_key)

            # Funding 체결 감지
            if exec_type == "Funding":
                ok = handle_funding_execution(
                    exec_fee=exec_fee,
                    exec_time=exec_time,
                    symbol=symbol,
                    side=side,
                    mark_dirty=mark_dirty,
                    save_if_dirty=save_if_dirty,
                )
                if not ok:
                    continue
            save_if_dirty()

            # 체결 정보 누적 이후에 포지션 종료 알림 전송 
            if should_send_final_alert:
                send_final_position_alert(alert_price, position_key=position_key)
        finally:
            save_if_dirty()
