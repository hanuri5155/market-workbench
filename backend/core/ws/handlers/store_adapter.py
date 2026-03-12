from __future__ import annotations

from core.state import shared_state
from core.trading.execution_store_ops import (
    manual_position_key,
    safe_float,
    find_open_position_keys as _find_open_position_keys_impl,
    resolve_open_position_key_for_update as _resolve_open_position_key_for_update_impl,
    merge_store_record_into as _merge_store_record_into_impl,
    resolve_position_key_for_close as _resolve_position_key_for_close_impl,
)


def find_open_position_keys(symbol: str, display_side: str, *, strategy: str | None = None) -> list[str]:
    return _find_open_position_keys_impl(
        shared_state.execution_data_store,
        symbol,
        display_side,
        strategy=strategy,
    )


def resolve_open_position_key_for_update(symbol: str, display_side: str) -> str | None:
    return _resolve_open_position_key_for_update_impl(
        shared_state.execution_data_store,
        symbol,
        display_side,
        current_position_link_id=shared_state.current_position_link_id,
        last_execution_order_id=shared_state.last_execution_order_id,
    )


def merge_store_record_into(dst_key: str, src_key: str, *, floor_qty) -> bool:
    return _merge_store_record_into_impl(
        shared_state.execution_data_store,
        dst_key,
        src_key,
        floor_qty=floor_qty,
    )


def resolve_position_key_for_close(symbol: str, pos_side: str, used_key: str | None):
    return _resolve_position_key_for_close_impl(
        shared_state.execution_data_store,
        symbol,
        pos_side,
        used_key,
        current_position_link_id=shared_state.current_position_link_id,
        last_execution_order_id=shared_state.last_execution_order_id,
    )
