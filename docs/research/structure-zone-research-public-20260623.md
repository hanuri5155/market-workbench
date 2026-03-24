# Structure Zone 연구 공개 요약

기준: 2026-06-23 공개 스냅샷

이 문서는 비공개 레포의 Structure Zone 후보 연구 흐름을 공개 레포 문맥으로 다시 정리한 것이다. 실제 전략 조건, threshold, raw row, 운영 수치, 주문 파라미터는 포함하지 않는다. 대신 후보를 어떻게 만들고, 어떤 기준으로 검증하며, 왜 아직 자동 매매 규칙으로 승격하지 않는지를 설명한다.

## 먼저 이해할 용어

| 용어 | 공개 문맥에서의 의미 |
| --- | --- |
| Structure Zone | 가격 흐름에서 관찰할 만한 후보 구간을 공개용으로 부르는 이름. 실제 후보 산출 기준과 전략명은 공개하지 않는다. |
| 후보 구간 | 나중에 결과를 붙여 검증할 관찰 대상. 후보라는 말은 곧바로 매매 신호라는 뜻이 아니다. |
| q4 | 내부 연구에서 쓰는 강한 구조 후보 bucket 이름. 공개 문서에서는 높은 수익 잠재력과 큰 위험을 함께 볼 수 있는 후보군으로만 설명한다. |
| no-entry | 후보가 만들어졌지만 관찰 기간 안에 진입 기준에 닿지 않은 상태. 실패라기보다 후보 품질과 진입 가능성을 나눠 보는 진단 값이다. |
| post-formation pre-entry | 후보가 산출된 뒤, 실제 진입 기준에 닿기 전까지의 구간. 미래 정보를 쓰지 않고 후보의 상태를 관찰하기 위한 구간이다. |
| entry-touch / near-touch | 진입 기준에 닿았거나 가까워진 후보를 따로 보는 관찰 축. 빠르게 닿은 후보와 끝까지 닿지 않은 후보를 같은 방식으로 해석하지 않기 위해 둔다. |
| MFE / MAE | 후보 이후 유리한 방향으로 움직인 폭과 불리한 방향으로 움직인 폭. 성과 홍보가 아니라 후보 위험과 잠재력을 같이 보기 위한 label이다. |
| MA regime | 20D, 200D 같은 이동평균 위치를 이용해 후보가 어떤 시장 구간에 있었는지 나눠 보는 진단 축. 공개 레포에서는 방향성 검증 흐름만 설명한다. |
| source provider | 요약 계산에 필요한 원천 데이터를 정해진 형태로 공급하는 코드 경계. 누락, 품질 flag, 사용 불가 row를 summary에 남겨 과해석을 막는다. |
| bounded rerun | 전체 운영 데이터를 마음대로 다시 계산하지 않고, 정해진 작은 범위에서 read-only / summary-only로 재확인하는 실행. |
| external official source | 거래소 공식 OI, funding, mark/index premium, taker flow처럼 내부 candle만으로는 알 수 없는 외부 검증 후보. |

## 공개 반영 범위

이번 공개 반영은 2026-06-18 이후 비공개 연구 흐름을 그대로 복사하지 않고, 공개 독자가 이해할 수 있는 연구 단계로 묶었다.

| 내부 연구 흐름 | 공개 반영 방식 |
| --- | --- |
| no-entry bridge와 q4 diagnostic taxonomy | q4를 단일 포함/제외 규칙으로 만들지 않고, balanced / high-risk / early-touch / unclassified 같은 진단 언어로 분리하는 방식만 설명 |
| q4 score validation과 bounded source provider | 자연키 기반 source row, 품질 flag, leakage guard, aggregate-only summary 원칙을 문서화 |
| MA regime과 20D 재검증 | 이동평균 regime을 production rule이 아니라 bounded diagnostic denominator로만 다루는 기준을 설명 |
| short non-q4 compression 진단 | 특정 subtype이 많아 보여도 balance caveat와 외부 source 부재가 있으면 자동진입 후보로 승격하지 않는 판단 기준을 설명 |
| external official source backfill 계획 | Bybit/Binance 공식 지표와 order-book/liquidation archive를 future validation source로 분리하고, 현재-only source나 재현 불가능한 source는 배제하는 정책을 설명 |

## 연구 흐름

### 1. 후보를 만든 뒤 바로 판단하지 않는다

Structure Zone은 차트에서 관찰할 후보 구간을 만드는 출발점이다. 공개 레포의 `demo_zone`은 이 흐름을 보여주기 위한 샘플이며, 실제 후보 산출 기준은 포함하지 않는다.

후보가 만들어지면 바로 주문 규칙으로 쓰지 않고, 이후 가격 움직임을 label로 붙여 검증한다. 이때 label은 결과 확인용이며, 후보 시점에 알 수 없던 정보를 feature로 쓰지 않는다.

### 2. 결과 label과 feature를 분리한다

