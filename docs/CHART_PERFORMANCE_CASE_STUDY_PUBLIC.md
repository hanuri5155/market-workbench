# 차트 성능 개선 사례

기준: `v0.4.3`

## 문제

이 프로젝트의 차트 화면은 multi-timeframe overlay, indicator warm-up, position annotation이 함께 붙는 구조다. 실제 사용성 문제는 cold entry였다. 사용자는 차트 탭에 처음 들어왔을 때, 첫 번째로 쓸 수 있는 차트를 보기까지 너무 오래 기다려야 했다.

문제는 API 하나가 느린 것이 아니었다. 초기 차트 경로가 first candle paint 이전에 너무 많은 작업을 한 번에 수행하고 있었다.

- current timeframe history fetch
- non-critical preload work
- multi-timeframe overlay bootstrap
- indicator side loads
- duplicate bootstrap requests

즉, current timeframe data 자체는 이미 준비될 수 있는데도, 사용자는 차트가 전체 bootstrap을 끝낼 때까지 기다리는 구조였다.

## 측정

먼저 차트 진입 구간을 명시적으로 계측했다.

- chart tab click
- first usable chart paint
- first overlay attach
- mount / unmount / dispose
- grouped side-load timing
- request count before first paint

측정은 desktop과 mobile에서 같은 before-after 방식으로 비교했다.

## 변경

### 1. critical path와 side load 분리

초기 candle paint 경로를 non-critical work와 분리했다.

- current timeframe candles를 먼저 paint
- preload와 overlay 관련 작업은 after-paint/background로 이동
- duplicate bootstrap request 제거

### 2. warm path 재사용

차트 runtime이 매번 cold start처럼 반복되지 않도록 data reuse를 강화했다.

- chart runtime keep-alive
- repeated request dedupe
- existing chart data reuse
- timeframe switch 시 cold-like lifecycle 축소

### 3. grouped startup snapshot 도입

multi-timeframe Structure Zone overlay를 startup grouped read path로 이동했다.

- grouped startup snapshot request 1회
- grouped response로 multiple timeframes 반환
- startup attach용 read-optimized projection 도입

이 구조로 startup 시 request fan-out과 overlay attach coordination cost를 줄였다.

## 결과

### cold entry

| 환경 | 지표 | 작업 전 | 작업 후 |
| --- | --- | ---: | ---: |
| Desktop | First usable chart paint | 13.4s | 0.5s |
| Desktop | First overlay attach | 13.4s | 1.1s |
| Desktop | Requests before first paint | 12 | 2 |
| Mobile (iPhone 14 Pro) | First usable chart paint | 40.0s | 0.6s |
| Mobile (iPhone 14 Pro) | First overlay attach | 40.0s | 1.18s |
| Mobile (iPhone 14 Pro) | Requests before first paint | 14 | 2 |

### 구조적 변화

- cold entry가 non-critical preload를 기다리지 않게 됨
- warm re-entry가 cold-style chart initialization을 반복하지 않게 됨
- timeframe switch에서 full dispose/init 성격의 작업이 줄어듦
- multi-timeframe overlays가 개별 startup fetch 여러 개 대신 grouped startup path로 붙게 됨

## 이 사례가 보여주는 점

이 사례는 세 가지를 보여준다.

1. 성능 개선을 추정이 아니라 측정 기준으로 진행했다는 점
2. 가장 큰 개선은 함수 하나의 미세 최적화가 아니라 실행 순서와 data path를 바꿔서 만들었다는 점
3. desktop뿐 아니라 mobile까지 같은 방식으로 검증해 실제 사용자 체감 변화로 설명할 수 있다는 점
