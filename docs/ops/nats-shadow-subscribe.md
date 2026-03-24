# NATS JetStream Shadow Subscribe 검증 요약

기준: historical shadow validation note + 2026-06-06 public clarification

> 최신 runtime 기준에서는 `CHART_EVENT_ACTIVE_SOURCE=chart_ingest_active`가 active fanout 경로다. 이 문서는 NATS 전환 과정에서 사용한 shadow compare/log/metric 경로를 설명하는 기록이며, 현재 browser-facing 주 경로를 뜻하지 않는다.

## 범위

- API websocket gateway의 NATS JetStream shadow subscribe
- active gateway 전환 전 compare/log/metric 검증
- browser fanout과 MySQL durable write를 하지 않는 관찰 전용 경로
- Redis durable path 사용 없음

## 요약

이 기록의 당시 기준에서는 NATS JetStream event를 shadow compare/log/metric 경로로만 구독했다. 최신 공개 스냅샷에서는 별도의 active gateway와 `chart-storage` worker가 추가되었고, 이 문서는 shadow 관찰 경로의 의미를 설명하는 보조 문서로 남긴다.

운영 compose 예시는 NATS JetStream을 기본 runtime service로 두고, Redis는 `redis-rollback` profile로 격리한다.

## Compose 요약

Default runtime:

- `api`
- `bot`
- `nats`

Profile runtime:

- `mysql`: `db`
- `redis`: `redis-rollback`

Redis는 기본 runtime에서 제외한다. Redis persistence는 켜지 않는다.

## NATS Stream/Consumer 요약

Stream:

- `CHART_PARTIAL`
  - subject: `candles.partial.*.*.*`
  - storage: file
  - retention: limits
  - shorter max age
- `CHART_CRITICAL`
  - subject: `candles.critical.*.*.*`
  - storage: file
  - retention: limits
  - longer max age

Consumer:

- `chart-api-gateway-shadow-partial`
- `chart-api-gateway-shadow-critical`
- ack policy: explicit
- max deliver: env configurable
- max ack pending: env configurable

## Shadow Subscribe 동작

API startup 시 `NatsJetStreamGatewayShadowSubscriber`가 durable pull consumer를 준비한다. 구독 event는 `ObservedCandleEvent`로 변환 후 bot HTTP event와 별도 compare store에서 비교한다.

처리 정책:

- 정상 schema: compare state 반영 후 `ack`
- JSON decode/schema mismatch: drop log 후 `term`
- 처리 중 예외: `nak` with delay
- NATS 연결 장애: shadow 관찰값으로 분리해 기록

사용하지 않는 경로:

- browser fanout 없음
- MySQL durable write 없음
- active source switch 없음. 최신 active fanout은 [Chart event contract](../CHART_EVENT_CONTRACT.md)를 기준으로 한다.

## 검증 체크리스트

- `docker compose config --quiet`
- `docker compose ps`
- `curl -fsS http://127.0.0.1:8000/healthz`
- `/ws/chart-candles` subscribe ack
- NATS stream info
- NATS consumer info
- API restart 후 shadow subscriber reconnect
- NATS restart 후 live path 영향 없음
- `schema_drop_total=0`
- `redelivery_total=0` 또는 redelivery 발생 시 원인 로그 확인
- compare mismatch/final mismatch/duplicate final 관찰

## 관련 문서

- [Chart event contract](../CHART_EVENT_CONTRACT.md)
- [Chart ingest broker design](../CHART_INGEST_BROKER_DESIGN.md)
- [배포 구조](../DEPLOYMENT.md)
