## backend/app/api/ws/position_overlay.py

from __future__ import annotations
import asyncio
from typing import Any
from fastapi import WebSocket

#  클라이언트 집합
_POSITION_OVERLAY_CLIENTS: set[WebSocket] = set()

#  현재 상태 스냅샷(hedge 모드 포함)
#    key = overlay.id (보통 order_link_id)
_POSITION_OVERLAY_STATE: dict[str, dict[str, Any]] = {}

#  마지막 이벤트(디버깅/재접속 누락 방지 용)
_LAST_EVENT: dict[str, Any] | None = None

_STATE_LOCK = asyncio.Lock()


async def register_position_overlay_client(ws: WebSocket) -> None:
    await ws.accept()
    _POSITION_OVERLAY_CLIENTS.add(ws)

    #  연결 즉시: 현재 상태 스냅샷 1회 push (무폴링 + 재접속 누락 방지)
    async with _STATE_LOCK:
        snapshot = list(_POSITION_OVERLAY_STATE.values())
        last_event = dict(_LAST_EVENT) if isinstance(_LAST_EVENT, dict) else None

    try:
        await ws.send_json({"type": "position_overlay_snapshot", "overlays": snapshot})
        if last_event is not None:
            await ws.send_json({"type": "position_overlay_last_event", "event": last_event})
    except Exception:
        pass


async def unregister_position_overlay_client(ws: WebSocket) -> None:
    _POSITION_OVERLAY_CLIENTS.discard(ws)


async def _broadcast(event: dict[str, Any]) -> None:
    global _LAST_EVENT
    _LAST_EVENT = event

    dead: list[WebSocket] = []
    for client in list(_POSITION_OVERLAY_CLIENTS):
        try:
            await client.send_json(event)
        except Exception:
            dead.append(client)

    for c in dead:
        _POSITION_OVERLAY_CLIENTS.discard(c)


async def upsert_overlay_and_broadcast(overlay: dict[str, Any]) -> None:
    overlay_id = str(overlay.get("id") or "")
    if not overlay_id:
        return

    async with _STATE_LOCK:
        _POSITION_OVERLAY_STATE[overlay_id] = overlay

    await _broadcast({"type": "position_overlay_update", "overlay": overlay})


async def get_overlay_snapshot(overlay_id: str) -> dict[str, Any] | None:
    oid = str(overlay_id or "")
    if not oid:
        return None

    async with _STATE_LOCK:
        current = _POSITION_OVERLAY_STATE.get(oid)
        if not isinstance(current, dict):
            return None
        return dict(current)


async def patch_overlay_and_broadcast(
    overlay_id: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    oid = str(overlay_id or "")
    if not oid or not isinstance(patch, dict):
        return None

    async with _STATE_LOCK:
        current = _POSITION_OVERLAY_STATE.get(oid)
        if not isinstance(current, dict):
            return None

        merged = {**current, **patch, "id": oid}
        _POSITION_OVERLAY_STATE[oid] = merged

    await _broadcast({"type": "position_overlay_update", "overlay": merged})
    return merged


async def clear_overlay_and_broadcast(overlay_id: str, exit_ts: int | None = None) -> None:
    oid = str(overlay_id or "")
    if not oid:
        return

    async with _STATE_LOCK:
        _POSITION_OVERLAY_STATE.pop(oid, None)

    await _broadcast({"type": "position_overlay_clear", "id": oid, "exitTs": exit_ts})
