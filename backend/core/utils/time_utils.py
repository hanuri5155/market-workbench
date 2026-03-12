## backend/core/utils/time_utils.py

from __future__ import annotations

from datetime import datetime, timezone

DEFAULT_UTC_FMT = "%Y-%m-%d %H:%M:%S"


# UTC datetime 문자열을 epoch ms로 파싱
#
# - 기본 포맷은 "%Y-%m-%d %H:%M:%S" (예: "2026-01-29 12:34:56")
# - 실패하면 None 반환
def parse_utc_datetime_str_to_ms(dt_str: str | None, fmt: str = DEFAULT_UTC_FMT) -> int | None:
    if not dt_str:
        return None
    try:
        dt = datetime.strptime(str(dt_str), fmt).replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


COMPACT_UTC_FMT = "%Y%m%d%H%M%S"


# epoch ms → UTC datetime 문자열(기본: %Y%m%d%H%M%S). 실패하면 None
def utc_ms_to_compact_str(ms: int | float | str | None, fmt: str = COMPACT_UTC_FMT) -> str | None:
    if ms is None:
        return None
    try:
        ms_i = int(float(ms))
        if ms_i <= 0:
            return None
        dt = datetime.fromtimestamp(ms_i / 1000.0, tz=timezone.utc)
        return dt.strftime(fmt)
    except Exception:
        return None


# UTC datetime 문자열(%Y%m%d%H%M%S)을 timezone.utc datetime으로 파싱(실패 시 예외 발생)
def parse_utc_compact_str_to_dt(dt_str: str, fmt: str = COMPACT_UTC_FMT) -> datetime:
    # 호출부에서 예외를 그대로 처리할 수 있게 반환 대신 예외 유지
    return datetime.strptime(str(dt_str), fmt).replace(tzinfo=timezone.utc)
