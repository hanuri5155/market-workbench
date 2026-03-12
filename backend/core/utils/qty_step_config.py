from __future__ import annotations

import os
from decimal import Decimal

from core.utils.qty_utils import floor_to_step


# LOT 스텝 격자 스냅 (부동소수 잔량 방지)
QTY_STEP = Decimal(os.getenv("QTY_STEP", "0.001"))
QTY_FLOOR_EPS = Decimal(os.getenv("QTY_FLOOR_EPS", "0.0000005"))  # 경계 근접치 보정


# 0.001 스텝 내림 스냅
#
# 경계 바로 아래(예: 0.004999999)에서 한 스텝 덜 내려가는 현상을 막기 위해
# 아주 작은 epsilon(QTY_FLOOR_EPS) 추가 후 floor 적용
def floor_to_step_qty(q: float) -> float:
    return floor_to_step(q, step=QTY_STEP, eps=QTY_FLOOR_EPS)
