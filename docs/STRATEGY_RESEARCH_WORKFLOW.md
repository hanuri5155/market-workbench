# 전략 연구 방식

기준: 2026-06-23 공개 스냅샷

이 문서는 Market Workbench에서 전략 후보를 어떻게 연구하고 검증하는지 정리한다.
실제 진입 조건, 청산 조건, 파라미터, 수치 결과는 포함하지 않는다. 대신 후보를 만들고, 데이터를 붙이고, 검증 결과를 해석한 뒤 운영 반영 여부를 나누는 과정을 설명한다.

## 이 문서의 범위

공개하는 것:

- 전략 후보를 연구할 때 사용하는 데이터 검증 흐름
- 라벨, 코호트, MFE/MAE 같은 용어의 의미
- read-only 검증과 leakage guard를 두는 이유
- source provider와 bounded rerun으로 검증 범위를 좁히는 방식
- MA regime, no-entry, external official source 같은 최근 연구 축의 공개 가능한 의미
- 연구 결과를 바로 자동화하지 않고 단계별로 승격하는 방식

공개하지 않는 것:

- 실제 전략의 생성 규칙
- 구체적인 threshold, window, 조건식
- 실전 주문 파라미터와 리스크 설정
- 특정 기간, 심볼, row 수, hit rate 같은 수치 결과
- 자동 주문으로 이어질 수 있는 운영 규칙

## 용어 정리

| 용어 | 의미 |
| --- | --- |
| Structure Zone | 가격 흐름에서 관찰할 만한 후보 구간을 공개용으로 부르는 이름. 실제 전략명이나 후보 산출 기준은 공개하지 않는다. |
| 후보 구간 | 이후 가격 움직임을 추적해볼 수 있는 관찰 대상. 후보 자체가 바로 매매 신호라는 뜻은 아니다. |
| label | 후보 구간이 만들어진 뒤 가격이 어떻게 움직였는지 나중에 붙이는 결과 표식. 유리한 움직임, 불리한 움직임, 도달 시간 등을 함께 본다. |
| cohort | 후보를 같은 성격끼리 나눈 묶음. 전체를 한 번에 보지 않고, 비슷한 조건의 후보끼리 비교하기 위해 쓴다. |
| q4 | 내부 연구에서 쓰는 강한 구조 후보 bucket 이름. 공개 문서에서는 높은 수익 잠재력과 큰 위험을 함께 볼 수 있는 후보군으로만 설명한다. |
| 관찰 가설 버전 | 서로 다른 관찰 방식이나 연구 가설을 구분하기 위한 표현. 공개 문서에서는 내부 축약명 대신 가설 버전이라는 범용 표현만 사용한다. |
| no-entry | 후보가 만들어졌지만 관찰 기간 안에 진입 기준에 닿지 않은 상태. 실패라기보다 후보 품질과 진입 가능성을 나눠 보는 진단 값이다. |
| post-formation pre-entry | 후보 산출 이후, 진입 기준에 닿기 전까지의 관찰 구간. 후보 시점 이후 정보이므로 사용 범위와 leakage guard를 명확히 나눠야 한다. |
| entry-touch | 진입 기준에 실제로 닿은 후보를 따로 보는 관찰 축. 진입 이후 결과와 후보 시점 feature를 섞지 않기 위해 분리한다. |
| near-touch | 진입 기준에 닿지는 않았지만 가까워진 후보를 보는 진단 축. no-entry를 억지로 entry-touch로 바꾸지 않기 위해 둔다. |
| MFE | Maximum Favorable Excursion. 후보 이후 가격이 유리한 방향으로 얼마나 움직였는지 보는 지표. |
| MAE | Maximum Adverse Excursion. 후보 이후 가격이 불리한 방향으로 얼마나 움직였는지 보는 지표. |
| hit rate | 정해진 관찰 기준에 도달한 비율. 공개 문서에서는 실제 비율을 공개하지 않는다. |
| time-to-hit | 기준에 도달하기까지 걸린 시간. 빨리 도달했는지, 늦게 도달했는지 해석할 때 쓴다. |
| MA regime | 이동평균 위치로 후보가 어떤 시장 구간에 있었는지 나눠 보는 진단 축. 공개 레포에서는 production rule이 아니라 보조 검증 축으로만 설명한다. |
| 20D | 20일 이동평균을 가리키는 내부 축약어. 방향성, side alignment, 단기 regime을 나눠 보는 데 쓰지만 단독 자동진입 조건은 아니다. |
| source provider | summary 계산에 필요한 원천 데이터를 정해진 형태로 공급하는 코드 경계. 누락 row, 품질 flag, unsupported source를 summary에 남겨 과해석을 막는다. |
| bounded rerun | 전체 운영 데이터를 무제한 재실행하지 않고, 정해진 작은 범위에서 read-only / summary-only로 재확인하는 실행. |
| external official source | 거래소 공식 OI, funding, mark/index premium, taker flow처럼 내부 candle만으로는 알 수 없는 외부 검증 후보. |
| leakage guard | 미래 정보를 실수로 feature에 섞지 않기 위한 장치. 연구 결과가 실제보다 좋아 보이는 것을 막는다. |
| read-only dry-run | 운영 데이터나 저장소를 바꾸지 않고 계산 결과만 확인하는 검증 실행. |
| promotion gate | 연구 결과를 다음 단계로 넘길지 판단하는 기준. 문서 검토, 모의 관찰, 작은 범위의 실험처럼 단계를 나눠서 본다. |