연구에서 가장 중요한 경계는 leakage guard다. 예를 들어 후보가 만들어진 뒤에야 알 수 있는 MFE/MAE, hit 여부, no-entry 결과가 feature 계산에 섞이면 실제보다 좋은 결과처럼 보일 수 있다.

그래서 공개용 helper인 `backend/core/research/public_validation_summary.py`도 다음 원칙을 코드로 고정한다.

- `feature_cutoff_ms` 이후에 관측된 label만 summary에 포함한다.
- 같은 `candidate_id`가 중복으로 들어오면 한 번만 센다.
- 작은 sample group은 `caveat_only`로 두고 production rule로 승격하지 않는다.
- 공개 output에는 candidate id, raw row, target value, 전략 조건을 내보내지 않는다.

### 3. q4는 하나의 결론이 아니라 진단 언어로 다룬다

q4는 내부 연구에서 강한 구조 후보 bucket을 가리키는 이름이다. 최근 연구에서는 q4를 "좋다" 또는 "나쁘다"로 단순히 나누지 않고, 다음처럼 역할을 분리했다.

| 진단 언어 | 해석 |
| --- | --- |
| balanced candidate | 상대적으로 균형적인 후보로 보이지만, 아직 production include rule은 아니다. |
| high-potential / high-risk | 유리한 움직임 가능성과 큰 불리한 움직임 위험이 함께 보이는 후보. hard avoid가 아니라 risk diagnostic이다. |
| early-touch diagnostic | 너무 빠르게 진입 기준에 닿거나 같은 candle에서 해석이 꼬일 수 있는 후보를 별도로 분리한다. |
| unclassified | evidence가 부족하거나 섞여 있어 강제로 분류하지 않는 안전 bucket이다. |

이 방식은 후보를 숨기기 위한 장치라기보다, 연구 결과를 성급하게 자동화하지 않기 위한 장치다.

### 4. MA regime은 보조 진단으로만 둔다

20D/200D 이동평균 위치는 후보가 어떤 시장 구간에서 나온 것인지 설명하는 데 도움이 된다. 다만 최근 bounded sample 기준으로도 일부 group은 sample이 작거나 반대 상태 비교가 부족하다. 따라서 공개 레포에서는 MA regime을 "조건이 맞으면 진입" 같은 규칙으로 표현하지 않는다.

현재 공개 가능한 결론은 다음 정도다.

- MA readiness가 충분한 후보만 primary denominator로 해석한다.
- 20D position과 side alignment는 추가 진단 후보가 될 수 있다.
- recent/time split, q4 source-backed segment, 외부 source validation이 없으면 production readiness를 주장하지 않는다.

### 5. 내부 source만으로는 provisional이다

최근 연구에서 남은 후보 family는 내부 candle, 구조 후보 형성, post-formation, entry-touch, near-touch, MA source만으로 본 provisional 후보로 둔다. 내부 지표가 좋아 보이거나 나빠 보여도 외부 official source가 붙기 전까지는 ranking이 바뀔 수 있다.

외부 검증 후보는 다음처럼 나눈다.

| source | 목적 |
| --- | --- |
| Bybit official OI / funding / mark-index premium | 내부 후보가 파생상품 포지션 쏠림, funding, mark/index 괴리와 함께 움직였는지 확인 |
| Binance official funding / taker / mark-index premium | 한 거래소만의 현상인지 cross-venue 맥락이 있는지 확인 |
| order book / liquidation archive | 현재 API만으로 재현하기 어려운 깊이/청산 맥락을 archive source로 검토 |

현재-only source나 재현할 수 없는 source는 연구 결과를 다시 검증하기 어렵기 때문에 우선순위를 낮춘다.

## 공개 코드와 연결되는 지점

| 파일 | 역할 |
| --- | --- |
| `backend/core/research/public_validation_summary.py` | 공개용 aggregate-only summary helper. source provider와 bounded summary의 안전 경계를 작은 코드로 보여준다. |
| `backend/tests/test_public_validation_summary.py` | raw row를 내보내지 않는지, leakage row를 제외하는지, 작은 sample을 caveat로 남기는지 검증한다. |
| `backend/strategies/demo_zone/` | 실전 전략 대신 Structure Zone runtime 연결 흐름만 보여준다. |
| `backend/core/tools/backfill_candles.py` | 연구용 candle 데이터를 plan/dry-run 중심으로 준비하는 공개용 도구다. |
| `docs/STRATEGY_RESEARCH_WORKFLOW.md` | 전체 연구 방식, 용어, 공개/비공개 경계를 설명한다. |

## 아직 하지 않는 것

- production q4 include/avoid rule
- score formula 또는 score threshold
- model training
- UI highlight / 추천 badge
- shadow, paper, live auto-entry
- raw 연구 row 공개
- 외부 API 실제 backfill 실행
- 운영 DB read/write 또는 schema 변경

공개 레포의 연구 파트는 성과나 조건식을 보여주는 문서가 아니라, 후보를 안전하게 검증하고 과해석을 막는 절차를 보여주는 문서다.
