## backend/app/auth/otp/sessions.py

from datetime import datetime, timedelta, timezone
from typing import Dict
import secrets

# 세션 유지 시간 (일 단위로 생각하기 쉽도록)
SESSION_TTL_DAYS = 3  # 예: 3일 동안 사용 없으면 자동 만료

# 내부적으로는 분 단위 값도 같이 써도 됨
SESSION_TTL_MINUTES = SESSION_TTL_DAYS * 24 * 60

# 메모리 기반 세션 저장소: {session_id: 만료시간}
_SESSIONS: Dict[str, datetime] = {}


# 새 세션 ID 생성과 만료시간 등록
# - OTP 인증 성공 시 한 번만 호출
def create_session() -> str:
    session_id = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)                             #  타임존 포함 UTC
    expires_at = now + timedelta(minutes=SESSION_TTL_MINUTES)
    _SESSIONS[session_id] = expires_at
    return session_id


# 세션 ID가 존재하고, 아직 안 만료됐는지 확인
# ▷ 슬라이딩 만료 방식:
#    - 유효한 세션이면 만료시간을 '지금 + TTL'로 갱신
#    - 즉, 계속 쓰는 동안은 세션이 살아 있고,
#      TTL 기간 동안 완전 미사용일 때만 만료
def validate_session(session_id: str) -> bool:
    if not session_id:
        return False

    expires_at = _SESSIONS.get(session_id)
    if expires_at is None:
        return False

    now = datetime.now(timezone.utc)  
    if expires_at < now:
        # 이미 만료된 세션은 정리
        _SESSIONS.pop(session_id, None)
        return False

    # 슬라이딩: 유효하면 만료시간을 다시 연장
    new_expires_at = now + timedelta(minutes=SESSION_TTL_MINUTES)
    _SESSIONS[session_id] = new_expires_at

    return True
