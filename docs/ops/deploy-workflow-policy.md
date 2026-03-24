# Deploy Workflow Policy

Last updated: 2026-06-06 KST

이 문서는 Market Workbench 공개 스냅샷에서 서비스별 배포 경계를 설명한다. 핵심은 image publish와 운영 rollout을 같은 작업으로 묶지 않는 것이다.

## 배포 경계

| 경로 | 목적 | 운영 반영 |
| --- | --- | --- |
| API-only image publish | API image만 GHCR에 빌드/푸시 | 하지 않음 |
| BOT-only image publish | 필요 시 BOT image만 빌드/푸시 | 별도 workflow가 필요함 |
| full release | API/BOT image를 같은 release tag로 빌드/푸시 | self-hosted runner에서 API/BOT 재기동 |
| chart-storage rollout | final/reconcile durable worker 반영 | `nats-durable` profile 별도 운영 작업 |

## API-only image publish

구현 파일:

```text
.github/workflows/api-image-publish.yml
```

이 workflow가 하는 일:

- `ghcr.io/<owner>/market-workbench-api:<image_tag>` 빌드/푸시
- `linux/amd64`, `linux/arm64` manifest 생성
- image label과 workflow summary에 source commit 기록

하지 않는 일:

- BOT image build/push
- 운영 host 접근
- Docker Compose 명령 실행
- `.env` 수정
- API service recreate
- DB query/write

## API-only 운영 반영

API-only image를 실제 운영에 반영할 때는 별도 승인된 운영 작업에서 다음 범위만 수행한다.

```bash
docker compose -p market-workbench --env-file .env -f compose.yaml pull api
docker compose -p market-workbench --env-file .env -f compose.yaml up -d --no-deps api
curl -fsS http://127.0.0.1:8000/healthz
```

BOT, NATS, MySQL, chart-storage, frontend는 이 경로의 범위가 아니다.

## Full release

구현 파일:

```text
.github/workflows/release.yml
```

tag push(`v*`) 기준으로 API/BOT image를 함께 빌드하고, self-hosted runner가 compose host에서 API health check 후 BOT을 재기동한다. infra service는 release job 안에서 재생성하지 않는다.

## Chart-storage

`chart-storage`는 NATS critical lane의 final/reconcile event를 MySQL `candles`에 쓰는 worker다. 공개 compose에서는 `nats-durable` profile로 분리한다.

```bash
docker compose -p market-workbench --env-file .env -f compose.yaml --profile nats-durable up -d chart-storage
```

consumer filter 변경, schema auto-create, 기존 durable consumer 삭제/재생성은 별도 runbook이 필요한 운영 작업으로 취급한다.
