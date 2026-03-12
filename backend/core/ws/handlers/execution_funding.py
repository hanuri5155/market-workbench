from core.state import shared_state
from core.notifications.alert_utils import send_positions_telegram_alert
from core.utils.log_utils import log
from core.utils.side_utils import normalize_bybit_side
from core.utils.time_utils import parse_utc_compact_str_to_dt
from core.utils.tp_utils import format_signed_4f_with_comma_round
from core.persistence.positions_repo import insert_fill_by_order_link_id
from core.ws.handlers.execution_common import format_strategy_name
from core.ws.handlers.store_adapter import resolve_position_key_for_close


def handle_funding_execution(*, exec_fee, exec_time, symbol, side, mark_dirty, save_if_dirty) -> bool:
    # Funding의 side는 '포지션의 방향'으로 오는 것이 일반적이므로 그대로 매핑
    pos_side = normalize_bybit_side(side)
    position_key = resolve_position_key_for_close(symbol, pos_side, None)

    if not position_key:
        # 최후의 보루로 기존 fallback
        position_key = (
            shared_state.current_position_link_id
            or shared_state.last_execution_order_id
        )
        # 최후의 보루로 fallback 하되, 심볼/사이드까지 검증
        cand = shared_state.current_position_link_id or shared_state.last_execution_order_id
        if cand and cand in shared_state.execution_data_store:
            vi = shared_state.execution_data_store.get(cand, {})
            if (
                isinstance(vi, dict)
                and not vi.get("closed", False)
                and vi.get("symbol") == symbol
                and vi.get("display_side") == pos_side
            ):
                position_key = cand
            else:
                position_key = None
        else:
            position_key = None

    if not position_key or position_key not in shared_state.execution_data_store:
        log("❌ [펀딩 수수료 처리 중단] 대상 포지션을 찾지 못했습니다.")
        return False

    filled_fills = shared_state.execution_data_store[position_key].setdefault("filled_fills", [])
    position_fills = shared_state.execution_data_store[position_key].setdefault("position_fills", {})
    strategy = shared_state.execution_data_store.get(position_key, {}).get("strategy", "unknown")
    strategy_label = format_strategy_name(strategy)

    label = f"Funding_{exec_time}"

    filled_fills.append(label)
    position_fills[label] = {
        "pnl": -exec_fee,
        "close_fee": 0.0,
        "qty": 0.0,
        "fill_time": exec_time
    }
    mark_dirty()
    save_if_dirty()
    # DB: FUNDING fill 삽입 (pnl_gross에 그대로 반영, fee=0)
    try:
        insert_fill_by_order_link_id(
            position_key,
            fill_time_utc=parse_utc_compact_str_to_dt(exec_time),
            price=shared_state.execution_data_store[position_key].get("entry_price", 0.0),  # 의미상 값
            qty=0.0,            # 거래 수량 아님 → 0으로 기록(또는 유지중 수량을 넣어도 무방)
            pnl_gross=-exec_fee, # 음수(수령) / 양수(지불)
            fee=0.0,            # 수수료 아님
            fill_type="FUNDING",
            stage_code=None
        )
    except Exception as e:
        log(f"⚠️ [DB] FUNDING fill 기록 실패: {e}")

    log(f"💸 펀딩 수수료 체결 감지: fee={format_signed_4f_with_comma_round(exec_fee)}, time={exec_time}")
    funding_received = exec_fee < 0
    alert_msg = (
        f"<b>💸 {strategy_label}</b>\n"
        f"<b>Funding Fee:</b> <code>{format_signed_4f_with_comma_round(exec_fee)} USD</code> "
        f"<i>({'Received' if funding_received else 'Paid'})</i>"
    )
    send_positions_telegram_alert(alert_msg, parse_mode="HTML")
    return True
