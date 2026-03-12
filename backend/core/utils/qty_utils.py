from __future__ import annotations

from decimal import Decimal, ROUND_DOWN


# 수량(q)을 LOT step 격자에 맞춘 '내림' 스냅
#
# - Decimal(str(q)) 사용으로 float 오차 완화
# - eps: 경계 바로 아래(예: 0.004999999)에서 한 스텝 덜 내려가는
#   현상을 완화하기 위한 아주 작은 보정값(기본 0)
#
# Args:
#     q: 수량
#     step: LOT step (예: Decimal('0.001'))
#     eps: 경계 보정 epsilon (기본 0)
#
# Returns:
#     step 격자에 맞춘 내림 스냅 결과(float)
def floor_to_step(q: float, *, step: Decimal, eps: Decimal = Decimal("0")) -> float:
    d = Decimal(str(q)) + eps
    dq = (d / step).to_integral_value(rounding=ROUND_DOWN) * step
    return float(dq)
