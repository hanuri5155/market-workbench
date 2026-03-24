# 배포 구조

기준: public snapshot + 2026-06-06 API-only / chart-storage update

## 문서 역할

- 배포/릴리스 구조 문서
- GitHub Actions, GHCR, OCI, Compose 기준 실제 배포 흐름 정리
- 운영 확인 포인트와 시크릿 관리 위치 정리

## 원칙

- 운영 서버는 소스 빌드 없이 image pull 기준
- 릴리스 태그 `v*`는 배포 기준점
- 배포 단위는 API와 bot 이미지
- frontend 배포는 별도 정적 파일 배포 절차로 분리
- 단일 기준 문서: `.github/workflows/release.yml`
- Redis는 default runtime dependency가 아니라 rollback profile only
- NATS JetStream은 chart event active fanout과 final/reconcile durable write 경로
- API-only image publish와 운영 rollout은 분리

## GitHub Actions 워크플로우

### API-only image publish

- 파일: `.github/workflows/api-image-publish.yml`
- Trigger: 수동 `workflow_dispatch`
- 입력: `image_tag`, `push_latest`
- 역할:
  - API image만 `linux/amd64`, `linux/arm64`로 빌드/푸시
  - GHCR image digest와 source commit을 summary에 남김
- 하지 않는 일:
  - BOT image build/push
  - 운영 서버 접속
  - compose command
  - `.env` 수정
  - API service 재기동

API-only 운영 반영이 필요하면 별도 운영 작업에서 `API_IMAGE`만 바꾸고 `docker compose up -d --no-deps api`로 재기동한다.

### Full release

- Trigger: Git tag push `v*`
- `build-and-push`
  - GitHub-hosted runner에서 GHCR multi-arch 이미지 빌드/푸시
  - `linux/amd64`, `linux/arm64` manifest 생성
- `deploy`
  - self-hosted runner에서 compose host 내부 명령 실행
  - SSH ingress를 GitHub-hosted runner에 열지 않는 구성
- `create-release`
  - 배포 성공 시 draft GitHub Release 생성

## 이미지

| 구분 | Dockerfile | Registry/Image | Tag 규칙 |
| --- | --- | --- | --- |
| API | `backend/Dockerfile.api` | `ghcr.io/<owner>/market-workbench-api` | 릴리스 태그 |
| BOT | `backend/Dockerfile.bot` | `ghcr.io/<owner>/market-workbench-bot` | 릴리스 태그 |
| chart-storage | `backend/Dockerfile.api` 재사용 | `${CHART_STORAGE_IMAGE:-API_IMAGE}` | 별도 pin 또는 API image 재사용 |

## Self-Hosted Runner

공개 레포에서는 runner 이름과 실제 host 정보를 일반화한다.

| 항목 | 공개 예시 |
| --- | --- |
| runner labels | `self-hosted`, `market-workbench-deploy` |
| compose path | `/opt/market-workbench/compose` |
| compose project | `market-workbench` |
| env var override | `MARKET_WORKBENCH_COMPOSE_DIR` repository variable |
| compose env file override | `MARKET_WORKBENCH_ENV_FILE` environment variable |

설계 의도:

- GitHub-hosted runner의 유동 IP를 운영 서버 SSH ingress에 열지 않기 위한 구성
- 태그 push 기반 자동 배포 흐름 유지
- 운영 서버에는 source clone 없이 GHCR image pull만 수행

## 필요한 GitHub Secrets/Vars

Secrets:

- `GITHUB_TOKEN`: GHCR push 인증. GitHub 기본 제공
- `GHCR_PAT`: 운영 서버에서 private GHCR image pull용 token
- `GHCR_USER`: 운영 서버에서 GHCR login용 username

Vars:

- `MARKET_WORKBENCH_COMPOSE_DIR`: compose directory override. 미설정 시 `/opt/market-workbench/compose`

Legacy SSH deploy secrets는 self-hosted runner deploy job에서 사용하지 않는다.

