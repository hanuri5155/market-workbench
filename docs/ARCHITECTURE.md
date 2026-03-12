# Market Workbench 아키텍처

## 목적

ICT 이론 기반 트레이딩에서 겹쳐지는 조건을 비교하고, 진입 후보를 차트 오버레이와 토글 인터랙션으로 다루기 위한 웹앱.

프로젝트 핵심:
- 운영 구조와 백엔드 설계
- bot / API / 프론트 분리 구조
- 데이터 저장, 실시간 동기화, 배포 동선
- ICT 관점 조건이 겹치는 진입 후보를 zone 오버레이로 보여주는 흐름
- PC/모바일에서 zone 오버레이 터치/클릭 토글로 판단을 보조하는 인터랙션

## 런타임 경계

1. bot 프로세스
- 진입점: `backend/main.py`
- 역할: 시세 수신, 캔들 적재, strategy loader 경계, 내부 이벤트 발행, heartbeat 기록

2. API 서버
- 진입점: `backend/app/api_app.py`
- 역할: REST/WS 엔드포인트, OTP 게이트, zone 상태 브로드캐스트, 운영 제어

3. 프론트엔드
- 진입점: `frontend/src/main.jsx`
- 역할: 운영 대시보드, 차트 오버레이, zone 터치/클릭 토글, 알림 확인, OTP 기반 접근 제어

## 상위 데이터 흐름

```text
+------------------+           REST/WS            +------------------+
|   Web Browser    |  <----------------------->   |   API Server     |
|   (React UI)     |                              |   (FastAPI)      |
+------------------+                              +------------------+
                                                     ^        |
                                                     |        | internal HTTP + WS push
                                                     |        v
                                                  +------------------+
                                                  |   Trading Bot    |
                                                  |   (async core)   |
                                                  +------------------+
                                                   |           |       \
                                                   |           |        \
                                                   v           v         v
                                             Bybit WS/REST   MySQL    Telegram
```

핵심 흐름:

- 시세 -> bot -> ICT 관점 zone 평가와 주문 판단 -> Bybit -> 체결/포지션 -> MySQL -> API -> UI
- 캔들 확정 -> zone 계산/DB 갱신 -> 내부 API -> `/ws/zones` 브로드캐스트 -> UI
- 체결/포지션 WS -> `execution_data_store` 갱신 -> 내부 오버레이 이벤트 -> API -> UI
- UI 토글 -> API -> DB -> `/ws/control` -> bot 플래그 즉시 재로딩
- zone 활성/entry override 변경 -> API 저장 -> `/ws/zones` 상태 동기화 -> UI 반영

## 관점

- 이 프로젝트의 초점은 ICT 이론 기반 트레이딩을 더 편하게 보조하는 웹앱 구성
- 겹쳐지는 조건 비교 결과를 zone 오버레이로 보여주고 PC/모바일에서 바로 토글할 수 있는 구조
- zone 상태 추적, 오버레이 반영, 운영 플래그 제어, 모니터링 연결을 하나의 화면 흐름으로 묶는 구조
- 실전 전략 로직과 세부 진입 조건은 별도 비공개 구성으로 관리
- 현재 저장소의 거래소 연동 기준은 Bybit V5 WS/REST

## 현재 공개 범위

- 현재 저장소는 차트 오버레이, zone 상태 동기화, 토글 인터랙션, 배포 구조 설명 중심
- 전략 공급자 로더와 주문 경계 분리 구조까지 공개 범위에 포함
- 운영 시크릿과 실배포 환경 구성은 현재 저장소 범위 밖

## 백엔드 핵심 영역

- `app/`
  FastAPI 라우터, WebSocket 채널, OTP, SQLAlchemy 모델/스키마
- `core/`
  설정 캐시, persistence, execution 처리, 알림, heartbeat, 공용 상태
- `strategies/`
  전략 로더와 예시 전략 구조

## 저장 데이터

- MySQL
  `accounts`, `sessions`, `positions`, `fills`, `candles`, `zone_state`, `strategy_flags`
- 런타임 파일
  `storage/candles/*.json`, `storage/bbands/*.json`, `storage/execution_data_store.json`
- 프로세스 스냅샷
  `/tmp/shared_state.json`, `/tmp/bot_heartbeat`

## 운영 관점

- 이미지 단위 배포
  `api`, `bot`
- Compose 구성 대상
  API, bot, MySQL, 필요 시 Redis
- 릴리스 방식
  태그 기반 이미지 빌드 후 SSH 배포
- 모니터링 참고
  [DEPLOYMENT.md](DEPLOYMENT.md), [Zabbix_Agent2.md](Zabbix_Agent2.md)
