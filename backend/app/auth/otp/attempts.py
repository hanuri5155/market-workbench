## backend/app/auth/otp/attempts.py

from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, Optional

#  몇 번까지 틀리게 허용할지
MAX_ATTEMPTS = 5

#  차단 시간 (분 단위)
BLOCK_MINUTES = 5

# 내부 저장소: { key(IP 등): {"failed": int, "blocked_until": datetime|None} }
_AttemptsStore = Dict[str, Dict[str, Optional[datetime]]]
_ATTEMPTS: _AttemptsStore = {}


# 타임존 포함된 UTC 현재 시각
def _now() -> datetime:
    return datetime.now(timezone.utc)


# key(IP 등) 기준 현재 상태 조회
# - 차단 시간이 지났으면 자동으로 상태를 초기화
def _get_state(key: str) -> Dict[str, Optional[datetime]]:
    now = _now()
    info = _ATTEMPTS.get(key)

    if not info:
        info = {"failed": 0, "blocked_until": None}
    else:
        blocked_until = info.get("blocked_until")
        if blocked_until is not None and blocked_until <= now:
            # 차단 기간이 끝났으면 리셋
            info = {"failed": 0, "blocked_until": None}

    _ATTEMPTS[key] = info
    return info


# 현재 key가 차단 상태인지 확인
# 반환값: (차단여부, 남은 시간(초))
def is_blocked(key: str) -> Tuple[bool, int]:
    info = _get_state(key)
    blocked_until = info.get("blocked_until")

    if blocked_until is None:
        return False, 0

    now = _now()
    if blocked_until > now:
        remain_sec = int((blocked_until - now).total_seconds())
        return True, remain_sec

    return False, 0


# OTP 검증 실패 시 호출
# 실패 횟수를 1 증가시키고, MAX_ATTEMPTS 이상이면 BLOCK_MINUTES 동안 차단
def register_failure(key: str) -> None:
    info = _get_state(key)
    failed = int(info.get("failed", 0)) + 1
    info["failed"] = failed

    if failed >= MAX_ATTEMPTS:
        info["blocked_until"] = _now() + timedelta(minutes=BLOCK_MINUTES)

    _ATTEMPTS[key] = info


# OTP가 성공했을 때, 해당 key의 실패 기록을 초기화
def reset_attempts(key: str) -> None:
    if key in _ATTEMPTS:
        del _ATTEMPTS[key]
