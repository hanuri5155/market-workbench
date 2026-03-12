from __future__ import annotations

import asyncio

from core.utils.log_utils import log


async def start_demo_zone_notifier():
    log("🧪 [DemoZone] notifier started (demo mode, no live alerts)")
    while True:
        await asyncio.sleep(300.0)
