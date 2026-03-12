import json

from core.ws.ws_template import websocket_handler
from core.ws.handlers.execution_handler import handle_execution_message  # re-export
from core.ws.handlers.position_handler import handle_position_message    # re-export


# execution + position 토픽을 한 커넥션에서 처리 (라우터 전용)
async def handle_private_ws_message(ws, message: str):
    try:
        data = json.loads(message)
    except Exception:
        return

    topic = data.get("topic")
    if topic == "execution":
        await handle_execution_message(ws, message)
        return
    if topic == "position":
        await handle_position_message(ws, message)
        return
    return


async def start_execution_ws():
    await websocket_handler(
        url="wss://stream.bybit.com/v5/private",
        subscribe_args=["execution", "position"],
        label="execution_ws",
        message_handler=handle_private_ws_message,
    )
