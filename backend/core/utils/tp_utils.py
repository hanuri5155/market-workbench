## backend/core/trading/tp_utils.py

import math
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from core.state import shared_state
def truncate_decimal(value: float, digits: int = 8) -> float:
    return float(Decimal(str(value)).quantize(Decimal(f"1e-{digits}"), rounding=ROUND_DOWN))

def format_4f(value: float) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('1.0000'), rounding=ROUND_DOWN)}"

def format_4f_with_comma(value: float) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('1.0000'), rounding=ROUND_DOWN):,}"

def format_signed_4f_with_comma(value: float) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('1.0000'), rounding=ROUND_DOWN):+,}"

def format_signed_4f_with_comma_round(value: float) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('1.0000'), rounding=ROUND_HALF_UP):+,}"

def format_1f_with_comma(value: float) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('1.0'), rounding=ROUND_DOWN):,}"

def floor_to_one_decimal(price: float) -> float:
    return math.floor(price * 10) / 10

# 분할 수에 따라 비율 리스트 반환
# ex) [0.33, 0.33, 0.34] 또는 [0.5, 0.5]
def get_tp_ratios() -> list[float]:
    tp_partition = int(shared_state.current_config.get("tp_partition"))
    if tp_partition == 2:
        return [0.5, 0.5]
    elif tp_partition == 3:
        return [0.33, 0.33, 0.34]
    else:
        raise ValueError(f"지원되지 않는 TP 분할 수: {tp_partition}")

# 진입가 대비 % 상승 TP 계산
def calculate_percentage_tp(entry_price: float, pct: float, side: str) -> float:
    if side == "Buy":
        return floor_to_one_decimal(entry_price * (1 + pct / 100))
    else:
        return floor_to_one_decimal(entry_price * (1 - pct / 100))
