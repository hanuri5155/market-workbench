from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable


# 수동(Manual) 포지션은 포지션 단위로 1개 키로 통일
def manual_position_key(symbol: str, display_side: str) -> str:
    return f"manual|{symbol}|{display_side}"


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        s = str(v).strip()
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


# entry_size - sum(close_fills.qty)를 기준으로 현재 수량을 재산출
# (Funding 등 qty=0인 항목 제외)
def recalc_current_size_from_fills(info: dict, *, floor_qty: Callable[[float], float]) -> float:
    entry = Decimal(str(info.get("entry_size", 0.0)))
    closed = Decimal("0")
    for f in info.get("position_fills", {}).values():
        try:
            q = Decimal(str(f.get("qty", 0.0)))
        except Exception:
            q = Decimal("0")
        if q > 0:
            closed += q
    return max(floor_qty(float(entry - closed)), 0.0)


def find_open_position_keys(store: dict, symbol: str, display_side: str, *, strategy: str | None = None) -> list[str]:
    keys: list[str] = []
    for k, v in store.items():
        if not isinstance(v, dict):
            continue
        if v.get("closed", False):
            continue
        if v.get("symbol") != symbol:
            continue
        if v.get("display_side") != display_side:
            continue
        if strategy is not None and v.get("strategy") != strategy:
            continue
        keys.append(k)
    return keys


# symbol+side에 해당하는 '현재 열린' 포지션 키를 하나로 결정
def resolve_open_position_key_for_update(
    store: dict,
    symbol: str,
    display_side: str,
    *,
    current_position_link_id: str | None = None,
    last_execution_order_id: str | None = None,
) -> str | None:
    # 1) manual canonical
    mk = manual_position_key(symbol, display_side)
    mv = store.get(mk)
    if isinstance(mv, dict) and not mv.get("closed", False):
        return mk

    # 2) candidates
    cand = []
    for k, v in store.items():
        if not isinstance(v, dict):
            continue
        if v.get("closed", False):
            continue
        if v.get("symbol") != symbol:
            continue
        if v.get("display_side") != display_side:
            continue
        cand.append((k, v))

    if cand:
        cand.sort(key=lambda kv: kv[1].get("entry_time", ""), reverse=True)
        return cand[0][0]

    # 3) fallbacks
    for k in (current_position_link_id, last_execution_order_id):
        if not k:
            continue
        v = store.get(k)
        if isinstance(v, dict) and not v.get("closed", False) and v.get("symbol") == symbol and v.get("display_side") == display_side:
            return k

    return None


# execution_data_store의 두 레코드를 '포지션 단위'로 병합
def merge_store_record_into(
    store: dict,
    dst_key: str,
    src_key: str,
    *,
    floor_qty: Callable[[float], float],
) -> bool:
    if dst_key == src_key:
        return False

    src = store.get(src_key)
    if not isinstance(src, dict) or src.get("closed", False):
        return False

    dst = store.get(dst_key)
    if not isinstance(dst, dict):
        store[dst_key] = dict(src)
        store.pop(src_key, None)
        store[dst_key]["strategy"] = "manual"
        store[dst_key]["overlay_cleared"] = False
        return True

    # qty/value/fee 합산
    dst_entry_qty = safe_float(dst.get("entry_size"), 0.0)
    src_entry_qty = safe_float(src.get("entry_size"), 0.0)

    dst_price = safe_float(dst.get("entry_price"), 0.0)
    src_price = safe_float(src.get("entry_price"), 0.0)

    total_qty_raw = dst_entry_qty + src_entry_qty
    total_qty = floor_qty(total_qty_raw)

    # entry/qty가 유효할 때만 가중평균 진입가 계산
    if total_qty_raw > 0 and dst_price > 0 and src_price > 0:
        wavg = (dst_price * dst_entry_qty + src_price * src_entry_qty) / total_qty_raw
        dst["entry_price"] = wavg
    elif dst_price <= 0 and src_price > 0:
        dst["entry_price"] = src_price

    dst["entry_size"] = total_qty

    dst_cur = safe_float(dst.get("current_size"), 0.0)
    src_cur = safe_float(src.get("current_size"), 0.0)
    dst["current_size"] = floor_qty(dst_cur + src_cur)

    dst["open_fee"] = safe_float(dst.get("open_fee"), 0.0) + safe_float(src.get("open_fee"), 0.0)
    dst["exec_value"] = safe_float(dst.get("exec_value"), 0.0) + safe_float(src.get("exec_value"), 0.0)

    # entry_time은 더 이른 쪽
    dt = str(dst.get("entry_time") or "")
    st = str(src.get("entry_time") or "")
    if dt and st:
        dst["entry_time"] = min(dt, st)
    elif not dt and st:
        dst["entry_time"] = st

    # 수동 진입 묶음 추적용 order id 기록
    ids = []
    for v in (dst.get("manual_source_order_ids"), src.get("manual_source_order_ids")):
        if isinstance(v, list):
            ids.extend([str(x) for x in v if x])
    ids.append(str(src_key))

    seen = set()
    uniq = []
    for x in ids:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    if uniq:
        dst["manual_source_order_ids"] = uniq

    # SL/TP는 '있으면' 더 최신값 우선(그냥 src 우선 덮어쓰기)
    for k in ("sl_price", "tp_price", "tp_full_price"):
        sv = src.get(k)
        if sv is not None and safe_float(sv, 0.0) > 0:
            dst[k] = safe_float(sv, 0.0)

    dst["strategy"] = "manual"
    dst["overlay_cleared"] = False

    store[dst_key] = dst
    store.pop(src_key, None)
    return True


# pos_side: 'Long' 또는 'Short' (체결된 청산 주문이 줄이는 쪽)
# 우선순위:
# 1) used_key 자체가 entry 키로 열려 있으면 그것
# 2) 같은 심볼+side의 '열린' 포지션들 중 entry_time 최신
# 3) current_position_link_id / meta.last_active_order 가 같은 side면 그것
def resolve_position_key_for_close(
    store: dict,
    symbol: str,
    pos_side: str,
    used_key: str | None,
    *,
    current_position_link_id: str | None = None,
    last_execution_order_id: str | None = None,
) -> str | None:
    # 1) used_key가 entry 키로 저장돼 있고 아직 열린 상태면 우선
    if used_key in store:
        v = store.get(used_key, {})
        if (isinstance(v, dict) and not v.get("closed", False)
            and v.get("symbol") == symbol
            and v.get("display_side") == pos_side):
            return used_key

    # 2) candidates
    candidates = []
    for k, v in store.items():
        if not isinstance(v, dict):
            continue
        if v.get("symbol") != symbol:
            continue
        if v.get("display_side") != pos_side:
            continue
        if v.get("closed", False):
            continue
        candidates.append((k, v))
    if candidates:
        candidates.sort(key=lambda kv: kv[1].get("entry_time", ""), reverse=True)
        return candidates[0][0]

    # 3) fallbacks (side 일치할 때만)
    pk = current_position_link_id
    vi = store.get(pk or "", {})
    if pk and vi and not vi.get("closed", False) and vi.get("symbol") == symbol and vi.get("display_side") == pos_side:
        return pk

    pk = last_execution_order_id
    vi = store.get(pk or "", {})
    if pk and vi and not vi.get("closed", False) and vi.get("symbol") == symbol and vi.get("display_side") == pos_side:
        return pk

    return None
