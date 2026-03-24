# Chart Event Contract

기준: public snapshot + 2026-06-06 active chart pipeline update

## 목적

이 문서는 실시간 차트 candle event가 어떤 계약으로 이동하는지 정리한다. 공개 레포에서는 실제 운영 로그나 전략 조건을 공개하지 않고, browser-facing WebSocket 계약, NATS JetStream event 계약, durable write 경계를 이해할 수 있게 남긴다.

## 현재 흐름

현재 공개 기준 차트 이벤트의 핵심 흐름은 다음과 같다.

```text
Bybit public kline WS
  -> chart ingest runtime
  -> NATS JetStream
      partial lane: 진행 중 candle update
      critical lane: final/reconcile candle
  -> API active gateway
  -> /ws/chart-candles
  -> browser

NATS critical lane
  -> chart-storage worker
  -> MySQL candles
```

과거에는 bot이 API 내부 HTTP endpoint로 candle update를 직접 넘기는 경로가 있었다. 공개 스냅샷의 현재 기준에서는 `/internal/candle-live-update`, `/internal/candle-live-reconcile`, 내부 HTTP publisher helper를 active 경로에서 제거했다. REST 확정봉 보정용 `/internal/candle-rest-confirmed`는 Structure Zone 동기화와 브라우저 보정 이벤트를 위해 별도로 유지한다.

## Event Types

| event type | 의미 | 처리 lane |
| --- | --- | --- |
| `partial` | 진행 중 candle update | NATS partial lane, API active fanout |
| `final` | 확정 candle | NATS critical lane, API active fanout, durable write |
| `reconcile` | REST 검증 이후 보정 | NATS critical lane, API reconcile fanout, durable write |

## Core Fields

코드 표현은 `backend/core/ws/chart_events.py`의 `LiveCandleEvent`다.

| 필드 | 의미 |
| --- | --- |
| `event_type` | `partial`, `final`, `reconcile` 중 하나 |
| `exchange` | 거래소 이름. 예: `bybit` |
| `symbol` | 심볼. 예: `BTCUSDT` |
| `tf` | 분 단위 timeframe. 예: `15`, `30`, `60`, `240`, `1440` |
| `candle.start` | bar 시작 시각, epoch ms |
| `candle.end` | bar 종료 시각, epoch ms |
| `candle.open/high/low/close` | OHLC 가격 |
| `candle.volume` | 거래량. 없을 수 있지만 있으면 보존 |
| `candle.confirm` | final 여부와 맞춘 boolean |
| `is_final` | `partial=false`, `final/reconcile=true` |
| `source` | event producer label. active 경로 기본값은 `chart_ingest_active` |
| `source_seq` | producer process 안에서 TF별 순서를 볼 때 쓰는 sequence |
| `reason` | reconcile 전용 보정 이유 |
| `emitted_at_ms`, `exchange_ts` | 관측용 timestamp |

## Routing And Idempotency

기본 candle key:

```text
exchange:symbol:tf:bar_time
```

기본 idempotency key:

```text
exchange:symbol:tf:bar_time:event_type
```

`chart-storage` worker는 critical lane의 final/reconcile event를 MySQL에 쓰기 전에 `chart_event_dedupe` 테이블로 event idempotency를 확인한다. 중복 event는 재처리하지 않고 ack 가능한 상태로 분리한다.

## NATS Subject

NATS broker payload는 `schema_version=chart_candle.v1`을 가진다.

| lane | subject 형식 | stream |
| --- | --- | --- |
| partial | `candles.partial.<exchange>.<symbol>.<interval>` | `CHART_PARTIAL` |
| final/reconcile | `candles.critical.<exchange>.<symbol>.<interval>` | `CHART_CRITICAL` |

API active gateway는 partial/critical consumer를 각각 두고 browser-facing `/ws/chart-candles` 계약으로 변환한다. `chart-storage` worker는 critical lane만 구독해 MySQL에 durable write를 수행한다.

## Browser WebSocket Payload

브라우저가 받는 WebSocket event는 source가 바뀌어도 같은 형태를 유지한다.

```json
{
  "type": "candle_update",
  "symbol": "BTCUSDT",
  "tf": "15",
  "candle": {
    "start": 1779732000000,
    "end": 1779732899999,
    "open": 100.1,
    "high": 101.2,
    "low": 99.9,
    "close": 100.5,
    "volume": 12.3,
    "confirm": false
  },
  "isFinal": false,
  "source": "chart_ingest_active",
  "sourceSeq": 7,
  "seq": 1234,
  "serverTs": 1779732000123
}
```

`reconcile`은 `type=candle_reconcile`로 분리하고 `reason`을 함께 보낸다.

## Gateway Metrics

`/ws/chart-candles` gateway는 payload contract를 바꾸지 않고 process-local metric을 제공한다.

- endpoint: `GET /internal/chart-websocket-metrics`
- access: local-only
- payload: numeric aggregate와 sanitized text only
- 제외: client IP, raw WebSocket payload, credentials, private host/domain

주요 metric:

- `active_connections`, `subscribed_connections`, `subscriptions_by_tf`
- `broadcast_duration_p95_ms`, `broadcast_duration_p99_ms`
- `send_duration_p99_ms`, `slow_send_250ms_total`, `slow_send_1000ms_total`
- `active_send_failure_total`, `closing_send_failure_total`
- `broadcast_send_failure_total`, `teardown_close_race_total`, `send_skipped_closing_total`

## 관련 문서

- [아키텍처](ARCHITECTURE.md)
- [Chart ingest broker design](CHART_INGEST_BROKER_DESIGN.md)
- [백엔드 구조](BACKEND_STRUCTURE.md)
- [배포 구조](DEPLOYMENT.md)