## 연구 흐름

### 1. 후보 구간을 만든다

먼저 차트와 시장 데이터에서 관찰할 만한 Structure Zone 후보를 만든다.
공개 레포에서는 이 부분을 `demo_zone`으로 대체한다. `demo_zone`은 시스템 흐름을 보여주기 위한 샘플이며, 실제 후보 산출 기준은 포함하지 않는다.

### 1-1. 연구용 데이터를 준비한다

후보를 평가하려면 관찰 기준이 되는 candle 데이터가 먼저 안정적으로 준비되어야 한다.
최근 공개 스냅샷에는 `backend/core/tools/backfill_candles.py`의 1분봉 backfill plan 경로를 포함했다.

이 도구는 바로 DB에 쓰기보다 먼저 다음을 계산한다.

- 어느 기간을 대상으로 볼지
- 몇 개의 candle row가 예상되는지
- API page가 몇 번 필요할지
- 어떤 validation check를 실행해야 하는지
- 실제 write를 하기 전에 dry-run/plan으로 멈출 수 있는지

구체적인 기간과 결과 수치는 공개하지 않는다. 대신 연구 전에 데이터를 어떻게 안전하게 준비하고, gap/duplicate/null/OHLC 정합성을 어떤 기준으로 확인하는지 보여주는 목적이다.

### 2. 결과 label을 붙인다

후보가 만들어진 뒤에는 그 후보가 어떤 결과로 이어졌는지 label을 붙인다.
여기서 중요한 점은 label이 매매 성과를 자랑하기 위한 값이 아니라, 후보를 검증하기 위한 기준이라는 점이다.

예를 들면 다음을 본다.

- 유리한 방향으로 얼마나 움직였는지
- 불리한 방향으로 얼마나 밀렸는지
- 기준에 도달했다면 얼마나 걸렸는지
- 후보가 너무 늦게 반응했거나 관찰 범위를 벗어나지는 않았는지
- 같은 후보라도 최근 구간과 과거 구간에서 해석이 달라지는지

### 3. feature와 label을 분리해서 검증한다

연구에서 가장 조심하는 부분은 미래 정보가 feature에 섞이는 것이다.
후보가 만들어지는 시점에 알 수 없던 값을 사용하면, 결과가 실제보다 좋아 보일 수 있다.

그래서 검증 단계에서는 다음 원칙을 둔다.

- 후보 시점 이전 또는 관찰 가능한 범위의 정보만 feature로 사용
- 결과 label은 검증 기준으로만 사용
- row-level 원본을 공개하지 않고 aggregate summary로만 판단
- DB write나 production state 변경 없이 read-only dry-run으로 먼저 확인

### 4. cohort로 나눠서 본다

전체 후보 평균만 보면 좋은 후보와 위험한 후보가 섞여 보일 수 있다.
그래서 q4 같은 cohort를 두고, 같은 cohort 안에서도 관찰 가설 버전별로 결과를 나눠 본다.

이때 MFE, MAE, hit rate, time-to-hit 같은 지표를 함께 본다.
한 지표가 좋아 보여도 다른 지표가 나쁘면 바로 rule로 만들지 않는다.

예를 들어 어떤 후보군은 유리한 움직임도 크지만 불리한 흔들림도 클 수 있다.
이 경우에는 "무조건 제외"가 아니라 risk diagnostic으로 남겨두고, position sizing이나 경고 표시 같은 다음 연구 후보로 분리한다.

### 4-1. source provider와 bounded rerun으로 검증한다

최근 연구는 단순히 문서 해석만 늘린 것이 아니라, summary에 필요한 source row를 별도 provider로 공급하고 그 품질을 함께 기록하는 방향으로 정리했다.

공개 레포에서는 이 구조를 `backend/core/research/public_validation_summary.py`로 작게 보여준다. 실제 전략 조건이나 target 값을 넣지 않고도 다음 경계를 코드로 확인할 수 있다.

- 같은 후보 row가 중복으로 들어오면 한 번만 반영한다.
- feature cutoff보다 앞선 label observation은 leakage 가능성이 있어 제외한다.
- 작은 sample은 `caveat_only`로 남기고 production rule로 승격하지 않는다.
- 공개 output에는 candidate id, raw row, target value, 전략 조건을 내보내지 않는다.

이 helper는 실전 연구 엔진이 아니라 공개용 축약 예제다. 목적은 source provider, aggregate-only summary, leakage guard가 어떤 책임을 갖는지 코드로 읽히게 하는 것이다.

### 4-2. MA regime과 외부 source를 분리한다

MA regime은 후보가 이동평균 기준으로 어떤 시장 구간에 있었는지 설명하는 보조 진단 축이다.
20D position, side alignment, q4 여부가 함께 보이면 유용한 가설이 될 수 있지만, 현재 공개 기준에서는 다음 제한을 둔다.

