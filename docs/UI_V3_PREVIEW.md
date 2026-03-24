# UI v3 정리

기준: public snapshot + 2026-06-06 clarification

## 목적

UI v3는 공개 포트폴리오에서 기본으로 보여주는 운영형 workspace다. browser-facing 차트 데이터 계약은 유지하고, 화면 구조와 정보 위계를 chart-first 방식으로 재배치한다.

## Scope Guard

UI v3는 frontend presentation 변경으로 출발했다. 최신 공개 스냅샷에서는 backend chart active source가 `chart_ingest_active`로 정리되었지만, 브라우저가 보는 `/ws/chart-candles` protocol은 유지한다.

- `/ws/chart-candles` protocol 변경 없음
- browser-facing payload 변경 없음
- Docker/frontend 배포 방식 변경 없음

## 라우트

| 경로 | 상태 | 설명 |
| --- | --- | --- |
| `/` | UI v3 Control | strategy guard, live state, route board |
| `/chart` | UI v3 Chart | 기존 `CandleChart` runtime, Structure Zone deck |
| `/stats` | UI v3 Stats | 공개용 analytics mock/read surface |
| `/settings` | UI v3 Settings | 공개용 settings surface |
| `/ui-v2/*` | UI v2 preserved | 이전 shell 비교/롤백 검토 route |
| `/ui-preview/*` | UI v2 preview | OTP를 우회하는 읽기 전용 디자인 검토 route |

## 실제 연결 범위

### 연결됨

- `frontend/src/components/OtpGate.jsx`
  - `/api/auth/otp/status`
  - `/api/auth/otp/verify`
  - HttpOnly `otp_session` 쿠키 기반 접근 제어
- `frontend/src/ui-v3/pages/ChartWorkspace.jsx`
  - 기존 `CandleChart` runtime 탑재
  - `/api/candles/{tf}`, `/api/candles/latest/{tf}`
  - `/ws/chart-candles`
  - `/ws/zones`
  - Structure Zone notification context/deck
- `frontend/src/ui-v3/UiV3App.jsx`
  - UI v2 `useLiveTicker` 재사용
  - active timeframe state를 chart workspace와 header에 전달
- `frontend/src/ui-v3/pages/ControlCenter.jsx`
  - `/api/strategy_flags`
  - `/api/strategy_flags/enable_trading`
  - `/api/strategy_flags/enable_zone_strategy`

### 공개용 read/mock surface

- `AnalyticsDashboard.jsx`
  손익 카드, equity curve, trade table은 공개용 mock data 기반
- `SettingsPanel.jsx`
  설정 섹션과 입력 UI만 배치. 저장 API는 공개판에서 비활성

## 구현 메모

- UI v3는 `frontend/src/ui-v3` 경계 안에 둔다.
- chart와 websocket logic은 기존 `CandleChart`, `useLiveTicker`, `ZoneNotificationContext`를 재사용한다.
- Structure Zone은 텍스트 피드가 아니라 risk band/decision deck으로 표현한다.
- control toggle은 기존 strategy flag API 계약만 사용한다.
- CSS variable과 regular CSS만 사용한다. 새 UI dependency를 추가하지 않는다.

## UI v2와의 관계

- UI v2는 `/ui-v2`에 보존한다.
- `/ui-preview`는 OTP를 우회하는 읽기 전용 preview route다.
- 새 기본 화면은 UI v3다.

## 공개 범위

- 공개: 화면 구조, component boundary, chart runtime reuse, OTP/control/zone 연결 방식
- 비공개: 실전 전략 규칙, 실거래 파라미터, 운영 domain/server 정보

## 관련 문서

- [README](../README.md)
- [아키텍처](ARCHITECTURE.md)