## 배포 흐름

```text
git tag vX.Y.Z
git push origin vX.Y.Z
  -> GitHub Actions build-and-push
  -> GHCR multi-arch image push
  -> self-hosted runner deploy
  -> .env backup
  -> API_IMAGE/BOT_IMAGE tag update
  -> docker compose config
  -> docker compose pull api bot
  -> docker compose up -d --no-deps api
  -> /healthz
  -> docker compose up -d --no-deps bot
  -> docker compose ps
  -> draft GitHub Release
```

## Deploy Script 요약

`.github/workflows/release.yml`의 deploy job은 아래 흐름을 수행한다.

```bash
set -euo pipefail

cd "${MARKET_WORKBENCH_COMPOSE_DIR:-/opt/market-workbench/compose}"
TAG="<release-tag>"
IMAGE_OWNER="<github-repository-owner>"

echo "<GHCR_PAT>" | docker login ghcr.io -u "<GHCR_USER>" --password-stdin

ENV_BACKUP=".env.bak-$(date +%Y%m%d-%H%M%S)-release-${TAG}"
cp .env "$ENV_BACKUP"
chmod --reference=.env "$ENV_BACKUP" || chmod 600 "$ENV_BACKUP"

API_IMAGE="ghcr.io/${IMAGE_OWNER}/market-workbench-api:${TAG}"
BOT_IMAGE="ghcr.io/${IMAGE_OWNER}/market-workbench-bot:${TAG}"

grep -q '^API_IMAGE=' .env \
  && sed -i "s|^API_IMAGE=.*|API_IMAGE=${API_IMAGE}|g" .env \
  || echo "API_IMAGE=${API_IMAGE}" >> .env

grep -q '^BOT_IMAGE=' .env \
  && sed -i "s|^BOT_IMAGE=.*|BOT_IMAGE=${BOT_IMAGE}|g" .env \
  || echo "BOT_IMAGE=${BOT_IMAGE}" >> .env

docker compose -p market-workbench --env-file .env -f compose.yaml config --quiet
docker compose -p market-workbench --env-file .env -f compose.yaml pull api bot

docker compose -p market-workbench --env-file .env -f compose.yaml up -d --no-deps api
curl -fsS http://127.0.0.1:8000/healthz

docker compose -p market-workbench --env-file .env -f compose.yaml up -d --no-deps bot
docker compose -p market-workbench --env-file .env -f compose.yaml ps
```

## Compose Runtime

Default runtime:

- `api`
- `bot`
- `nats`

Profile runtime:

- `mysql`: `db`
- `chart-storage`: `nats-durable`
- `redis`: `redis-rollback`

Redis는 기본 `up -d` 경로에서 제외한다. rollback profile을 명시적으로 켤 때도 persistence는 켜지 않는다.
`chart-storage`는 final/reconcile candle durable write를 맡는 별도 worker다. API process 안 embedded durable worker는 기본 비활성화한다.

Local config validation can point the compose service `env_file` at the checked-in example file:

```bash
cd deploy
MARKET_WORKBENCH_ENV_FILE=.env.example \
docker-compose --env-file .env.example -f compose.yaml --profile db --profile redis-rollback config --quiet
```

`chart-storage`까지 포함한 config 확인:

```bash
cd deploy
MARKET_WORKBENCH_ENV_FILE=.env.example \
docker-compose --env-file .env.example -f compose.yaml --profile db --profile nats-durable --profile redis-rollback config --quiet
```

## 운영 확인 포인트

- API와 bot 이미지 태그 일치 여부
- `/healthz` 응답 여부
- bot heartbeat 파일 최신 갱신 여부
- local-only `/internal/chart-websocket-metrics` 수집 여부
- `market_workbench.api_ws.status`와 active/closing send failure delta
- `docker compose ps` 기준 상태
- NATS health와 JetStream data path 상태
- chart-storage worker가 필요한 환경에서만 떠 있는지, critical lane pending/ack pending이 쌓이지 않는지 확인
- Redis가 default profile로 올라오지 않는지 확인
- 로그 디렉터리 쓰기 상태
- MTF MA 소스: 프론트 latest REST polling 기준, 값이 현재 TF 가격보다 수초 늦게 보일 수 있는지 확인
- 차트 복귀 직후 MTF MA 값: 최신 latest REST 응답으로 한 번 보정되는지 확인

