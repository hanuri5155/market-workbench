# Market Workbench 백엔드 구조

기준: public snapshot + 2026-06-23 update

이 문서는 백엔드 디렉터리 책임과 주요 실행 흐름을 공개 레포 기준으로 정리한다. 코드를 처음 열었을 때 API 서버와 bot 런타임이 어디서 갈라지고, 어떤 경로로 UI까지 상태가 전달되는지 확인하는 용도다.

## 핵심 원칙

- `app/`: FastAPI HTTP/WS 레이어
- `core/`: 거래소 WS, persistence, trading helper, heartbeat/watchdog 같은 공용 런타임
- `core/research/`: 공개 가능한 aggregate-only 연구 검증 helper
- `strategies/`: 공개용 demo strategy와 strategy interface
- `deploy/`: 이미지 기반 운영 예시

API 서버와 bot은 같은 코드베이스를 쓰지만 프로세스는 분리한다. API는 브라우저와 DB gateway 역할을 하고, bot은 시장 데이터와 주문/체결 런타임을 담당한다.
전략 연구 자체는 공개 가능한 방법론만 문서화하고, 실제 규칙과 파라미터는 이 공개 레포에 두지 않는다.

## app/

- `app/api_app.py`
  FastAPI 앱 생성, OTP middleware, 내부 이벤트 엔드포인트, WebSocket 등록, startup projection 보장, chart ingest/shadow/active/durable service lifecycle
- `app/api/router.py`
  UI용 REST API, OTP 인증 API, strategy flag, candle, position overlay, TP/SL 수정 API, Structure Zone REST snapshot/state API
- `app/api/ws/chart_candles.py`
  UI v3 ticker와 차트가 구독하는 browser-facing candle WS state/fanout
- `app/api/ws/zone_state.py`
  Structure Zone 상태와 delta를 브라우저에 브로드캐스트
- `app/api/ws/position_overlay.py`
  position overlay snapshot/update/clear fanout
- `app/api/ws/control.py`
  bot이 strategy flag 변경을 즉시 받는 control WS
- `app/api/services/chart_ingest_shadow.py`
  Bybit kline shadow subscribe, bot HTTP event와 compare, broker publish shadow 연결
- `app/api/services/chart_broker_shadow.py`
  NATS JetStream/legacy Redis publish adapter, stream/subject naming, bounded queue policy
- `app/api/services/chart_nats_shadow.py`
  API gateway NATS shadow subscriber, durable consumer, ack/nak/term, pending/redelivery metric
- `app/api/services/chart_active_source.py`
  `CHART_EVENT_ACTIVE_SOURCE` 검증. 현재 active source는 `chart_ingest_active`만 유효하다.
- `app/api/services/chart_nats_active_gateway.py`
  NATS JetStream partial/critical event를 `/ws/chart-candles` payload로 변환하는 active gateway
- `app/api/services/chart_nats_final_durable.py`
  final/reconcile event를 MySQL `candles`에 쓰는 chart-storage worker와 dedupe guard
- `app/auth/otp/`
  Google TOTP 검증, 실패 횟수 제한, HttpOnly session cookie
- `app/db/`
  SQLAlchemy model/schema/session/crud, projection DDL helper

## core/

- `core/ws/`
  Bybit WS template, price dispatcher, candle detector, position/execution watcher, strategy flag/zone push listener
- `core/ws/chart_events.py`
  browser-facing live candle event contract, NATS payload normalization, volume 보존
- `core/persistence/`
  candles, positions, sessions, execution store, zone state/projection repo
- `core/trading/`
  주문 실행 공통 유틸, TP/SL, funding, execution store ops
- `core/state/`
  shared state와 `/tmp/shared_state.json` snapshot writer
- `core/operations/`
  bot heartbeat, event loop lag watchdog
- `core/notifications/`
  Telegram 및 position overlay 내부 알림
- `core/config/`
  config watcher, DB 기반 strategy flag cache
- `core/tools/`
  candle backfill, 1분봉 plan/dry-run, simulated price feeder 같은 단독 실행 도구
- `core/research/`
  공개용 연구 검증 helper. 현재는 `public_validation_summary.py`가 candidate id/raw row/target value를 노출하지 않는 aggregate-only summary와 leakage guard를 보여준다. 실제 전략 조건이나 score threshold는 포함하지 않는다.

## strategies/

- `strategies/base/`
  공개/비공개 전략을 같은 runtime에 꽂기 위한 최소 interface와 loader. 공개 레포에서는 이 interface로 "전략은 교체 가능한 런타임 단위"라는 점만 보여준다.
- `strategies/demo_zone/`
  공개용 구조 시연 전략. mock/sample candle 흐름과 zone event 연결을 보여주며 실전 전략 규칙은 포함하지 않는다. 실제 연구 방식은 [전략 연구 방식](STRATEGY_RESEARCH_WORKFLOW.md)에 따로 정리한다.