- bounded sample이 full validation이나 time-split validation을 대체하지 않는다.
- q4 source-backed segment가 없으면 q4 production readiness를 주장하지 않는다.
- recent split이 없으면 최근 구간에서도 같은 결론이라고 말하지 않는다.
- 내부 candle/source만으로 남은 후보는 external official source 검증 전까지 provisional이다.

외부 source는 Bybit/Binance 공식 OI, funding, mark/index premium, taker flow, order-book/liquidation archive처럼 내부 candle만으로 알 수 없는 맥락을 보완하기 위한 후보로 둔다. 현재 공개 스냅샷은 external API call이나 backfill을 실행하지 않고, 어떤 source를 우선 검토할지와 어떤 source를 보류할지만 설명한다.

### 5. 바로 자동화하지 않는다

연구에서 좋아 보이는 결과가 있어도 바로 운영 규칙으로 승격하지 않는다.
현재 연구 단계의 기준은 다음과 같다.

- label 설계와 read-only 계산 경로를 먼저 검증
- leakage guard와 sample caveat를 문서화
- cohort별 방향성이 일관적인지 확인
- 단독 rule인지, 보조 지표인지, risk diagnostic인지 구분
- 자동 주문, UI 표시, score/model 반영은 별도 단계로 보류

이 방식은 전략을 숨기기 위한 장치이기도 하지만, 더 중요하게는 연구 결과를 과신하지 않기 위한 장치다.

## 최근 연구의 공개 가능한 요약

최근 연구에서는 Structure Zone 후보를 q4, no-entry, post-formation pre-entry, MA regime, short compression, external official source라는 축으로 다시 나눠 보았다.

q4는 단일 포함/제외 규칙으로 만들지 않고, balanced candidate, high-potential/high-risk diagnostic, early-touch diagnostic, unclassified safety bucket처럼 해석 언어를 나눴다. 이 분류는 production taxonomy가 아니라 report-only 진단 기준이다.

MA regime은 20D/200D 위치와 side alignment를 보는 보조 진단으로 남겼다. 일부 view는 future drill-down 후보가 될 수 있지만, bounded sample, recent split 부재, q4 source-backed segment 부재 때문에 자동진입이나 UI 강조로 승격하지 않는다.

short non-q4 compression 쪽은 특정 subtype이 많이 관찰되더라도 balance caveat와 외부 source 부재가 있으면 broad diagnostic으로만 둔다. 후보가 많다는 사실만으로 production rule이 되지 않는다.

외부 source 계획은 Bybit/Binance 공식 지표와 archive source를 우선 검토하고, current-only 또는 재현할 수 없는 source를 보류하는 방향으로 정리했다. 내부-only 후보는 external composite validation 전까지 provisional이다.

운영 데이터 준비 측면에서는 1분봉 backfill을 바로 write하지 않고 plan/dry-run과 validation checklist를 먼저 통과시키는 흐름을 유지한다. 이 부분은 전략 자체를 드러내지 않으면서도, 연구가 임의 샘플이 아니라 데이터 수집/검증 절차 위에서 진행된다는 점을 보여준다.

수치와 조건은 공개하지 않는다. 공개 레포에서는 연구 방식과 검증 경계만 보여준다.

## 수치를 공개하지 않는 이유

이 문서가 보여주려는 것은 전략 성과가 아니라 연구 방식이다.
구체적인 수치나 조건을 공개하면 실제 전략을 유추할 수 있고, 동시에 공개 레포가 성과를 홍보하는 문서처럼 읽힐 수 있다.

그래서 공개 레포에서는 "어떤 지표를 보고, 어떤 절차로 걸러냈는지"까지만 설명한다.
실제 수치, 조건식, 운영 반영 여부는 private 원본에서 관리한다.

## 공개 레포에서 이 연구가 연결되는 위치

- `backend/strategies/base/`: 공개/비공개 전략을 같은 runtime에 연결하기 위한 interface
- `backend/strategies/demo_zone/`: 실전 전략 대신 시스템 흐름을 보여주는 데모 전략
- `backend/core/research/public_validation_summary.py`: 공개용 aggregate-only 연구 summary helper
- `backend/tests/test_public_validation_summary.py`: leakage row 제외, 작은 sample caveat, raw id 비공개 경계 테스트
- `backend/core/persistence/zone_state_repo.py`: 후보 상태를 저장하고 조회하는 공개용 repository
- `backend/core/tools/backfill_candles.py`: 연구용 candle 데이터를 plan/dry-run 기준으로 준비하는 공개용 도구
- `frontend/src/components/candle-chart/zones/`: 차트에 후보 구간을 표시하는 공개용 overlay
- `docs/CHART_PERFORMANCE_CASE_STUDY_PUBLIC.md`: 후보 구간을 차트에 빠르게 붙이기 위해 read path를 개선한 사례
- `docs/research/structure-zone-research-public-20260623.md`: 2026-06-23 기준 Structure Zone 연구 공개 요약

즉, 공개 레포는 실전 전략 규칙보다 연구, 검증, 운영 시스템 연결 구조를 중심으로 정리한 저장소다.
