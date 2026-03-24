# Market Workbench 아키텍처

기준: public snapshot + 2026-06-23 active chart pipeline / research validation update

## 문서 역할

- 공개 저장소에서 런타임 경계와 데이터 흐름을 빠르게 확인하기 위한 기준 문서
- API 서버, bot 프로세스, chart ingest/NATS 경로, 프론트엔드, 저장소, 운영 관측 지점을 한 번에 연결해서 보는 문서
- 세부 전략 규칙이 아니라 백엔드/인프라 설계 의도와 공개 가능한 연구 검증 방식을 설명하는 문서

## 시스템 컨텍스트

- 사용자: Google OTP 인증 후 웹 대시보드 사용
- 거래소: Bybit REST/WS로 시세, 캔들, 포지션, 주문/체결 처리
- API 서버: FastAPI 기반 REST/WS gateway
- Bot 프로세스: 시장 데이터 수신, demo strategy callback, 주문 실행 경계, 포지션/체결 감시
- MySQL: 계정, 세션, 포지션, 체결, 캔들, Structure Zone 상태, 전략 플래그 저장
- NATS JetStream: chart ingest event의 active fanout 및 final/reconcile durable write 경로
- Redis: 기본 runtime 제외, rollback profile only
- 관측 채널: `/healthz`, heartbeat, event loop watchdog, API WS gateway metrics, Zabbix, Telegram

## 런타임 경계

### 1. API 서버

- 진입점: `backend/app/api_app.py`
- 역할:
  - OTP middleware와 인증 API
  - UI용 REST API
  - `/ws/chart-candles`, `/ws/zones`, `/ws/position-overlay`, `/ws/control`
  - NATS active gateway가 받은 candle event를 브라우저 WS로 fanout
  - `/ws/chart-candles` fanout/backpressure metric 수집
  - local-only `/internal/chart-websocket-metrics` export
  - chart ingest shadow, NATS shadow subscriber, NATS active gateway lifecycle 관리
  - startup 시 Structure Zone projection read model 보장
  - event loop watchdog 실행

### 2. Bot 프로세스

- 진입점: `backend/main.py`
- 역할:
  - Bybit public/private WS 수신
  - candle detector 실행과 캔들 저장
  - demo strategy handler 등록
  - 주문 실행 경계, position/execution watcher, funding poller
  - `/tmp/bot_heartbeat` 갱신
  - `/tmp/shared_state.json` snapshot 기록

### 3. Frontend

- 진입점: `frontend/src/main.jsx`, `frontend/src/App.jsx`
- 역할:
  - OTP gate
  - UI v3 workspace
  - 실시간 차트와 Structure Zone deck
  - `/ui-v2` 비교/롤백 route 유지
  - `/ui-preview` OTP 우회 디자인 검토 route 유지

## 상위 데이터 흐름

```text
Browser
  <-> FastAPI REST/WS
      <-> MySQL
      <-> in-memory WS state
      <-  NATS JetStream active gateway

Bot
  <-> Bybit public/private WS
  <-> Bybit REST
  ->  MySQL / JSON runtime store
  ->  Telegram
```

현재 browser-facing live chart 주 경로:

```text
Bybit public kline WS
  -> chart ingest runtime
  -> NATS JetStream
  -> api active gateway
  -> /ws/chart-candles
  -> browser
```

final/reconcile durable 저장 경로:

```text
NATS JetStream critical lane
  -> chart-storage worker
  -> MySQL candles
```

과거 bot internal HTTP fanout 경로는 공개 스냅샷에서 retired path로 정리했다. REST 확정봉 보정 endpoint는 Structure Zone 동기화와 차트 보정 이벤트용으로 남긴다.

## 핵심 흐름

