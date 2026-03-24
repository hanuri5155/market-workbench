# GHCR Multi-Arch ARM64 Checklist

기준: `v0.4.3`

## 목적

OCI ARM64 host에서 API/BOT image를 pull/run하기 전에 GHCR image manifest가 `linux/arm64`를 포함하는지 확인하는 체크리스트다.

## Workflow 확인

- Workflow: `.github/workflows/release.yml`
- Trigger: `v*` tag push
- GHCR push 대상:
  - `ghcr.io/<owner>/market-workbench-api:<tag>`
  - `ghcr.io/<owner>/market-workbench-bot:<tag>`
- Build platform:
  - `linux/amd64`
  - `linux/arm64`
- Deploy job:
  - `self-hosted`, `market-workbench-deploy` label runner
  - compose host 내부에서 `docker compose pull/up`

## Manifest 확인

```bash
TAG="<release-tag>"
OWNER="<github-owner>"

docker buildx imagetools inspect "ghcr.io/${OWNER}/market-workbench-api:${TAG}"
docker buildx imagetools inspect "ghcr.io/${OWNER}/market-workbench-bot:${TAG}"
```

통과 기준:

- `linux/amd64` manifest 존재
- `linux/arm64` manifest 존재
- GHCR 인증 오류 없음

실패 예:

```text
no matching manifest for linux/arm64/v8 in the manifest list entries
```

이 경우 `docker/setup-qemu-action`과 `platforms: linux/amd64,linux/arm64` 설정을 먼저 확인한다.

## Compose 확인

```bash
docker compose -p market-workbench --env-file .env -f compose.yaml config --quiet
docker compose -p market-workbench --env-file .env -f compose.yaml pull api bot nats
docker compose -p market-workbench --env-file .env -f compose.yaml up -d api bot nats
docker compose -p market-workbench --env-file .env -f compose.yaml ps
curl -fsS http://127.0.0.1:8000/healthz
```

## 주의점

- frontend는 release workflow에 포함하지 않는다.
- Redis는 default runtime service가 아니다.
- NATS volume/data path는 Redis 정리와 분리해서 다룬다.
- 실제 host, domain, token, chat id는 공개 문서에 남기지 않는다.
