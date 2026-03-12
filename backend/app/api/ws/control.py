## backend/app/api/ws/control.py

from typing import Set
from fastapi import WebSocket
from core.utils.log_utils import log

CONTROL_WS_CLIENTS: Set[WebSocket] = set()

async def register_control_client(ws: WebSocket):
    await ws.accept()
    CONTROL_WS_CLIENTS.add(ws)
    log("[control-ws] client connected (%d total)", len(CONTROL_WS_CLIENTS))

async def unregister_control_client(ws: WebSocket):
    if ws in CONTROL_WS_CLIENTS:
        CONTROL_WS_CLIENTS.remove(ws)
        log("[control-ws] client disconnected (%d total)", len(CONTROL_WS_CLIENTS))

async def broadcast_control_event(event: dict):
    if not CONTROL_WS_CLIENTS:
        return

    dead = []
    for ws in list(CONTROL_WS_CLIENTS):
        try:
            await ws.send_json(event)
        except Exception as e:
            log("[control-ws] send failed: %s", e)
            dead.append(ws)

    for ws in dead:
        try:
            await ws.close()
        except Exception:
            pass
        await unregister_control_client(ws)
