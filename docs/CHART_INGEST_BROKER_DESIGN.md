# Chart Ingest Broker Design

기준: design history + 2026-06-06 active pipeline update

> 이 문서는 chart ingest broker 전환 과정을 설명하는 설계 이력 문서다. 최신 event 계약과 현재 active/durable 경계는 [Chart Event Contract](CHART_EVENT_CONTRACT.md)를 기준으로 한다.

## 목적

이 문서는 live chart pipeline을 `chart ingest -> broker -> api websocket gateway` 구조로 옮긴 과정을 공개 스냅샷 기준으로 정리한다. 초기에는 shadow mode로 비교/관찰했고, 최신 공개 스냅샷에서는 `chart_ingest_active` fanout과 `chart-storage` durable write 경계를 포함한다.

## 현재 구조

```text
Bybit public kline WS
  -> chart ingest runtime
  -> NATS JetStream
  -> api active gateway
  -> app.api.ws.chart_candles
  -> /ws/chart-candles
  -> browser

NATS critical lane
  -> chart-storage worker
  -> MySQL candles
```

현재 source concept:

- browser-facing active source: `chart_ingest_active`
- NATS JetStream: active fanout + shadow diagnostics
- Redis: `redis-rollback` profile only

## 목표 구조와 현재 반영

```text
Bybit public kline WS
  -> chart-ingest service
  -> NATS JetStream
  -> api websocket gateway
  -> /ws/chart-candles
  -> browser
```

목표 구조에서도 browser-facing payload는 유지한다. active source 전환 후에도 UI가 보는 `/ws/chart-candles` payload는 같은 형태를 유지한다.

## Service Responsibility Split

### chart-ingest

- Bybit public kline WS 구독
- symbol/timeframe subscription 관리
- raw exchange message를 `LiveCandleEvent` compatible schema로 normalize
- partial/final 판별
- ordering/drop metric 수집
- broker publish

chart ingest가 하지 않는 것:

- browser fanout
- MySQL write
- 실전 전략 판단

### broker

- live candle event 전달
- final/reconcile durability 보존
- replay/recovery 관찰
- pending/redelivery metric 제공
- retention/backpressure boundary 제공

현재 target은 NATS JetStream이다.

### api websocket gateway

- broker event를 active subscriber로 consume
- active source guard 적용
- browser-facing `/ws/chart-candles` payload로 fanout
- compare/log/metric은 shadow 경로로 별도 유지

### storage/reconcile

- `chart-storage` worker가 critical lane의 final/reconcile event를 MySQL에 저장한다.
- `chart_event_dedupe`로 event idempotency를 보장한다.
- consumer filter 변경은 자동 삭제/재생성하지 않고 별도 운영 절차로 분리한다.

## Broker Selection

| 기준 | Redis Streams | NATS JetStream |
| --- | --- | --- |
| durability | 가능 | 가능 |
| replay | 가능 | 가능 |
| consumer recovery | pending/xclaim | durable consumer/ack/redelivery |
| 현재 운영 방향 | rollback only | durable broker target |
| persistence policy | default off | JetStream file store |

결론:

- Redis는 public compose에 rollback profile로만 남긴다.
- NATS JetStream을 durable broker target으로 문서화한다.
- active fanout과 shadow compare/log/metric 경로를 분리한다.

## NATS Layout

Subjects:

```text
candles.partial.<exchange>.<symbol>.<interval>
candles.critical.<exchange>.<symbol>.<interval>
```

Streams:

- `CHART_PARTIAL`: partial lane, short retention
- `CHART_CRITICAL`: final/reconcile lane, longer retention

Consumers:

- `chart-api-gateway-shadow-partial`
- `chart-api-gateway-shadow-critical`
- `chart-api-gateway-active-partial`
- `chart-api-gateway-active-critical`
- `chart-storage-final-reconcile`

Ack policy:

- explicit ack
- schema mismatch: `term`
- temporary processing error: `nak`
- normal event: `ack`

## Runtime Env

Core flags:

- `CHART_EVENT_ACTIVE_SOURCE=chart_ingest_active`
- `CHART_INGEST_SHADOW_ENABLED=false`
- `CHART_BROKER_PUBLISH_SHADOW_ENABLED=false`
- `CHART_BROKER_KIND=nats_jetstream`
- `NATS_SHADOW_SUBSCRIBE_ENABLED=false`
- `NATS_FINAL_DURABLE_API_EMBEDDED_ENABLED=false`
- `CHART_STORAGE_ENABLED=false` in API env, true only in chart-storage service

Shadow mode와 durable storage worker는 명시적으로 켤 때만 동작한다.

## Observability

관찰 대상:

- connect/reconnect count
- messages seen
- ack/nak/term count
- schema drop
- pending
- ack pending
- redelivery
- last error
- compare mismatch
- final mismatch
- duplicate final

## Public Repo Boundary

공개 repo는 broker migration 설계와 shadow diagnostics를 보여준다. 실전 strategy rule, live server path, raw 운영 로그, 실제 token은 포함하지 않는다.

## 후속 단계

- replay/idempotency active 설계
- storage worker 분리 여부 검토
- active source switch gate 정의
- browser fanout 전환 전 부하/장애 테스트
