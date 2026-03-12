from __future__ import annotations

import os
import time


# Structure Zone 주문 키 규약 정리용 유틸
# 문자열 생성/파싱만 담당하는 레이어
# DB 접근, 네트워크 호출, shared_state 접근 금지

_SYMBOL_ENV = os.getenv("SYMBOL")


def _default_symbol(default_symbol: str | None) -> str | None:
    # 기본 심볼 미지정 시 .env의 SYMBOL 사용
    return _SYMBOL_ENV if default_symbol is None else default_symbol


def zone_parent_order_link_id_from_box_key(
    box_key: dict,
    *,
    default_symbol: str | None = None,
) -> str | None:
    # 박스 메타 -> parent_order_link_id 변환용
    try:
        symbol = str(box_key.get("symbol") or _default_symbol(default_symbol))
        interval_min = int(box_key.get("interval_min"))
        start_ts = int(box_key.get("start_ts"))
        side = str(box_key.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return None
        return f"zonebox|{symbol}|{interval_min}|{start_ts}|{side}"
    except Exception:
        return None


def zone_make_order_link_id_from_box_key(
    box_key: dict,
    *,
    default_symbol: str | None = None,
    now_ms: int | None = None,
) -> str | None:
    # 거래소 길이 제한 고려용 엔트리 주문 키 생성
    try:
        symbol = str(box_key.get("symbol") or _default_symbol(default_symbol))
        interval_min = int(box_key.get("interval_min"))
        start_ts = int(box_key.get("start_ts"))
        side = str(box_key.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return None

        side_letter = "L" if side == "LONG" else "S"
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        unique_suffix = str(int(now_ms) % 100000).zfill(5)
        return f"zn{symbol}-{interval_min}-{start_ts}-{side_letter}-{unique_suffix}"
    except Exception:
        return None


def is_zone_order_link_id(order_link_id: str) -> bool:
    if not order_link_id or not order_link_id.startswith("zn"):
        return False
    parts = order_link_id.split("-")
    return len(parts) >= 5 and len(parts[0]) > 2


def parse_zone_order_link_id(order_link_id: str) -> dict | None:
    # zn{symbol}-{tf}-{start_ts}-{L/S}-{u5} 파싱용
    try:
        if not is_zone_order_link_id(order_link_id):
            return None

        parts = order_link_id.split("-")
        symbol = parts[0][2:]
        interval_min = int(parts[1])
        start_ts = int(parts[2])
        side_letter = parts[3]
        side = "LONG" if side_letter == "L" else "SHORT" if side_letter == "S" else None
        if not symbol or side is None:
            return None

        return {
            "symbol": symbol,
            "interval_min": interval_min,
            "start_ts": start_ts,
            "side": side,
            "parent_order_link_id": f"zonebox|{symbol}|{interval_min}|{start_ts}|{side}",
        }
    except Exception:
        return None


def zone_parent_from_order_link_id(order_link_id: str) -> str | None:
    try:
        parsed = parse_zone_order_link_id(order_link_id)
        return parsed.get("parent_order_link_id") if parsed else None
    except Exception:
        return None


def parse_zone_parent_order_link_id(parent_order_link_id: str) -> dict | None:
    # parent_order_link_id -> zone 메타 복원용
    try:
        if not parent_order_link_id or not isinstance(parent_order_link_id, str):
            return None
        if not parent_order_link_id.startswith("zonebox|"):
            return None

        parts = parent_order_link_id.split("|")
        if len(parts) != 5:
            return None

        _, symbol, interval_min, start_ts, side = parts
        side = side.upper()
        if side not in ("LONG", "SHORT"):
            return None

        return {
            "symbol": symbol,
            "interval_min": int(interval_min),
            "start_ts": int(start_ts),
            "side": side,
        }
    except Exception:
        return None