## research helper

`backend/core/research/public_validation_summary.py`는 최근 Structure Zone 연구 흐름을 공개용 코드로 축약한 예제다.

이 helper가 보여주는 책임:

- feature cutoff보다 앞선 label observation을 제외해 leakage를 막는다.
- 중복 후보 row를 한 번만 센다.
- 작은 sample group은 `caveat_only`로 남겨 과해석을 막는다.
- candidate id, raw row, target value, 전략 조건을 output에 포함하지 않는다.

테스트는 `backend/tests/test_public_validation_summary.py`에 있다. 이 코드는 실전 전략 엔진이 아니라, 비공개 연구 코드가 공개 문맥에서 어떤 경계를 지켜야 하는지 보여주는 작은 기준점이다.

## 주요 이벤트 경로

### Candle

1. `core/ws/candle_detector.py`가 Bybit kline WS를 수신한다.
2. 진행 중/확정 candle을 DB와 shared state에 반영하고, REST verify로 보정한다.
3. chart ingest runtime이 NATS JetStream에 partial/final/reconcile event를 publish한다.
4. API active gateway가 NATS event를 읽어 `/ws/chart-candles`로 브라우저에 fanout한다.
5. `chart-storage` worker가 critical lane의 final/reconcile event를 MySQL `candles`에 durable write한다.
6. shadow compare 경로는 전환 검증과 관측용으로 남긴다.

### NATS Active / Durable

1. `CHART_EVENT_ACTIVE_SOURCE=chart_ingest_active`가 active gateway 기준값이다.
2. API active gateway는 partial/critical consumer를 나눠 NATS event를 읽는다.
3. 정상 event는 `/ws/chart-candles` payload로 변환하고 `ack`한다.
4. schema mismatch는 drop log 후 `term`, 처리 중 예외는 `nak` with delay를 사용한다.
5. `chart-storage` worker는 critical lane만 읽고 dedupe 후 MySQL에 저장한다.
6. `CHART_STORAGE_FILTER_SUBJECTS`를 쓰면 여러 정확한 critical subject만 선택할 수 있다.

### Structure Zone

1. bot runtime이 확정봉 기준 Structure Zone delta를 계산한다.
2. `/internal/zones/delta` 또는 `/internal/zones/state-sync`로 API 서버에 전달한다.
3. API 서버가 `/ws/zones`로 브라우저에 push한다.
4. `CandleChart`, `StructureZoneDeck`, `ZoneNotificationList`가 overlay와 알림 상태를 갱신한다.

### Position Overlay

1. position/execution watcher가 거래소 상태를 수신한다.
2. 내부 overlay event를 API 서버에 전달한다.
3. `/ws/position-overlay`가 브라우저에 snapshot/update/clear를 전달한다.

## Startup Snapshot / Projection

차트 최초 진입 시 multi-timeframe overlay attach는 canonical row를 매번 풀어 계산하지 않는다.

- canonical write model: `zone_state`
- read-optimized model: `zone_projection`
- startup endpoint: `/api/zones/startup-snapshot`

projection은 startup 화면에 필요한 형태로 미리 정리된 read model이다. first paint 이후 grouped response 한 번으로 여러 timeframe overlay를 붙이는 것이 목적이다.

## 운영 관측 포인트

- API liveness: `/healthz`
- Bot liveness: `/tmp/bot_heartbeat` 최근 갱신 여부
- Event loop lag: `start_event_loop_lag_watchdog("api"|"bot")`
- Candle/Zone fanout: internal endpoint 응답과 WS client count 로그
- NATS shadow: pending, ack pending, redelivery, schema drop, last error, compare mismatch
- NATS active/durable: fanout/drop/write count, ack/nak/term, pending, redelivery
- REST verify: 확정봉 보정 여부와 mismatch 로그

## 공개 레포에서 제외되는 것

- 실제 운영 `.env`
- `backend/config/config.json`
- `backend/storage/**`
- `backend/logs/**`
- 거래소/Telegram 키
- 운영 DB dump
- 실전 전략 로직과 파라미터
- raw 연구 row, target metric 원본, score threshold

## 관련 문서

- [README](../README.md)
- [아키텍처](ARCHITECTURE.md)
- [UI v3 정리](UI_V3_PREVIEW.md)
- [Chart event contract](CHART_EVENT_CONTRACT.md)
- [Chart ingest broker design](CHART_INGEST_BROKER_DESIGN.md)
- [전략 연구 방식](STRATEGY_RESEARCH_WORKFLOW.md)
- [Structure Zone 연구 공개 요약](research/structure-zone-research-public-20260623.md)
- [배포 구조](DEPLOYMENT.md)
- [Zabbix 기반 원격 모니터링](Zabbix_Agent2.md)
