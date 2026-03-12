from core.state import shared_state
from core.notifications.alert_utils import send_positions_telegram_alert
from core.utils.log_utils import log
from core.utils.tp_utils import (
    truncate_decimal,
    format_signed_4f_with_comma,
    format_1f_with_comma,
)


# 진입 수수료 계산용
def estimate_open_fee(entry_price: float, qty: float, fee_rate: float) -> float:
    try:
        return float(entry_price) * float(qty) * float(fee_rate)
    except (TypeError, ValueError):
        return 0.0


# 알림 노출용 전략 이름 정리
def format_strategy_name(strategy: str) -> str:
    if strategy == "zone_strategy":
        return "Structure Zone"
    return strategy.replace("_", " ").title()


def send_final_position_alert(exit_price, position_key: str | None = None):
    # 종료 체결 기준 최종 알림 전송용
    key = (
        position_key
        or shared_state.current_position_link_id
        or shared_state.last_execution_order_id
    )
    if not key or key not in shared_state.execution_data_store:
        log("❌ [send_final_position_alert] position_link_id가 없거나 execution_data_store에 존재하지 않습니다.")
        return
    info = shared_state.execution_data_store.get(key, {})
    log(f"[DEBUG] entry_info={info}")

    display_side = info.get("display_side", "?")
    symbol = info.get("symbol", "?")
    entry_price = info.get("entry_price", 0.0)
    formatted_entry = format_1f_with_comma(entry_price)
    formatted_exit = format_1f_with_comma(exit_price)
    strategy = info.get("strategy", "미상")
    strategy_label = format_strategy_name(strategy)

    # 체결 누적 기준 손익 정산용
    position_fills = info.get("position_fills", {})

    pnl_total = sum(float(f.get("pnl", 0.0)) for f in position_fills.values())
    close_fee_total = sum(float(f.get("close_fee", 0.0)) for f in position_fills.values())
    open_fee_total = float(info.get("open_fee", 0.0))

    net_pnl = truncate_decimal(pnl_total - close_fee_total - open_fee_total, 8)

    log(f"📊 수수료 상세 → total_fee={format_signed_4f_with_comma(close_fee_total + open_fee_total)}")
    log(f"📈 손익 정산 → P&L = {net_pnl}")

    signed_pnl = format_signed_4f_with_comma(net_pnl)

    # HTML 텔레그램 알림 본문 조립용
    alert_msg = (
        f"<b>💰 {strategy_label}</b>\n"
        f"<b>{symbol}</b> <code>{display_side}</code>\n\n"
        f"<b>Realized P&amp;L:</b> <code>{signed_pnl} USD</code>\n"
        f"<b>Entry Price:</b> <code>{formatted_entry}</code>\n"
        f"<b>Filled Price:</b> <code>{formatted_exit}</code>"
    )
    send_positions_telegram_alert(alert_msg, parse_mode="HTML")
