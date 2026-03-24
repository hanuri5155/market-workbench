# WebSocket load-test / metrics public summary

기준: 2026-05-29 공개 가능 범위

> 최신 공개 기준은 [2026-05-31 WebSocket load-test / metrics public summary](websocket-loadtest-capacity-public-20260531.md)다. 이 문서는 350/500/750 WS 단계까지의 중간 기록으로 남긴다.

이 문서는 private runtime에서 수행한 `/ws/chart-candles` 부하테스트와 metric 분리 결과를 공개 포트폴리오 범위로 재구성한 요약이다. raw evidence, 실제 host/domain, 내부 PR 번호, 운영 로그, token, chat id, IP는 포함하지 않는다.

## 목적

- WebSocket active-run 장애와 load generator 종료/close path를 분리해서 판단
- API gateway fanout/backpressure를 수치로 관찰
- 단일 node 기준 공개 가능한 capacity lower bound를 보수적으로 정리
- 30,000 concurrent users 같은 scale-out 목표와 현재 단일 node 검증값의 gap을 명확히 분리

## 공개 포함 코드와 도구

| 범위 | 파일 |
| --- | --- |
| gateway metric instrumentation | `backend/app/api/ws/chart_candles.py` |
| local-only metric endpoint | `backend/app/api_app.py` |
| unit tests | `backend/tests/test_api_websocket_gateway_metrics.py` |
| Locust scenario | `scripts/loadtest/chart_ws_locust.py` |
| Zabbix metric probe | `scripts/monitoring/market-workbench-api-ws-metrics-probe` |
| Zabbix reader | `scripts/monitoring/market-workbench-zabbix-read` |
| systemd examples | `deploy/systemd/market-workbench-api-ws-metrics-probe.*` |
| UserParameter template | `deploy/zabbix/market-workbench-api-ws-userparameters.conf` |

## Metric split

기존 aggregate counter만으로는 active-run 장애와 teardown close race를 구분하기 어려웠다. 공개 snapshot은 다음 metric을 추가해 판정 기준을 나눴다.

| metric | 의미 |
| --- | --- |
| `active_send_failure_total` | active/subscribed 상태에서 발생한 send failure |
| `closing_send_failure_total` | closing/disconnected/cleanup 또는 close-like exception으로 분류된 send failure |
| `client_disconnect_total` | receive path에서 client disconnect로 관찰된 종료 |
| `server_close_total` | server가 close/cleanup을 시도한 횟수 |
| `broadcast_send_failure_total` | broadcast loop 안에서 발생한 send failure 총합 |
| `teardown_close_race_total` | close-like failure 또는 teardown race window 안의 send failure |
| `send_skipped_closing_total` | 이미 closing/closed로 표시된 client에 send를 생략한 횟수 |

해석 기준:

- `active_send_failure_total` delta가 있으면 active-run warning/fail 후보
- `closing_send_failure_total`/`teardown_close_race_total`만 증가하면 teardown close-path 관찰값
- `send_failure_total`/`error_total`은 호환성을 위해 유지하지만 단독 fail 기준으로 쓰지 않음

## Load-test model

```text
endpoint: /ws/chart-candles
symbol: BTCUSDT
timeframes: 15,30,60,240
SUBS_PER_USER: 5
load generator: Locust + websocket-client
```

대표 실행 형태:

```bash
TARGET_BASE_URL="https://<public-app-host>" \
CHART_SYMBOL="BTCUSDT" \
CHART_TFS="15,30,60,240" \
EXPECTED_CHART_SOURCE="chart_ingest_active" \
CHECK_SOURCE_SEQ_GAPS="false" \
SUBS_PER_USER=5 \
locust -f scripts/loadtest/chart_ws_locust.py \
  --headless \
  -u "<USERS>" \
  -r 10 \
  --run-time "<DURATION>" \
  --host "https://<public-app-host>" \
  --csv "<EVIDENCE_PREFIX>" \
  --html "<EVIDENCE_PREFIX>.html" \
  --only-summary
```

## Results

| Stage | Total WS | Result | Public interpretation |
| --- | ---: | --- | --- |
| A/B teardown verification | 350 | PASS | aggregate teardown delta was classified as close-path, `active_send_failure_total=0` |
| A/B teardown verification | 500 | PASS | client failure 0, ack/latest failure 0, active send failure delta 0 |
| capacity discovery sanity | 500 | PASS | active/subscribed target reached, send/error aggregate delta 0 |
| capacity discovery | 750 | PARTIAL / gate stop | active/subscribed reached 751, but connect/ramp failure occurred |

Important observed values:

- 350 WS: client connect/ack/latest failure 0, active-run send failure 0
- 500 WS: client connect/ack/latest failure 0, active-run send failure 0
- 750 WS: 750 successful subscribed connections after retries, but 54 connect failures over 804 attempts
- 500/750 active gateway fanout stayed low latency during active windows
- NATS/chart-storage pending, ack pending, redelivery stayed at 0 in the observed windows
- Zabbix active problem and recent alert count stayed at 0 in the observed windows

## Capacity decision

```text
C_pass_discovery = 500 total WebSockets
C_safe_public = 350 total WebSockets
```

`C_safe_public=350` is kept because 750 WS was not a clean pass. The 750 result is useful as an active-gateway lower-bound observation, but not promoted to a safe capacity value.

User conversion:

| Model | Public safe users |
| --- | ---: |
| 1 chart/user | 350 |
| 3 charts/user | 116 |
| 5 charts/user | 70 |

## 30,000 concurrent users gap

The current single-node public-safe value is 350 total WebSockets.

| Target | Required WS | Gap vs 350 safe WS |
| --- | ---: | ---: |
| 30,000 users at 1 chart/user | 30,000 | 29,650 |
| 30,000 users at 3 charts/user | 90,000 | 89,650 |
| 30,000 users at 5 charts/user | 150,000 | 149,650 |

This means 30,000 concurrent users should be treated as a scale-out architecture target, not a single-node tuning target.

## Next work

- Investigate 750 WS connect/ramp failure: container `nofile`, nginx/TLS admission, backlog, load generator CPU sampling
- Re-run 750 WS after the connection-admission path is understood
- Reopen 1000/1500/2000 WS discovery only after 750 is clean
- Design Zabbix trigger thresholds from `active_send_failure_total`, broadcast latency, and synthetic probe status
- Draft shard/scale-out architecture for multi-node WebSocket gateway
