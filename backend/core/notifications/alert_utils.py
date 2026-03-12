## backend/core/utils/alert_utils.py

import os, requests
from concurrent.futures import ThreadPoolExecutor
from core.utils.log_utils import log
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_CONNECT_TIMEOUT_SEC = float(os.getenv("TELEGRAM_CONNECT_TIMEOUT_SEC", "2"))
TELEGRAM_READ_TIMEOUT_SEC = float(os.getenv("TELEGRAM_READ_TIMEOUT_SEC", "4"))
TELEGRAM_TIMEOUT = (TELEGRAM_CONNECT_TIMEOUT_SEC, TELEGRAM_READ_TIMEOUT_SEC)
TELEGRAM_ASYNC_SEND = os.getenv("TELEGRAM_ASYNC_SEND", "1") == "1"
TELEGRAM_ASYNC_WORKERS = max(1, int(os.getenv("TELEGRAM_ASYNC_WORKERS", "2")))
_TELEGRAM_EXECUTOR = ThreadPoolExecutor(
    max_workers=TELEGRAM_ASYNC_WORKERS,
    thread_name_prefix="telegram-send",
)

def _send_tg(token: str, chat_id: str, text: str,
             parse_mode: str | None = "HTML",
             disable_preview: bool = True) -> bool:
    if not token:
        log("⚠️ Telegram token이 비어 있습니다.")
        return False
    if not chat_id:
        log("⚠️ Telegram chat_id가 비어 있습니다.")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT)
        if resp.status_code != 200:
            log(f"❌ 텔레그램 알림 실패 → {resp.status_code} {resp.text}")
            return False
        return True
    except Exception as e:
        log(f"❌ 텔레그램 알림 예외 발생: {e}")
        return False


def _send_tg_dispatch(
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str | None = "HTML",
    disable_preview: bool = True,
) -> bool:
    if not TELEGRAM_ASYNC_SEND:
        return _send_tg(token, chat_id, text, parse_mode=parse_mode, disable_preview=disable_preview)

    if not token or not chat_id:
        return _send_tg(token, chat_id, text, parse_mode=parse_mode, disable_preview=disable_preview)

    try:
        _TELEGRAM_EXECUTOR.submit(
            _send_tg,
            token,
            chat_id,
            text,
            parse_mode,
            disable_preview,
        )
        return True
    except Exception as e:
        log(f"⚠️ 텔레그램 비동기 전송 스케줄 실패: {e}")
        return _send_tg(token, chat_id, text, parse_mode=parse_mode, disable_preview=disable_preview)

# 텔레그램 알림 전송 함수 (기본: TELEGRAM_* → 없으면 ZONE_*로 폴백)
# - chat_id는 인자 직접 입력 우선, 없으면 .env의 TELEGRAM_CHAT_ID → ZONE_CHAT_ID 순 조회
# - token은 TELEGRAM_BOT_TOKEN → ZONE_BOT_TOKEN 순 조회
def send_telegram_alert(text: str, chat_id: str | None = None,
                        parse_mode: str | None = "HTML",
                        disable_preview: bool = True) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("ZONE_BOT_TOKEN")
    cid = chat_id or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ZONE_CHAT_ID")
    return _send_tg_dispatch(token, cid, text, parse_mode=parse_mode, disable_preview=disable_preview)

#  임의의 봇/채널(.env 키)로 전송
def send_telegram_alert_by_env(text: str, token_env_key: str, chat_env_key: str,
                               parse_mode: str | None = "HTML",
                               disable_preview: bool = True) -> bool:
    token = os.getenv(token_env_key)
    if not token:
        log(f"⚠️ {token_env_key}이(가) 설정되지 않았습니다.")
        return False
    cid = os.getenv(chat_env_key)
    if not cid:
        log(f"⚠️ {chat_env_key}이(가) 설정되지 않았습니다.")
        return False
    return _send_tg_dispatch(token, cid, text, parse_mode=parse_mode, disable_preview=disable_preview)

#  편의 래퍼
def send_positions_telegram_alert(text: str,
                                  parse_mode: str | None = None,
                                  disable_preview: bool = True) -> bool:
    # parse_mode=None → 현재 포맷(###, backticks)을 안전하게 그대로 표시
    return send_telegram_alert_by_env(
        text, "POSITIONS_BOT_TOKEN", "POSITIONS_CHAT_ID",
        parse_mode=parse_mode, disable_preview=disable_preview
    )

def send_zone_telegram_alert(text: str,
                             parse_mode: str | None = None,
                             disable_preview: bool = True) -> bool:
    return send_telegram_alert_by_env(
        text, "ZONE_BOT_TOKEN", "ZONE_CHAT_ID",
        parse_mode=parse_mode, disable_preview=disable_preview
    )

send_fivecandle_telegram_alert = send_zone_telegram_alert
