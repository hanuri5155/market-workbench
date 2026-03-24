from __future__ import annotations

from datetime import datetime, timezone


def normalize_zone_side(side: str | None) -> str:
    side_up = (side or "").strip().upper()
    return "SHORT" if side_up == "SHORT" else "LONG"


def build_zone_projection_payload(
    *,
    symbol: str,
    interval_min: int,
    start_time: datetime,
    end_time: datetime | None,
    side: str,
    base_entry: float,
    base_sl: float,
    entry_override: float | None,
    is_active: bool,
) -> dict:
    side_up = normalize_zone_side(side)
    base_entry_value = float(base_entry)
    base_sl_value = float(base_sl)
    entry_override_value = float(entry_override) if entry_override is not None else None

    if entry_override_value is None:
        render_entry = base_entry_value
        render_sl = base_sl_value
        render_upper = max(base_entry_value, base_sl_value)
        render_lower = min(base_entry_value, base_sl_value)
    else:
        render_entry = entry_override_value
        render_sl = base_sl_value
        if side_up == "LONG":
            render_upper = render_entry
            render_lower = render_sl
        else:
            render_upper = render_sl
            render_lower = render_entry

    is_broken = end_time is not None
    render_is_active = False if is_broken else bool(is_active)

    return {
        "symbol": symbol,
        "interval_min": int(interval_min),
        "start_time": start_time,
        "end_time": end_time,
        "side": side_up,
        "base_entry": base_entry_value,
        "base_sl": base_sl_value,
        "entry_override": entry_override_value,
        "render_entry": float(render_entry),
        "render_sl": float(render_sl),
        "render_upper": float(render_upper),
        "render_lower": float(render_lower),
        "is_active": render_is_active,
        "is_broken": bool(is_broken),
        "last_updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