- 차트 진입: current TF candle REST fetch -> first usable paint -> grouped startup snapshot -> overlay attach
- 실시간 가격: Bybit kline WS -> chart ingest -> NATS -> API active gateway -> `/ws/chart-candles`
- 확정봉 fanout: final/reconcile 이벤트 -> NATS critical lane -> API active gateway -> `/ws/chart-candles`
- 확정봉 저장: NATS critical lane -> chart-storage worker -> MySQL `candles`
- Structure Zone: DB canonical state/projection -> REST snapshot + WS delta/state sync -> chart overlay/notification
- Strategy research: private 후보/label/source provider 흐름 -> public aggregate-only validation helper와 연구 문서
- 포지션 오버레이: Bybit position/execution WS -> bot overlay event -> API memory state -> `/ws/position-overlay`
- 제어 토글: UI/API -> `strategy_flags` DB -> `/ws/control` -> bot cache reload
- NATS shadow: active 전환 검증/비교용 compare/log/metric 경로로 유지

## API WebSocket gateway metrics

`/ws/chart-candles`는 connection/subscription/fanout/send 상태를 process-local metric으로 기록한다.

- export endpoint: `GET /internal/chart-websocket-metrics`
- guard: local client만 허용하며 public reverse proxy path는 404로 둔다.
- Zabbix key prefix: `market_workbench.api_ws.*`
- 주요 counters:
  - `active_connections`, `subscribed_connections`, `subscriptions_by_tf`
  - `broadcast_duration_p95_ms`, `broadcast_duration_p99_ms`
  - `send_duration_p99_ms`, `slow_send_250ms_total`, `slow_send_1000ms_total`
  - `active_send_failure_total`, `closing_send_failure_total`
  - `broadcast_send_failure_total`, `teardown_close_race_total`, `send_skipped_closing_total`

load-test 판정에서는 기존 aggregate `send_failure_total`/`error_total`만 단독 fail 기준으로 쓰지 않는다. active-run failure는 `active_send_failure_total`을 우선 보고, load generator 종료 직후 close race는 `closing_send_failure_total`/`teardown_close_race_total`로 분리한다.

## 저장소 책임

### MySQL

- `accounts`, `sessions`
- `positions`, `fills`
- `candles`
- `zone_state`
- `zone_projection`
- `strategy_flags`

### NATS JetStream / Chart Storage

- API active gateway: partial/critical event를 browser WS payload로 변환
- `chart-storage`: final/reconcile event를 MySQL에 쓰는 별도 worker
- `chart_event_dedupe`: durable worker의 event idempotency guard
- `CHART_STORAGE_FILTER_SUBJECTS`: 정확한 critical subject만 여러 개 고를 때 쓰는 multi-filter

### JSON / 파일

- `EXECUTION_DATA_STORE_PATH`: 포지션/체결 진행 상태
- `CONFIG_PATH`: 운영 config
- `/tmp/shared_state.json`: UI/운영 확인용 state snapshot
- `/tmp/bot_heartbeat`: bot healthcheck 파일

실제 운영 config, storage, log 파일은 공개 레포에 포함하지 않는다.

## Broker / Active Gateway

NATS JetStream 경로는 active fanout과 durable write를 분리한다.

- Stream: partial lane과 critical(final/reconcile) lane 분리
- Consumer: API gateway active consumer, chart-storage durable consumer
- Ack policy: explicit
- Drop policy: schema mismatch는 log 후 terminate, 처리 예외는 `nak` with delay
- 관찰값: pending, ack pending, redelivery, schema drop, last error, fanout/drop/write count
- shadow compare는 여전히 전환 검증과 관측용으로 유지

Redis는 durable broker가 아니다. 공개 compose에는 `redis-rollback` profile로만 남고 기본 runtime에서는 올라오지 않는다.

## 차트 런타임

차트 경로의 핵심은 첫 paint를 막는 작업을 줄이고, 정합성 보정은 별도 경로로 분리하는 것이다.

- current TF candle fetch가 먼저 chart paint를 만든다.
- multi-timeframe overlay는 projection read model 기반 startup snapshot으로 붙는다.
- `/ws/zones`는 Structure Zone delta/state sync를 전달한다.
- `/ws/chart-candles`는 최신 candle update/reconcile 이벤트를 전달하는 브라우저용 계약이다.
- UI v3는 chart-first workspace이며 UI v2는 비교 route로 남는다.

