from __future__ import annotations

from typing import Set

from fastapi import WebSocket

from core.utils.log_utils import log

ZONE_WS_CLIENTS: Set[WebSocket] = set()

async def register_client(ws: WebSocket):
    await ws.accept()
    ZONE_WS_CLIENTS.add(ws)
    log("[zone-ws] client connected (%d total)", len(ZONE_WS_CLIENTS))


async def unregister_client(ws: WebSocket):
    if ws in ZONE_WS_CLIENTS:
        ZONE_WS_CLIENTS.remove(ws)
        log("[zone-ws] client disconnected (%d total)", len(ZONE_WS_CLIENTS))


async def broadcast_zone_state(event: dict):
    payload = dict(event)
    if not ZONE_WS_CLIENTS:
        log("[zone-ws] no clients to broadcast, skip. event=%s", payload)
        return

    dead_clients = []
    for ws in list(ZONE_WS_CLIENTS):
        try:
            await ws.send_json(payload)
        except Exception as exc:
            log("[zone-ws] send failed, marking client dead: %s", exc)
            dead_clients.append(ws)

    for ws in dead_clients:
        try:
            await ws.close()
        except Exception:
            pass
        await unregister_client(ws)
