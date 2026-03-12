## backend/core/utils/smooth_partition.py

import math
from decimal import Decimal
from typing import List, Union

# 0.001 등 최소 스텝(step) 단위로 합 S를 k개로 분할
# 규칙:
#   - 내림차순 a1 >= a2 >= ... >= ak >= step
#   - '서로 다른 값'의 개수 최대화(전부 다르면 best)
#   - 큰 수(앞쪽)에 더 많이 배분하되, 인접 차이(Δ)는 앞에서 뒤로 비증가(매끈)
#   - step 배수만 허용
# 출력 표시 깨짐 방지:
#   - Decimal로 양자화(quantize) 후, 필요시 float/str로 변환하여 '0.009000000000000001' 같은 표시 방지
#   - return_type="float" | "str" | "decimal"
def smooth_partition(
    S: Union[float, str],
    k: int,
    step: Union[float, str] = 0.001,
    *,
    return_type: str = "float",  # "float" | "str" | "decimal"
) -> List[Union[float, str, Decimal]]:

    # --- 정밀도 설정(문자열을 Decimal로 파싱해 정확도 보존) ---
    dec_S = Decimal(str(S))
    dec_step = Decimal(str(step))

    if dec_step <= 0:
        raise ValueError("step must be positive.")
    if k <= 0:
        raise ValueError("k must be positive.")

    # S가 step의 배수인지 확인 (정확한 정수 배수 N)
    N_dec = dec_S / dec_step
    if N_dec != N_dec.to_integral_value():
        raise ValueError(f"S must be a multiple of step. Given S={S}, step={step}.")
    N = int(N_dec)

    # 최소합 검사: 각 조각이 최소 1단위(=step)이므로 N >= k
    if N < k:
        raise ValueError(f"Sum too small: need at least k*step (={k*dec_step}) but S={dec_S}.")

    # --- 서로 다른 값의 최대 개수 D* ---
    # 조건: D(D-1)/2 <= (N - k) → D* = floor((1 + sqrt(1 + 8*(N-k)))/2), 상한은 k
    D_star = min(k, (1 + math.isqrt(1 + 8 * (N - k))) // 2)

    # --- 기본 Δ 구성 (길이 k-1): 앞쪽 D*-1칸만 1, 나머지 0  ---
    deltas = [0] * (k - 1)
    for i in range(D_star - 1):
        deltas[i] = 1

    # 기본합 B(D,k) = D(D+1)/2 + (k-D)
    base_sum = D_star * (D_star + 1) // 2 + (k - D_star)
    B = N - base_sum  # 남는 예산(정수)

    # prefix layer 그리디: 가장 큰 접두사 길이부터 반복 증가
    def triangular(j: int) -> int:
        return j * (j + 1) // 2

    max_j = max(0, D_star - 1)

    for j in range(max_j, 0, -1):
        t = triangular(j)
        if B >= t and t > 0:
            q, B = divmod(B, t)
            if q:
                for i in range(j):
                    deltas[i] += q

    # 남은 B를 j를 줄여가며 소진
    while B > 0 and max_j > 0:
        # B에 맞는 최대 j 선택
        j = min(max_j, (int((1 + math.isqrt(1 + 8 * B)) // 2)))
        if j <= 0:
            j = 1
        for i in range(j):
            deltas[i] += 1
        B -= triangular(j)

    # --- → x 복원 (정수 단위: x_k = 1, 뒤에서 앞으로 누적) ---
    x = [0] * k
    x[-1] = 1
    for i in range(k - 2, -1, -1):
        x[i] = x[i + 1] + deltas[i]

    if sum(x) != N:
        raise RuntimeError(f"Internal error: sum(x)={sum(x)} != N={N}")
    if any(x[i] < x[i + 1] for i in range(k - 1)):
        raise RuntimeError("Internal error: sequence is not non-increasing.")

    # --- 결과 양자화 & 출력 형태 결정 ---
    digits = -dec_step.as_tuple().exponent  # 표시 자릿수(예: step=0.001 → 3)
    vals_dec = [(Decimal(v) * dec_step).quantize(dec_step) for v in x]

    if return_type == "decimal":
        return vals_dec
    elif return_type == "str":
        fmt = f"{{:.{digits}f}}"
        return [fmt.format(float(v)) for v in vals_dec]
    elif return_type == "float":
        # Decimal → float 변환 후에도 사람이 보기엔 깔끔하게 보이도록 한 번 더 반올림
        return [round(float(v), digits) for v in vals_dec]
    else:
        raise ValueError('return_type must be one of: "float", "str", "decimal"')


# -------- 사용 예 --------
if __name__ == "__main__":
    # 1) S=0.034, k=6
    print(smooth_partition(0.044, 3, 0.001, return_type="float"))
    # -> [0.012, 0.009, 0.006, 0.004, 0.002, 0.001]