상세 수치는 [차트 성능 개선 사례](CHART_PERFORMANCE_CASE_STUDY_PUBLIC.md)에 정리되어 있다.

## 전략 연구 검증 경계

전략 연구는 런타임과 분리해서 다룬다. 공개 레포의 `demo_zone`은 Structure Zone event가 API/WS/UI에 어떻게 연결되는지 보여주는 샘플이고, 실제 후보 산출 기준은 포함하지 않는다.

최근 공개 스냅샷에서는 연구 흐름을 다음처럼 정리했다.

- 후보 구간, label, source provider, bounded rerun을 분리해서 설명한다.
- q4, no-entry, post-formation pre-entry, MA regime 같은 내부 용어는 먼저 풀어 설명하고, 이후에 축약어를 사용한다.
- 내부 candle/source만으로 남은 후보는 external official source 검증 전까지 provisional로 둔다.
- `backend/core/research/public_validation_summary.py`는 raw row나 candidate id를 내보내지 않는 aggregate-only summary 예제다.
- 이 코드는 실전 전략 조건, score threshold, UI highlight, 자동진입 규칙을 포함하지 않는다.

자세한 연구 문맥은 [전략 연구 방식](STRATEGY_RESEARCH_WORKFLOW.md)과 [Structure Zone 연구 공개 요약](research/structure-zone-research-public-20260623.md)을 기준으로 한다.

## WebSocket capacity summary

2026-05-31 공개 요약 기준:

- 1000 total WebSockets: PASS 이후 60분 soak PASS.
- 1500 total WebSockets: close-path 보정 후 PASS, 60분 revalidation PASS.
- 2000 total WebSockets: discovery에서 한 번 PARTIAL이 있었지만, 단일 재확인과 60분 soak에서 PASS-clean.
- 공개 기준 `C_pass_soak=2000`, 보수 운영 기준 `C_safe_public=1400`.
- 다음 미검증 단계는 2500 total WebSockets.

자세한 수치와 공개/비공개 경계는 [WebSocket load-test / metrics 요약](ops/websocket-loadtest-capacity-public-20260531.md)을 기준으로 한다.

## 배포 구조

- GitHub tag `v*` push
- GitHub Actions가 API/BOT 이미지를 GHCR에 multi-arch build/push
- self-hosted runner가 compose host에서 `.env` 이미지 태그 갱신
- `docker compose pull/up`
- API는 `/healthz`, BOT은 heartbeat 파일로 healthcheck

상세 절차는 [배포 구조](DEPLOYMENT.md)를 기준으로 한다.

## 공개 레포 기준 주의점

- 이 레포는 플랫폼/운영 구조를 보여주는 공개 스냅샷이다.
- 실운영 키, config, storage, log는 제외한다.
- demo strategy는 구조 시연용이며 실전 전략 규칙을 포함하지 않는다.
- 전략 세부 규칙보다 API/bot 분리, event contract, read model, 배포/관측 구조, 연구 검증 경계를 읽는 것이 목적이다.

## 관련 문서

- [배포 구조](DEPLOYMENT.md)
- [Chart event contract](CHART_EVENT_CONTRACT.md)
- [Chart ingest broker design](CHART_INGEST_BROKER_DESIGN.md)
- [Zabbix 기반 원격 모니터링](Zabbix_Agent2.md)
- [백엔드 구조](BACKEND_STRUCTURE.md)
- [UI v3 정리](UI_V3_PREVIEW.md)
- [차트 성능 개선 사례](CHART_PERFORMANCE_CASE_STUDY_PUBLIC.md)
- [전략 연구 방식](STRATEGY_RESEARCH_WORKFLOW.md)
- [Structure Zone 연구 공개 요약](research/structure-zone-research-public-20260623.md)
- [WebSocket load-test / metrics 요약](ops/websocket-loadtest-capacity-public-20260529.md)
