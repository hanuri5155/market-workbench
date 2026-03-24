import os, time, asyncio
from core.utils.log_utils import log

async def start_bot_heartbeat():
    path = os.getenv("BOT_HEARTBEAT_PATH", "/tmp/bot_heartbeat")
    interval = float(os.getenv("BOT_HEARTBEAT_SEC", "5"))
    log(f"❤️ [Heartbeat] start: path={path}, interval={interval}s")

    while True:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(int(time.time())))
        except Exception as e:
            log(f"⚠️ [Heartbeat] write failed: {e}")
        await asyncio.sleep(interval)
