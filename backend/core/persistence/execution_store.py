from __future__ import annotations

import os
import json

from core.utils.file_utils import write_json_atomic


# store(dict) 내부에서 dict가 아닌 최상위 엔트리를 제거/보정
def _sanitize_store_inplace(store: dict) -> int:
    fixed = 0
    try:
        if not isinstance(store, dict):
            return 0
        # meta가 store 안에 끼어들었으면 제거(메타는 wrapper로 별도 저장)
        if "meta" in store and not isinstance(store.get("meta"), dict):
            store.pop("meta", None)
            fixed += 1
        # 최상위 엔트리 중 dict가 아닌 것 제거
        for k, v in list(store.items()):
            if not isinstance(v, dict):
                store.pop(k, None)
                fixed += 1
    except Exception:
        pass
    return fixed


# execution_data_store JSON을 로드하여 wrapper(dict)를 반환
# wrapper 형식: {"store": {...}, "meta": {"last_active_order": ...}}
def load_execution_data_store(path: str | None) -> dict:
    if not path:
        return {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                return {}

            store = data.get("store", {})
            if not isinstance(store, dict):
                store = {}

            fixed = _sanitize_store_inplace(store)
            if fixed:
                data["store"] = store
            return data
        except Exception as e:
            print(f"❌ execution_data_store 로딩 실패: {e}")
            return {}
    return {}


# execution_data_store 저장
#
# 메모리/디스크 보호:
# - 닫힌 포지션 우선 삭제
# - 최근 N개 포지션만 유지 (기본 200개, ENV: EXEC_STORE_MAX)
# - position_fills 상한 (기본 100개, ENV: EXEC_FILLS_MAX)
def save_execution_data_store(
    path: str | None,
    store: dict,
    *,
    last_active_order: str | None = None,
    indent: int = 2,
) -> None:
    if not path:
        print("❌ execution_data_store 저장 실패: path is empty")
        return

    try:
        # 저장 직전 항상 sanitize (비-dict 최상위 엔트리 제거)
        if not isinstance(store, dict):
            store = {}
        else:
            _sanitize_store_inplace(store)

        pruned: dict = {}
        meta = {"last_active_order": last_active_order}

        max_pos = int(os.getenv("EXEC_STORE_MAX", "200"))
        max_fills = int(os.getenv("EXEC_FILLS_MAX", "100"))

        open_items: list[tuple[str, dict]] = []
        closed_items: list[tuple[str, dict]] = []

        for key, val in store.items():
            if key in ("meta",):  # 혹시 잘못 들어온 메타 키 방지
                continue
            if not isinstance(val, dict):
                continue
            closed = bool(val.get("closed"))
            (closed_items if closed else open_items).append((key, val))

        def ts_of(v: dict) -> str:
            return v.get("exit_time") or v.get("entry_time") or ""

        open_items.sort(key=lambda kv: ts_of(kv[1]), reverse=True)
        closed_items.sort(key=lambda kv: ts_of(kv[1]), reverse=True)

        kept: list[tuple[str, dict]] = []
        for kv in open_items:
            kept.append(kv)
            if len(kept) >= max_pos:
                break
        for kv in closed_items:
            if len(kept) >= max_pos:
                break
            kept.append(kv)

        for k, v in kept:
            fills = v.setdefault("position_fills", {})
            if isinstance(fills, dict) and len(fills) > max_fills:
                sorted_items = sorted(
                    fills.items(),
                    key=lambda kv2: kv2[1].get("fill_time", ""),
                    reverse=True,
                )
                v["position_fills"] = dict(sorted_items[:max_fills])
            pruned[k] = v

        wrapped = {"store": pruned, "meta": meta}
        write_json_atomic(path, wrapped, indent=indent)
    except Exception as e:
        print(f"❌ execution_data_store 저장 실패: {e}")
