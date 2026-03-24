# WebSocket load-test / metrics public summary

기준: 2026-05-31 공개 가능 범위

이 문서는 private runtime에서 수행한 `/ws/chart-candles` 부하테스트와 metric 분리 결과를 공개 포트폴리오 범위로 재구성한 요약이다. raw evidence, 실제 host/domain, 내부 PR 번호, 운영 로그, token, chat id, IP는 포함하지 않는다.

## 목적

- WebSocket active-run 장애와 load generator 종료/close path를 분리해서 판단
- API gateway fanout/backpressure를 수치로 관찰
- 단일 node 기준 공개 가능한 capacity lower bound를 보수적으로 정리
- scale-out 목표와 현재 단일 node 검증값의 gap을 명확히 분리

## 공개 포함 코드와 도구

| 범위 | 파일 |
| --- | --- |
| gateway metric instrumentation | `backend/app/api/ws/chart_candles.py` |
| local-only metric endpoint | `backend/app/api_app.py` |
| unit tests | `backend/tests/test_api_websocket_gateway_metrics.py` |
| Locust scenario | `scripts/loadtest/chart_ws_locust.py` |
| load generator resource sampler | `scripts/loadtest/loadgen_resource_sampler.py` |
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
load generator: Locust + websocket-client
active source: chart_ingest_active
```

대표 실행 형태:

```bash
TARGET_BASE_URL="https://<public-app-host>" \
CHART_SYMBOL="BTCUSDT" \
CHART_TFS="15,30,60,240" \
EXPECTED_CHART_SOURCE="chart_ingest_active" \
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

| Stage | Result | Public interpretation |
| --- | --- | --- |
| 750 WS ramp after admission tuning | PASS | 기존 connect/ramp failure 재현 없음 |
| 1000 WS discovery | PASS | active send failure와 client failure 없이 target 도달 |
| 1000 WS 60m soak | PASS | 긴 실행에서도 active-run clean 유지 |
| 1500 WS close-path A/B | PASS | close-like send failure가 teardown path로 분리됨 |
| 1500 WS 60m revalidation | PASS | 보정 후 장시간 검증에서도 active-run clean |
| 2000 WS discovery | PARTIAL | active-run은 clean이었지만 종료 후 slow-send warning이 있어 보류 |
| 2000 WS A/B gate | STOP | backend/API는 clean이었지만 client connect timeout 관찰로 gate stop |
| 2000 WS single recheck | PASS-clean | slow-send warning과 connect timeout 재현 없음 |
| 2000 WS 60m soak | PASS-clean | 이전 PARTIAL/gate-stop 증상이 재현되지 않음 |

## Capacity decision

```text
C_pass_soak = 2000 total WebSockets
C_safe_public = 1400 total WebSockets
next_untested_level = 2500 total WebSockets
```

`C_safe_public=1400`은 2000 WS soak 통과값을 그대로 운영 기준으로 쓰지 않고, 보수 여유를 둔 공개 요약값이다. 2500 WS는 아직 검증하지 않은 다음 단계로 남긴다.

User conversion:

| Model | Public safe users |
| --- | ---: |
| 1 chart/user | 1400 |
| 3 charts/user | 466 |
| 5 charts/user | 280 |

## 운영적으로 확인한 점

- active-run failure와 teardown close-path를 분리해야 부하테스트 판정이 흔들리지 않는다.
- load generator 자체 자원 사용을 함께 봐야 connect timeout을 backend 장애로 오판하지 않는다.
- API `nofile`, reverse proxy admission, NATS pending/ack pending, Zabbix alert state를 같은 표로 봐야 다음 단계 판단이 가능하다.
- 단일 node 검증값은 scale-out 목표와 구분해야 한다.

## 다음 작업

- 2500 WS 단계는 별도 discovery로 분리한다.
- scale-out 목표는 단일 node 튜닝이 아니라 gateway shard/분산 구조로 설계한다.
- Zabbix trigger는 aggregate `send_failure_total`이 아니라 active/closing split metric을 기준으로 조정한다.
