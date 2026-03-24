## backend/core/tools/simulated_price_feeder.py

import os, asyncio, json, random
from core.ws.price_dispatcher import _price_handlers
from core.ws.candle_detector import save_candle_to_file
from core.state.shared_state import update_price
from core.utils.log_utils import log

# 환경 설정
SYMBOL = "BTCUSDT"

# 시뮬레이션 캔들 시나리오 경로
CANDLE_FILE_PATH = os.getenv("SIMULATED_CANDLES_PATH", "config/simulated_candles.demo.json")

# 시뮬레이션 종료 캔들 저장
def save_simulated_closed_candle(candle):
    interval = str(candle["interval"])
    kline_data = {
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"]
    }
    save_candle_to_file(interval, kline_data)
    log(f"[SIMULATION] 마감 캔들 저장 완료 (interval={interval}분): {kline_data}")

# 가격 경로 생성
def generate_price_sequence(start_price, end_price, steps, noise_pct=0.0003):
    prices = []
    price_diff = end_price - start_price
    for i in range(steps):
        progress = i / (steps - 1)
        base_price = start_price + price_diff * progress

        # 각 스텝에 작은 흔들림 반영
        noise = base_price * random.uniform(-noise_pct, noise_pct)
        prices.append(round(base_price + noise, 2))

    return prices

# 한 개 캔들 구간의 가격 흐름 생성
def build_candle_price_path(open_price, low_price, high_price, close_price, interval_seconds):
    segment_steps = {
        "open_to_low": max(3, int(interval_seconds * 0.25) // 2),
        "low_to_high": max(3, int(interval_seconds * 0.5) // 2),
        "high_to_close": max(3, int(interval_seconds * 0.25) // 2)
    }

    path = []

    path += generate_price_sequence(open_price, low_price, segment_steps["open_to_low"])
    path += generate_price_sequence(low_price, high_price, segment_steps["low_to_high"])
    path += generate_price_sequence(high_price, close_price, segment_steps["high_to_close"])

    return path

# 시뮬레이션 메인 루프
async def simulated_price_loop():
    if not os.path.exists(CANDLE_FILE_PATH):
        log(f"❌ 시뮬레이션 캔들 파일이 없습니다: {CANDLE_FILE_PATH}")
        return

    with open(CANDLE_FILE_PATH, "r") as f:
        candle_scenario = json.load(f)

    candles = candle_scenario.get("candles", [])

    for candle_idx, candle in enumerate(candles):
        interval_seconds = candle["interval"] * 60  # 예: 5분봉이면 300초

        price_path = build_candle_price_path(
            open_price=candle["open"],
            low_price=candle["low"],
            high_price=candle["high"],
            close_price=candle["close"],
            interval_seconds=interval_seconds
        )

        log(f"🕰️ [{candle_idx+1}/{len(candles)}] 캔들 가격 흐름 생성 완료 - 구간 수: {len(price_path)}")

        for price in price_path:
            update_price(SYMBOL, price)

            for handler in _price_handlers:
                await handler(price)

            log(f"[SIMULATION] 공급된 가격: {price}")

            await asyncio.sleep(0.3)  # 가격 틱 간 대기(초); 값이 작을수록 빠르게 재생

        # 캔들 종료 후 마감 캔들 저장
        save_simulated_closed_candle(candle)

        log(f"[SIMULATION] Structure Zone demo candle closed (#{candle_idx+1})")

    # 마지막 가격 무한 공급 (기존 유지)
    last_price = price_path[-1]
    log(f"🔁 모든 캔들 종료. 마지막 가격 {last_price} 유지 반복 공급 시작.")

    while True:
        update_price(SYMBOL, last_price)
        for handler in _price_handlers:
            await handler(last_price)

        log(f"[SIMULATION] (반복) 공급된 마지막 가격: {last_price}")
        await asyncio.sleep(1.0)