## Rollback

이전 릴리스 태그를 기준으로 이미지 태그를 되돌린다.

```bash
TAG="<previous-release-tag>"

sed -i "s|^API_IMAGE=.*|API_IMAGE=ghcr.io/<owner>/market-workbench-api:${TAG}|g" .env
sed -i "s|^BOT_IMAGE=.*|BOT_IMAGE=ghcr.io/<owner>/market-workbench-bot:${TAG}|g" .env

docker compose -p market-workbench --env-file .env -f compose.yaml config --quiet
docker compose -p market-workbench --env-file .env -f compose.yaml pull api bot
docker compose -p market-workbench --env-file .env -f compose.yaml up -d --no-deps api
curl -fsS http://127.0.0.1:8000/healthz
docker compose -p market-workbench --env-file .env -f compose.yaml up -d --no-deps bot
```

Rollback 후에도 chart active source는 `chart_ingest_active`를 유지한다. Redis를 durable source로 되살리는 방향은 금지한다.

## API-only rollout 예시

API-only image publish가 끝난 뒤 운영 반영은 별도 작업으로 수행한다.

```bash
TAG="<api-image-tag>"
API_IMAGE="ghcr.io/<owner>/market-workbench-api:${TAG}"

grep -q '^API_IMAGE=' .env \
  && sed -i "s|^API_IMAGE=.*|API_IMAGE=${API_IMAGE}|g" .env \
  || echo "API_IMAGE=${API_IMAGE}" >> .env

docker compose -p market-workbench --env-file .env -f compose.yaml config --quiet
docker compose -p market-workbench --env-file .env -f compose.yaml pull api
docker compose -p market-workbench --env-file .env -f compose.yaml up -d --no-deps api
curl -fsS http://127.0.0.1:8000/healthz
docker compose -p market-workbench --env-file .env -f compose.yaml ps api
```

이 경로는 backfill tool 검증, API hotfix, API-only runtime 변경처럼 BOT image를 움직일 필요가 없는 작업에 사용한다.

## API WS metrics probe

공개 repo에는 API WebSocket gateway metric을 Zabbix cache로 수집하는 public-safe 예시를 포함한다.

```text
scripts/monitoring/market-workbench-api-ws-metrics-probe
scripts/monitoring/market-workbench-zabbix-read
deploy/systemd/market-workbench-api-ws-metrics-probe.service
deploy/systemd/market-workbench-api-ws-metrics-probe.timer
deploy/zabbix/market-workbench-api-ws-userparameters.conf
```

기본 compose 위치와 project name은 운영 host에 맞게 환경변수로 override한다.

```bash
MARKET_WORKBENCH_COMPOSE_DIR=/opt/market-workbench/compose
COMPOSE_PROJECT_NAME=market-workbench
MARKET_WORKBENCH_METRICS_DIR=/var/lib/zabbix/market_workbench_metrics
```

## Frontend 배포

- `release.yml` 및 `deploy/compose.yaml` 기준으로는 frontend build/deploy가 포함되어 있지 않다.
- frontend는 별도 정적 파일 배포 절차로 관리한다.
- public repo에서는 서버 path를 일반화하며, 실제 운영 root와 domain은 문서화하지 않는다.

## 관련 문서

- [아키텍처](ARCHITECTURE.md)
- [Deploy workflow policy](ops/deploy-workflow-policy.md)
- [Chart ingest broker design](CHART_INGEST_BROKER_DESIGN.md)
- [Zabbix 기반 원격 모니터링](Zabbix_Agent2.md)
- [백엔드 구조](BACKEND_STRUCTURE.md)
