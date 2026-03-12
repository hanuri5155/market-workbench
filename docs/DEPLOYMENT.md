# 배포 구조 메모

## 원칙

- 운영 서버는 소스 빌드보다 이미지 pull 기준
- 릴리스 태그 `v*`는 변경 불가능한 배포 기준점
- 배포 단위는 API와 bot 이미지 기준
- 문서 안의 경로와 시크릿 값은 placeholder 기준
- 현재 문서는 배포 구조 설명용 메모 기준

## 흐름 요약

- 트리거
  `v*` 태그 푸시
- 빌드 대상
  `ghcr.io/<owner>/market-workbench-api:<tag>`
  `ghcr.io/<owner>/market-workbench-bot:<tag>`
- 배포 순서
  이미지 태그 생성 -> 서버 쪽 `.env` 반영 -> API / bot 재기동 -> `/healthz` 확인

## 필요한 시크릿

- `OCI_HOST`
- `OCI_USER`
- `OCI_SSH_KEY`
- `GHCR_USER`
- `GHCR_PAT`

## 서버 디렉터리 예시

```text
/srv/market-workbench/deploy
  ├── compose.yaml
  ├── .env
  └── data/
      ├── config/
      ├── storage/
      └── logs/
```

## 운영 확인 포인트

- API와 bot 이미지 태그 일치 여부
- `/healthz` 응답 여부
- heartbeat 갱신 여부
- 로그 디렉터리 쓰기 상태
- `CANDLE_REST_NOTIFY_URL` / `MTF_MA_SOURCE_NOTIFY_URL` 이 API 컨테이너를 가리키는지 확인
- MTF MA live sync 관련 변경은 API와 bot을 함께 재기동해야 반영
- bot 로그에 `connection refused` 또는 `read timeout` 이 반복되면 internal notify 경로와 API 응답 지연을 우선 점검

## 메모

- 프론트 정적 배포 방식은 이 문서 범위 밖
- 실제 배포 절차와 시크릿 값은 운영 환경에서 별도 관리
- 실제 시크릿과 호스트값은 운영 저장소 또는 운영 서버에서 관리
