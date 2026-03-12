from __future__ import annotations

import time

DEMO_SYMBOL = "BTCUSDT"


def incremental_update_after_rest_confirmed(*, symbol: str, interval_min: int, candle: dict) -> dict:
    close_price = float(candle.get("close") or 0.0)
    if close_price <= 0:
        return {"created": [], "broken": []}

    width = max(round(close_price * 0.001, 2), 8.0)
    start_ts = int(candle.get("start") or int(time.time() * 1000))
    zone = {
        "id": f"demo-zone-{interval_min}-{start_ts}",
        "symbol": symbol or DEMO_SYMBOL,
        "intervalMin": int(interval_min),
        "tf": str(interval_min),
        "side": "LONG",
        "startTs": start_ts,
        "endTs": None,
        "entry": round(close_price, 2),
        "sl": round(close_price - width * 1.5, 2),
        "upper": round(close_price + width, 2),
        "lower": round(close_price - width, 2),
        "isBroken": False,
        "isActive": True,
        "baseEntry": round(close_price, 2),
        "baseSl": round(close_price - width * 1.5, 2),
        "baseUpper": round(close_price + width, 2),
        "baseLower": round(close_price - width, 2),
        "entryOverride": None,
    }
    return {"created": [zone], "broken": []}
