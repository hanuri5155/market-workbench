from __future__ import annotations


# Bybit side('Buy'/'Sell') 또는 Long/Short 계열을 'Long'/'Short'로 정규화. 알 수 없으면 None
def normalize_bybit_side(side: str | None) -> str | None:
    if side is None:
        return None
    s = str(side).strip()
    if not s:
        return None
    low = s.lower()
    if low == "buy":
        return "Long"
    if low == "sell":
        return "Short"
    if low == "long":
        return "Long"
    if low == "short":
        return "Short"
    if s.upper() == "LONG":
        return "Long"
    if s.upper() == "SHORT":
        return "Short"
    return None
