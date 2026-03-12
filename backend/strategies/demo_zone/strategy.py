from __future__ import annotations

import asyncio
import os
import time

from app.api.ws.zone_state import broadcast_zone_state
from core.state import shared_state
from core.utils.log_utils import log
from core.ws.price_dispatcher import register_price_handler
from strategies.base.interfaces import StrategyRuntime
from strategies.demo_zone.notifier import start_demo_zone_notifier
from strategies.demo_zone.push_listener import start_demo_zone_push_listener

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
DEMO_TF = str(os.getenv("DEMO_ZONE_INTERVAL", "15"))
DEMO_EMIT_EVERY_TICKS = max(int(os.getenv("DEMO_ZONE_EMIT_EVERY_TICKS", "12")), 1)

_demo_tick_count = 0


def _build_zone_payload(price: float) -> dict:
    width = max(round(price * 0.0012, 2), 8.0)
    start_ms = int(time.time() * 1000)
    upper = round(price + width, 2)
    lower = round(price - width, 2)
    entry = round(price, 2)
    sl = round(price - width * 1.4, 2)
    zone_id = f"demo-zone-{DEMO_TF}-{start_ms}"

    return {
        "id": zone_id,
        "symbol": SYMBOL,
        "intervalMin": int(DEMO_TF),
        "tf": DEMO_TF,
        "side": "LONG",
        "startTs": start_ms,
        "endTs": None,
        "entry": entry,
        "sl": sl,
        "upper": upper,
        "lower": lower,
        "isBroken": False,
        "isActive": True,
        "baseEntry": entry,
        "baseSl": sl,
        "baseUpper": upper,
        "baseLower": lower,
        "entryOverride": None,
    }


async def _handle_demo_zone_tick(price: float):
    global _demo_tick_count
    if price <= 0:
        return

    _demo_tick_count += 1
    if (_demo_tick_count % DEMO_EMIT_EVERY_TICKS) != 1:
        return

    zone = _build_zone_payload(float(price))
    shared_state.last_demo_zone = zone

    event = {
        "type": "zone_delta",
        "symbol": SYMBOL,
        "tf": DEMO_TF,
        "delta": {
            "created": [zone],
            "broken": [],
        },
        "server_ts": int(time.time() * 1000),
        "seq": int(time.time() * 1000),
    }
    await broadcast_zone_state(event)


def register_demo_zone_handler():
    register_price_handler(_handle_demo_zone_tick)
    log("🧪 [DemoZone] registered price handler")


async def handle_demo_zone_stoploop():
    while True:
        await asyncio.sleep(60.0)


def build_demo_zone_runtime() -> StrategyRuntime:
    return StrategyRuntime(
        provider="demo_zone",
        display_name="Structure Zone",
        register_hooks=[register_demo_zone_handler],
        background_tasks=[
            handle_demo_zone_stoploop,
            start_demo_zone_notifier,
            start_demo_zone_push_listener,
        ],
        notes="Public-safe demo strategy. No live alpha is included.",
    )
