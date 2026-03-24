## backend/core/ws/price_dispatcher.py

import os, json
from core.utils.log_utils import log
from core.ws.ws_template import websocket_handler
from core.state.shared_state import update_price

# 환경 설정
SYMBOL = os.getenv("SYMBOL")

# 등록된 콜백 핸들러들 (strategy 모듈에서 등록할 수 있음)
_price_handlers = []

# 전략 모듈이 콜백을 등록할 때 사용
def register_price_handler(handler):
    if handler not in _price_handlers:      
        _price_handlers.append(handler)

# WebSocket 메시지 핸들러
async def _handle_price_message(ws, message: str):
    try:
        data = json.loads(message)
        if "topic" in data and data["topic"].startswith("publicTrade."):
            trades = data.get("data", [])
            if not trades:
                return

            last_trade = trades[-1]
            price_str = last_trade.get("p")
            if price_str is None:
                log(f"⚠️ [price_dispatcher] 수신된 가격이 None입니다 → 데이터: {last_trade}")
                return
            price = float(price_str)
            symbol = data["topic"].split(".")[-1]

            # 등록된 핸들러들에 브로드캐스트
            if update_price(symbol, price):
                for handler in _price_handlers:
                    try:
                        await handler(price)
                    except Exception as e:
                        log(f"❌ [price_dispatcher] 핸들러 오류 in {handler.__name__}: {e}")

    except Exception as e:
        log(f"❌ [price_dispatcher] 메시지 처리 오류: {e}")

# WebSocket 실행 함수
async def start_price_dispatcher():
    subscribe_args = [f"publicTrade.{SYMBOL}"]
    await websocket_handler(
        url="wss://stream.bybit.com/v5/public/linear",
        subscribe_args=subscribe_args,
        label="price_dispatcher_ws",
        message_handler=_handle_price_message,
        auth_required=False
    )
