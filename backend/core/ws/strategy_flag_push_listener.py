## backend/core/ws/strategy_flag_push_listener.py

import os, json, asyncio, websockets
from core.utils.log_utils import log
from core.config.config_utils import refresh_strategy_flags_cache_from_db, log_strategy_flags_from_db

async def start_strategy_flag_push_listener():
    # API 서버 control WS 구독용
    # strategy_flags 변경 시 봇 메모리 캐시 즉시 동기화하기 위함
    url = os.getenv("STRATEGY_FLAGS_WS_URL", "ws://127.0.0.1:8000/ws/control")

    while True:
        try:
            async with websockets.connect(
                url,
                ping_interval=25,
                ping_timeout=40,
                open_timeout=15,
                close_timeout=10,
            ) as ws:
                log(f"📡 [StrategyFlags] connected: {url}")

                # 연결 직후 1회 선반영
                refresh_strategy_flags_cache_from_db()

                while True:
                    msg = await ws.recv()
                    try:
                        data = json.loads(msg)
                    except Exception:
                        continue

                    if data.get("type") == "strategy_flags_updated":
                        refresh_strategy_flags_cache_from_db()
                        log_strategy_flags_from_db(prefix="✅ [StrategyFlags] updated:")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"⚠️ [StrategyFlags] ws disconnected, retry: {e}")
            await asyncio.sleep(2.0)
