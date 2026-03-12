from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from core.persistence.execution_store import load_execution_data_store
from core.trading.execution_store_ops import manual_position_key

if TYPE_CHECKING:
    from app.db.models import Position


BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_EXECUTION_DATA_STORE_PATH = "storage/execution_data_store.json"


def normalize_side_upper(raw: str | None) -> str | None:
    value = str(raw or "").strip().upper()
    if value in {"LONG", "BUY"}:
        return "LONG"
    if value in {"SHORT", "SELL"}:
        return "SHORT"
    return None


def make_position_overlay_id(symbol: str, side_upper: str) -> str:
    return f"pos:{symbol}:{side_upper}"


def to_epoch_ms(dt: datetime | None) -> int | None:
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)


def build_position_overlay_snapshot(
    *,
    symbol: str,
    bybit_rows: list[dict[str, Any]],
    bybit_ok: bool,
    latest_open_db_by_side: dict[str, "Position"],
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    symbol_upper = str(symbol or "").upper()
    current_ms = int(now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000)
    exec_store_by_side = _load_open_execution_store_by_side(symbol_upper)

    bybit_by_side: dict[str, dict[str, Any]] = {}
    if bybit_ok:
        for row in bybit_rows or []:
            side_upper = normalize_side_upper(row.get("side"))
            if side_upper not in {"LONG", "SHORT"}:
                continue
            bybit_by_side[side_upper] = row

    if bybit_ok:
        active_sides = set(bybit_by_side.keys())
    else:
        active_sides = set(exec_store_by_side.keys()) | set(latest_open_db_by_side.keys())

    overlays: list[dict[str, Any]] = []
    for side_upper in sorted(active_sides):
        store_record = exec_store_by_side.get(side_upper) or {}
        overlay = _build_overlay(
            symbol_upper=symbol_upper,
            side_upper=side_upper,
            bybit_row=bybit_by_side.get(side_upper),
            store_info=store_record.get("info"),
            db_pos=latest_open_db_by_side.get(side_upper),
            now_ms=current_ms,
        )
        if overlay:
            overlays.append(overlay)

    overlays.sort(key=lambda item: str(item.get("id") or ""))
    return overlays


def _build_overlay(
    *,
    symbol_upper: str,
    side_upper: str,
    bybit_row: dict[str, Any] | None,
    store_info: dict[str, Any] | None,
    db_pos: "Position" | None,
    now_ms: int,
) -> dict[str, Any] | None:
    info = store_info if isinstance(store_info, dict) else {}
    strategy = _strategy_to_text(getattr(db_pos, "strategy", None)) or _strategy_to_text(info.get("strategy"))

    entry_ts = (
        _entry_ts_from_store(info)
        or _entry_ts_from_bybit(bybit_row)
        or to_epoch_ms(getattr(db_pos, "entry_time", None))
        or now_ms
    )

    entry_price = _first_positive(
        _safe_float((bybit_row or {}).get("avgPrice"), 0.0),
        _safe_float((bybit_row or {}).get("entryPrice"), 0.0),
        _safe_float(info.get("entry_price"), 0.0),
        _safe_float(getattr(db_pos, "entry_price", None), 0.0),
    )
    if entry_price <= 0:
        return None

    tp_available, tp_price = _resolve_tp(bybit_row, info)
    sl_available, sl_price = _resolve_sl(strategy, bybit_row, info, db_pos)

    return {
        "id": make_position_overlay_id(symbol_upper, side_upper),
        "symbol": symbol_upper,
        "strategy": strategy,
        "side": side_upper,
        "entryTs": int(entry_ts),
        "entryPrice": float(entry_price),
        "tpAvailable": bool(tp_available),
        "tpPrice": float(tp_price) if tp_available and tp_price is not None else None,
        "slAvailable": bool(sl_available),
        "slPrice": float(sl_price) if sl_available and sl_price is not None else None,
        "closed": False,
        "exitTs": None,
    }


def _load_open_execution_store_by_side(symbol_upper: str) -> dict[str, dict[str, Any]]:
    path = _resolve_runtime_path(
        os.getenv("EXECUTION_DATA_STORE_PATH") or DEFAULT_EXECUTION_DATA_STORE_PATH
    )
    wrapped = load_execution_data_store(path)
    store = wrapped.get("store", {})
    if not isinstance(store, dict):
        return {}

    latest: dict[str, dict[str, Any]] = {}
    for key, info in store.items():
        if not isinstance(info, dict) or info.get("closed", False):
            continue

        info_symbol = str(info.get("symbol") or "").upper()
        if info_symbol != symbol_upper:
            continue

        side_upper = normalize_side_upper(
            info.get("display_side") or info.get("side") or info.get("pos_side")
        )
        if side_upper not in {"LONG", "SHORT"}:
            continue

        current = latest.get(side_upper)
        if current is None or _store_candidate_sort_key(symbol_upper, side_upper, str(key), info) > _store_candidate_sort_key(
            symbol_upper,
            side_upper,
            str(current.get("key") or ""),
            current.get("info") if isinstance(current.get("info"), dict) else {},
        ):
            latest[side_upper] = {"key": str(key), "info": dict(info)}

    return latest


def _store_candidate_sort_key(
    symbol_upper: str,
    side_upper: str,
    key: str,
    info: dict[str, Any],
) -> tuple[int, int, str, str]:
    display_side = "Long" if side_upper == "LONG" else "Short"
    manual_key = manual_position_key(symbol_upper, display_side)
    return (
        1 if key == manual_key else 0,
        _safe_int(info.get("entry_ts_ms"), 0),
        str(info.get("entry_time") or ""),
        str(key),
    )


def _resolve_tp(bybit_row: dict[str, Any] | None, info: dict[str, Any]) -> tuple[bool, float | None]:
    if bybit_row is not None:
        price = _safe_float(bybit_row.get("takeProfit"), 0.0)
        return price > 0, (price if price > 0 else None)

    price = _first_positive(
        _safe_float(info.get("tp_full_price"), 0.0),
        _safe_float(info.get("tp_price"), 0.0),
    )
    return price > 0, (price if price > 0 else None)


def _resolve_sl(
    strategy: str | None,
    bybit_row: dict[str, Any] | None,
    info: dict[str, Any],
    db_pos: "Position" | None,
) -> tuple[bool, float | None]:
    strategy_lower = str(strategy or "").lower()
    exchange_sl = _exchange_sl_price(bybit_row, info)

    if strategy_lower == "manual":
        return exchange_sl > 0, (exchange_sl if exchange_sl > 0 else None)

    if strategy_lower == "zone_strategy":
        if exchange_sl > 0:
            return True, exchange_sl
        wick_active = bool(info.get("wick_sl_active"))
        wick_price = _safe_float(info.get("wick_sl_price"), 0.0)
        return wick_active and wick_price > 0, (wick_price if wick_active and wick_price > 0 else None)

    internal_sl = _first_positive(
        _safe_float(info.get("sl_price"), 0.0),
        _safe_float(getattr(db_pos, "sl_price", None), 0.0),
    )
    effective_sl = exchange_sl if exchange_sl > 0 else internal_sl
    return effective_sl > 0, (effective_sl if effective_sl > 0 else None)


def _exchange_sl_price(bybit_row: dict[str, Any] | None, info: dict[str, Any]) -> float:
    if bybit_row is not None:
        return _safe_float(bybit_row.get("stopLoss"), 0.0)

    cached = _safe_float(info.get("exchange_sl_price"), 0.0)
    available = bool(info.get("exchange_sl_available")) or cached > 0
    return cached if available and cached > 0 else 0.0


def _resolve_runtime_path(raw_path: str | None) -> str | None:
    value = str(raw_path or "").strip()
    if not value:
        return None

    path = Path(value)
    if not path.is_absolute():
        path = BACKEND_DIR / value
    return str(path)


def _entry_ts_from_bybit(row: dict[str, Any] | None) -> int | None:
    if not isinstance(row, dict):
        return None

    for field in ("updatedTime", "createdTime"):
        value = _safe_int(row.get(field), 0)
        if value > 0:
            return value
    return None


def _entry_ts_from_store(info: dict[str, Any]) -> int | None:
    if not isinstance(info, dict):
        return None

    value = _safe_int(info.get("entry_ts_ms"), 0)
    if value > 0:
        return value

    raw = str(info.get("entry_time") or "").strip()
    if not raw:
        return None

    parsers = [
        lambda v: datetime.strptime(v, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc),
        lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),
        lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc),
        lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
    ]
    for parser in parsers:
        try:
            return int(parser(raw).astimezone(timezone.utc).timestamp() * 1000)
        except Exception:
            continue
    return None


def _strategy_to_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _first_positive(*values: float) -> float:
    for value in values:
        if value > 0:
            return value
    return 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default
