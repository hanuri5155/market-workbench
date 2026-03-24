# Zabbix 기반 원격 모니터링

> 실제 운영 검증 흐름 기준 공개 문서
> 실제 host, IP, token, chat id, 원본 로그는 제외하고 공개 가능한 probe/template 예시만 포함

## 문서 역할

- 운영 가시성 문서
- 내부 healthcheck/watchdog와 외부 Zabbix 관측을 연결하는 문서
- 장애 생성/해제와 Telegram 알림 검증 흐름 정리

## 이 문서에서 보는 것

- Zabbix 서버 위치와 감시 대상 범위
- host/container/`/healthz`/systemd/disk 감시 구조
- `/ws/chart-candles` synthetic probe와 API WS gateway metric 관찰 구조
- Current Problems와 Telegram 알림 기준 실제 검증 흐름
- 공개/비공개 경계와 비식별 캡처 기준

## 목적

- 애플리케이션 내부 로그와 heartbeat만으로 끝나지 않고, 운영자 외부 시야에서 OCI 대상을 다시 관측
- host, container, `/healthz`, systemd, disk 상태를 별도 모니터링 서버에서 확인
- Problem/Resolved와 Telegram 알림까지 연결해 장애 인지 흐름을 검증

## 구성 개요

- 모니터링 서버: Windows 노트북 + WSL2 + Docker
- Zabbix 구성요소: `zabbix-server`, `zabbix-web`, `mysql`
- 모니터링 대상: OCI에서 운영 중인 API/bot 중심 런타임
- 알림 채널: Telegram
- 대상 측 감시 경로: `zabbix-agent2`와 container/systemd/HTTP 체크를 조합
- 핵심: 운영 대상과 분리된 위치에서 상태를 다시 보는 구성
- `heartbeat`, `/healthz`, `event loop watchdog`는 내부 시그널
- Zabbix는 운영자 외부 관측 시그널

## 감시 항목

- host availability
- Docker container health
- `/healthz` 응답 여부
- systemd/service 상태
- disk 사용량
- CPU / memory 추이
- API WebSocket gateway fanout/backpressure metric
- active-run send failure와 close-path/teardown send failure 분리
- Current Problems / Latest data / Telegram notifications

item key 예시와 UserParameter template은 공개한다. 실제 host, token, chat id, trigger expression, action policy 세부값은 비공개 범위다.

## 공개 포함 파일

| 용도 | 파일 |
| --- | --- |
| API WS metric probe | `scripts/monitoring/market-workbench-api-ws-metrics-probe` |
| metric cache reader | `scripts/monitoring/market-workbench-zabbix-read` |
| systemd service | `deploy/systemd/market-workbench-api-ws-metrics-probe.service` |
| systemd timer | `deploy/systemd/market-workbench-api-ws-metrics-probe.timer` |
| UserParameter template | `deploy/zabbix/market-workbench-api-ws-userparameters.conf` |

probe 기본값은 public repo 기준으로 일반화했다.

```text
MARKET_WORKBENCH_COMPOSE_DIR=/opt/market-workbench/compose
COMPOSE_PROJECT_NAME=market-workbench
MARKET_WORKBENCH_METRICS_DIR=/var/lib/zabbix/market_workbench_metrics
```

## 실제 검증 흐름

1. Zabbix Global view에서 대상 host와 주요 지표가 정상 수집되는지 확인
2. API 컨테이너를 의도적으로 중단해 `Current Problems`에 장애 이벤트가 생성되는지 확인
3. 컨테이너를 다시 기동해 같은 이벤트가 해제되는지 확인
4. Problem/Resolved가 Telegram 알림까지 이어지는지 확인
5. WebSocket load-test 중 `market_workbench.api_ws.*`와 synthetic probe가 active-run 장애를 보이는지 확인

## API WebSocket metric keys

대표 UserParameter key:

```text
market_workbench.api_ws.status
market_workbench.api_ws.active_connections
market_workbench.api_ws.subscribed_connections
market_workbench.api_ws.subscriptions_total
market_workbench.api_ws.subscriptions[15]
market_workbench.api_ws.subscriptions[30]
market_workbench.api_ws.subscriptions[60]
market_workbench.api_ws.subscriptions[240]
market_workbench.api_ws.broadcast_duration_p95_ms
market_workbench.api_ws.broadcast_duration_p99_ms
market_workbench.api_ws.send_duration_p99_ms
market_workbench.api_ws.slow_send_250ms_total
market_workbench.api_ws.slow_send_1000ms_total
market_workbench.api_ws.send_failure_total
market_workbench.api_ws.active_send_failure_total
market_workbench.api_ws.closing_send_failure_total
market_workbench.api_ws.broadcast_send_failure_total
market_workbench.api_ws.teardown_close_race_total
market_workbench.api_ws.send_skipped_closing_total
market_workbench.api_ws.error_total
```

해석 기준:

- `active_send_failure_total` delta가 있으면 active-run warning/fail 후보로 본다.
- `closing_send_failure_total`/`teardown_close_race_total`만 증가하면 load 종료나 client close path 관찰값으로 분리한다.
- aggregate `send_failure_total`/`error_total`은 호환성 때문에 유지하지만 단독 fail 기준으로 쓰지 않는다.

## 운영 검증 캡처

### 1. Zabbix 운영 화면

- 별도 Zabbix 서버에서 OCI 대상의 availability, CPU, memory, latest data와 문제 상태를 확인하는 화면

![Zabbix global view](screenshots/monitoring-zabbix-global-view.png)

### 2. Telegram 알림

- 운영자가 대시보드를 열고 있지 않아도 Problem/Resolved 상태 변화를 받을 수 있도록 연결한 화면

<img src="screenshots/monitoring-zabbix-telegram-alert.png" alt="Zabbix Telegram alert" width="32%" />

## 운영 관점에서 의미가 있었던 점

- 내부 `/healthz`, heartbeat, watchdog와 외부 Zabbix를 함께 둔 관측 시야 분리
- `Problem 생성 -> Problem 해제 -> Telegram 알림`까지 한 흐름으로 검증한 실제 운영형 모니터링 증빙
- 별도 WSL 기반 모니터링 서버를 통해 대상 서버 내부 프로세스와 독립된 관점에서 상태를 다시 보는 구조

## 공개/비공개 경계

- 공개: 구조 설명, 감시 항목 범주, 검증 시나리오, 비식별 캡처, public-safe probe/template
- 비공개: 실제 host/domain, token, chat id, trigger/action 세부값, 원본 운영 로그, raw evidence bundle

## 관련 문서

- [README](../README.md)
- [아키텍처](ARCHITECTURE.md)
- [배포 구조](DEPLOYMENT.md)
- [백엔드 구조](BACKEND_STRUCTURE.md)
- [WebSocket load-test / metrics 요약](ops/websocket-loadtest-capacity-public-20260529.md)
