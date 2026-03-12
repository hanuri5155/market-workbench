# Market Workbench 백엔드 구조

ICT 이론 기반 트레이딩에서 겹쳐지는 조건을 zone 오버레이로 보여주고, PC/모바일 토글 인터랙션까지 이어지게 하는 백엔드 분리 구조.

## 설계 원칙

- `app/`
  FastAPI 경계면 전용
- `core/`
  재사용 가능한 런타임 로직 전용
- `strategies/`
  교체 가능한 전략 공급자 전용

## `app/`

- `api_app.py`
  FastAPI 앱 조립, 미들웨어, 내부 이벤트 엔드포인트, WebSocket 등록
- `api/router.py`
  차트, zone 토글, 포지션 오버레이용 REST 엔드포인트
- `api/ws/`
  zone 상태, 포지션 오버레이, control 채널 브로드캐스트
- `auth/otp/`
  OTP 검증, 세션 발급, 요청 보호
- `db/`
  SQLAlchemy 세션, 모델, 스키마, CRUD

## `core/`

- `config/`
  설정 로드, 전략 플래그 캐시 갱신
- `persistence/`
  MySQL 접근과 런타임 저장소 보조 함수
- `state/`
  프로세스 간 공유 상태와 스냅샷
- `operations/`
  heartbeat, watchdog, 상태 점검 유틸
- `notifications/`
  Telegram 알림과 내부 오버레이 통지
- `trading/`
  Bybit 기준 주문 실행 경계와 보조 로직
- `ws/`
  Bybit 기준 시세, 캔들, 체결, 포지션 스트림 처리
- `tools/`
  운영 보조 스크립트와 테스트용 도구

## `strategies/`

- `base/`
  전략 인터페이스와 로더
- `demo_zone/`
  구조 설명용 예시 전략

## 현재 공개 범위

- 전략 공급자는 loader 기준으로 교체 가능
- zone 중심 데이터 흐름과 전략 연결 경계 설명이 공개 범위
- 운영 시크릿과 실배포 환경 구성은 현재 저장소 범위 밖
