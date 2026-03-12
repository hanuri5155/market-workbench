from __future__ import annotations

from app.api.ws.zone_state import broadcast_zone_state


async def _notify_zone_state_sync(symbol: str, interval_min: int):
    # zone 상태 동기화 이벤트 강제 발행용
    await broadcast_zone_state(
        {
            "type": "zone_state_sync",
            "symbol": symbol,
            "tf": str(interval_min),
            "boxes": [],
        }
    )


async def _finalize_zone_after_debounce(*args, **kwargs):
    # 후처리 훅 자리 유지용
    return None
