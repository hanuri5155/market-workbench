## backend/app/auth/otp/middleware.py

from typing import List, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .sessions import validate_session


# /api/** 요청에 대해 otp_session 쿠키를 검사해서
# 세션이 없으면 401(OTP_REQUIRED)을 돌려주는 미들웨어
#
# allow_paths: OTP 검증을 건너뛸 경로 prefix 리스트
#              (헬스체크, OTP 검증 엔드포인트 등)
class OTPAuthMiddleware(BaseHTTPMiddleware):

    def __init__(self, app, allow_paths: Optional[List[str]] = None):
        super().__init__(app)
        self.allow_paths = allow_paths or []

    async def dispatch(self, request, call_next):
        path = request.url.path

        # 1) 허용된 prefix는 그냥 통과
        for prefix in self.allow_paths:
            if path.startswith(prefix):
                return await call_next(request)

        # 2) API가 아닌 경로(/ws, /docs 등)는 OTP 체크 안 함
        if not path.startswith("/api"):
            return await call_next(request)

        # 3) otp_session 쿠키 검사
        session_id = request.cookies.get("otp_session")
        if not session_id or not validate_session(session_id):
            # 프론트에서 구분하기 쉽게 detail을 OTP_REQUIRED로 고정
            return JSONResponse(
                status_code=401,
                content={"detail": "OTP_REQUIRED"},
            )

        # 4) 정상 세션이면 다음으로 진행
        return await call_next(request)
