from __future__ import annotations

import asyncio

from core.utils.log_utils import log


async def start_demo_zone_push_listener():
    log("🧪 [DemoZone] zone state listener started (demo mode)")
    while True:
        await asyncio.sleep(300.0)
