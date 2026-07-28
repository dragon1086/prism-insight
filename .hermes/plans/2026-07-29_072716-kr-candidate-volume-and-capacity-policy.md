# KR 후보 발굴 개수·분석 용량 정책

**상태:** 구현 전 기준선 — 실제 분포는 실데이터 계측 후 확정
**작성 기준시각:** 2026-07-29 07:27 KST
**적용 범위:** 한국장 일일 종가 제품만. 미국장은 보류한다.
**상위 계획:** [KR Daily Product Completion Plan](./2026-07-28_221724-kr-first-daily-product-and-us-adaptation.md)

---

## 1. 목적

현재는 “후보가 몇 개 나오는가”가 하나의 숫자로 보이지 않는다. 레거시 코드에는 다음 수가 서로 섞여 있다.

1. 시장 전체 입력 종목 수
2. 각 trigger의 조건을 통과한 종목 수
3. trigger 내부 순위 제한 뒤 남은 종목 수
4. 여러 trigger 사이 중복 제거 전·후 종목 수
5. 레거시 최종 selector가 고른 종목 수
6. 외부 주도군 보강 후보 수
7. 실제 SWING/TREND 분석에 들어간 종목 수
8. 데이터 문제나 provider 실패로 완료하지 못한 종목 수

이 문서는 이 수들을 분리해 매 실행마다 보이게 하고, 실제 분포를 측정하기 전 임의의 분석 상한을 두지 않도록 한다. 또한 “무제한”을 무제한 동시 호출이나 무한 재시도로 오해하지 않도록 처리 용량 정책을 별도로 정의한다.

---

## 2. 현재 코드에서 확인되는 개수 제한

### 2.1 레거시 trigger 단계

`trigger_batch.py`의 현재 종가 배치는 기본 trigger 3개를 실행한다.

- 일중 상승률 상위주
- 마감 강도 상위주
- 거래량 증가 상위 횡보주

공유 시장 국면이 있고, `leading_sectors`·`sector_map`이 실제 종목과 매칭되어 결과가 비어 있지 않으면 매크로 섹터 리더를 추가한다. `sideways`, `moderate_bear`, `strong_bear`에서 역발상 조건을 통과한 결과가 비어 있지 않으면 역발상 가치주도 추가한다.

각 trigger는 현재 최종 반환 직전에 최대 10개로 잘린다. 따라서 **중복 제거 전 raw 후보 행의 코드상 상한**은 다음과 같다.

| 현재 실행 구성 | 활성 trigger | raw 후보 행 상한 | 고유 종목 상한 | 활성 조건 |
|---|---:|---:|---:|---|
| 기본 3개만 | 3 | 30 | 최대 30 | `macro_context`가 없거나 두 보조 trigger가 모두 빈 결과 |
| 기본 3개 + 보조 trigger 1개 | 4 | 40 | 최대 40 | 매크로 또는 역발상 중 정확히 하나의 결과만 비어 있지 않음 |
| 기본 3개 + 매크로 섹터 + 역발상 가치 | 5 | 50 | 최대 50 | 매크로 결과가 비어 있지 않고, `sideways`/약세 국면에서 역발상 결과도 비어 있지 않음 |

고유 종목 수는 trigger 간 중복 때문에 raw 행 수보다 작거나 같다. 위 숫자는 **실제 관측값이나 예상 평균이 아니라 정적 코드 상한**이다.

이 상한 앞에도 숨은 순위 window가 있다.

- 매크로 섹터 리더는 절대 유동성 필터 뒤 거래대금 상위 100개만 sector criterion을 평가한다.
- 역발상 가치주는 절대 유동성·당일 상승 필터 뒤 거래대금 상위 50개만 52주 고점·PER·PBR criterion을 평가한다.

따라서 두 trigger의 “criterion 통과 수”는 시장 전체 통과 수가 아니라 **명시된 pre-criterion rank window 안에서의 통과 수**다. 이 100/50 window도 기존 발굴 정책 제한으로 별도 계측한다.

### 2.2 레거시 최종 selector 단계

현재 `select_final_tickers()`에는 `max_selections = 3`이 있다. 따라서 현재 사용자 표면의 최종 `CORE_PRISM` 결과는 앞 단계에서 30~50개의 raw 후보 행이 생겨도 최대 3개다.

### 2.3 보강 후보 단계

`SUPPLEMENTAL_LEADERSHIP` 후보 생성기는 아직 실제 제품 경로로 완성되지 않았다. 따라서 다음 값은 현재 근거 있게 말할 수 없다.

- 하루 보강 후보 평균
- core와 보강 후보의 중복률
- 합산 고유 후보의 p50/p90/p95/최댓값
- 후보별 SWING/TREND 분석 소요시간과 LLM 사용량

보강 후보 수를 임의로 2개, 3개 또는 다른 숫자로 가정하지 않는다.

### 2.4 현재 알 수 있는 것과 모르는 것

```text
현재 정적으로 알 수 있음
- core raw 후보 행: 실행 구성에 따라 최대 30/40/50
- 레거시 최종 core 출력: 최대 3

현재 실측 전에는 모름
- trigger 조건 통과 전 종목 수
- raw 후보의 실제 일별 분포
- trigger 간 실제 중복률
- 보강 후보 수와 core 중복률
- 합산 고유 후보 수
- 모든 후보를 SWING/TREND로 처리하는 실제 시간·호출량
```

---

## 3. 용어와 후보 수 funnel

앞으로 “몇 종목이 발굴됐는가”는 아래 funnel 전체로 보고한다. 하나의 `candidate_count`로 뭉개지 않는다.

| 필드 | 의미 |
|---|---|
| `market_universe_count` | 해당 실행이 실제로 읽은 한국 상장·거래가능 universe 수 |
| `trigger_input_count` | 절대 유동성·거래가능성 등 공통 필터 뒤 trigger가 받은 수 |
| `trigger_precriterion_window_count` | criterion 평가 전에 거래대금 순위 window가 있으면 그 안에 들어온 수. window가 없으면 `trigger_input_count`와 같음 |
| `trigger_precriterion_excluded_count` | `trigger_input_count - trigger_precriterion_window_count`; 100/50 등 window 때문에 criterion 평가도 받지 못한 수 |
| `trigger_criterion_match_count` | 각 trigger가 명시한 evaluation window 안에서 정량 조건을 통과한 수. 시장 전체 통과 수로 해석하지 않음 |
| `trigger_emitted_count` | trigger가 현재 정책에 따라 내보낸 수 |
| `core_raw_record_count` | core trigger 출력 행의 합. 중복 포함 |
| `core_unique_count` | stable security identity로 중복 제거한 core 수 |
| `supplemental_raw_record_count` | 보강 원천이 내보낸 행의 합. 중복 포함 |
| `supplemental_unique_count` | 보강 원천 내부 중복 제거 후 수 |
| `cross_channel_overlap_count` | core와 보강 양쪽에서 발견된 고유 종목 수 |
| `total_unique_candidate_count` | 두 채널을 합쳐 중복 제거한 최종 고유 후보 수 |
| `decision_eligible_candidate_count` | 핵심 데이터가 완전해 완전한 전략 predicate를 평가할 수 있는 수 |
| `report_only_candidate_count` | 기술·시장 시나리오 분석은 가능하지만 진입 수준을 내면 안 되는 수 |
| `data_unavailable_candidate_count` | 핵심 데이터 부족으로 분석할 수 없는 수 |
| `analysis_candidate_count` | `decision_eligible_candidate_count + report_only_candidate_count` |
| `strategy_evaluation_planned_count` | `analysis_candidate_count × 2` — SWING_V1과 TREND_V1을 분리한 평가 수 |
| `strategy_evaluation_completed_count` | validator와 저장까지 완료한 전략 평가 수 |
| `strategy_evaluation_failed_count` | provider·LLM·validation·storage 오류로 완료하지 못한 전략 평가 수 |
| `truncated_candidate_count` | 처리 용량 때문에 버린 후보 수. 정상 정책에서는 항상 0이어야 함 |
| `resumable_pending_candidate_count` | 아직 버리지 않고 durable queue에서 다음 재개를 기다리는 후보 수 |
| `resumable_pending_evaluation_count` | 아직 완료·실패하지 않고 재개를 기다리는 SWING/TREND 평가 수 |
| `analysis_incomplete_candidate_count` | admission 뒤 하나 이상의 필수 전략 평가가 완료되지 않은 고유 후보 수. admission status와 별도 batch outcome |

상태 층을 섞지 않는다.

- **Candidate admission:** `ELIGIBLE`, `REPORT_ONLY`, `DATA_UNAVAILABLE`
- **Evaluation execution:** `COMPLETED`, `FAILED`, `PENDING`; 후보 요약으로 `ANALYSIS_INCOMPLETE`를 파생할 수 있음
- **Strategy/policy outcome:** `WATCH`, `NO_ENTRY`, `ENTRY_CANDIDATE`, `REPORT_ONLY`

`REPORT_ONLY`, `DATA_UNAVAILABLE`, `ANALYSIS_INCOMPLETE`, `NO_ENTRY`는 서로 대체하지 않는다. 특히 데이터가 부족한 종목을 `NO_ENTRY`로 세어서는 안 된다.

---

## 4. 개수 제한 정책

### 4.1 현재 결정

1. **reconciliation 이후 고유 후보에는 top-N 상한을 두지 않는다.**
2. **분석 대상 수와 LLM 호출 수 때문에 후보를 조용히 버리지 않는다.**
3. 같은 종목이 여러 trigger·채널에서 발견되면 stable identity로 한 종목으로 합치되, 모든 발견 경로와 점수·근거를 보존한다.
4. 각 고유 decision-eligible 또는 `REPORT_ONLY` 후보는 `SWING_V1`과 `TREND_V1`을 각각 평가한다. `REPORT_ONLY`는 분석 생략이 아니라 actionable level veto다.
5. 한 종목 또는 한 전략의 실패가 형제 후보를 중단시키지 않는다.
6. 실행 시간이 길어지면 미처리 후보를 durable queue에 남기고 같은 snapshot 경계에서 재개한다.
7. provider quota·429·timeout은 backoff와 재개로 처리한다. 이를 후보 탈락 근거로 사용하지 않는다.
8. `truncated_candidate_count > 0`이면 실행을 정상 완료로 보고하지 않는다.

### 4.2 “무제한”의 정확한 뜻

```text
무제한인 것
- reconciliation 이후 분석할 고유 후보의 총수
- 후보 총수 때문에 수행하는 top-N truncation

제한하는 것
- 동시에 실행하는 provider/LLM 요청 수
- provider별 rate limit
- 요청별 timeout
- 후보별 재시도 횟수와 backoff
- 한 프로세스의 lease와 실행시간
- 저장·재개의 idempotency
```

즉, **총 작업량은 버리지 않고 동시 작업량만 제한한다.** 동시성 값은 실제 KIS·DART/KIND·ChatGPT OAuth smoke의 latency와 quota를 측정한 뒤 정한다. 근거 없는 숫자를 이 문서에서 운영값으로 확정하지 않는다.

### 4.3 레거시 trigger의 순위 제한 처리

현재 trigger별 최대 10개와 매크로/역발상의 사전 100/50 window는 분석 엔진의 용량 제한이 아니라 **기존 후보 발굴 정책 제한**이다. 이를 제거했다고 가장하거나 숨기지 않는다. 따라서 다음 수를 모두 보존해야 한다.

- `trigger_input_count`
- `trigger_precriterion_window_count`와 `trigger_precriterion_excluded_count`
- 선언된 evaluation window 안의 `trigger_criterion_match_count`
- 현재 정책이 내보낸 `trigger_emitted_count`

Task 5의 레거시 selector adapter는 최종 3개만 가져와 “전체 후보”라고 부르면 안 된다. 최소한 trigger가 내보낸 후보 전체를 core 후보로 전달해야 한다. trigger별 10개 정책을 확대·제거할지는 실제 `criterion_match_count` 분포와 품질을 본 뒤 별도 전략 변경으로 결정한다. 단, 10개 뒤의 종목이 존재했다는 사실은 반드시 보고한다.

---

## 5. 실데이터 계측 계획

### 5.1 첫 UAT에서 즉시 보여줄 값

첫 실제 한국장 read-only UAT는 아래를 리포트·stdout·DB readback에 동일하게 표시한다.

```text
시장 universe: N
trigger 입력 / pre-criterion window / 사전 제외: trigger별 N / N / N
trigger 조건 통과: trigger별 N
trigger 출력: trigger별 N
core raw / core unique: N / N
supplemental raw / supplemental unique: N / N
core-supplemental overlap: N
total unique: N
decision-eligible / report-only / unavailable: N / N / N
SWING+TREND evaluation planned / completed / failed / pending: N / N / N / N
pending candidates / analysis-incomplete candidates: N / N
truncated: 0
```

모든 수에는 다음 식별자가 붙어야 한다.

- `job_id`
- 한국장 거래 세션과 timezone-aware `as_of`
- source snapshot ID와 hash
- candidate reconciliation version
- selector/trigger version
- strategy versions
- 실제 호출 시작·종료시각

### 5.2 기준 분포 수집

먼저 최소 20개 완료 거래 세션을 같은 계약으로 계측해 **잠정 범위**를 확인한다. 이 표본의 p95와 최댓값은 불안정하므로 hard cap 근거로 사용하지 않는다. hard cap 검토 전에는 최소 60개 완료 거래 세션을 모으고, 역발상 trigger 활성 국면과 비활성 국면을 각각 10회 이상 포함한다. 구현·provider 권한 때문에 모든 과거 세션을 정직하게 재현할 수 없으면 가짜 값을 채우지 않고 `NOT_REPLAYABLE`로 기록한다.

수집 항목:

- 일별 p50, p90, p95, 최댓값
- trigger별 조건 통과·출력 수
- trigger 간 중복률
- core·supplemental 중복률
- 상태별 후보 수
- 후보 1개당 provider 호출·latency
- SWING/TREND별 LLM latency·실패·재시도
- 전체 wall-clock 시간
- 중단 후 재개 성공률
- report/DB count 동일성

분포 수집 중 trigger 임계값이나 상한을 임의로 바꾸지 않는다. 정책 버전이 바뀌면 이전 분포와 섞지 않고 새 cohort로 시작한다. 특정 국면이나 40/50 trigger 구성이 관측되지 않은 cohort는 전체 용량 분포라고 부르지 않는다.

### 5.3 실제 상한 제안이 필요한 경우

다음 중 하나가 실제 증거로 반복되면 별도 변경안에서 hard cap 필요성을 검토할 수 있다.

- provider 약관·quota상 전체 후보 처리가 불가능함
- durable resume를 사용해도 다음 거래 세션 전 완료할 수 없음
- 개인 Mac의 저장·메모리·실행시간이 운영 목표를 반복적으로 넘음
- 후보 증가가 품질 향상 없이 대부분 동일·저품질 trigger noise임이 시간 분리 검증으로 확인됨

그 경우에도 먼저 검토할 순서는 다음과 같다.

```text
중복 제거 개선
→ 공통 market snapshot 재사용
→ provider 호출 cache/idempotency
→ bounded concurrency 조정
→ batch resume
→ 값싼 결정론적 데이터 품질·eligibility gate
→ 마지막에만 명시적 후보 cap 제안
```

hard cap을 제안할 때는 반드시 포함한다.

- 어느 단계의 cap인지
- 왜 strategy filter가 아니라 capacity cap인지
- 실제 p50/p90/p95/최댓값
- cap 때문에 누락될 일수와 후보 수
- 누락 후보의 성과·편향 분석
- 보고서의 `truncated_candidate_count`와 누락 사유
- 사용자 승인과 rollback 방법

승인되지 않은 hard cap을 기본값으로 추가하지 않는다.

---

## 6. 저장·보고 계약

### 6.1 불변식

```text
core_unique_count
+ supplemental_unique_count
- cross_channel_overlap_count
= total_unique_candidate_count
```

```text
total_unique_candidate_count
= decision_eligible_candidate_count
+ report_only_candidate_count
+ data_unavailable_candidate_count
```

```text
analysis_candidate_count
= decision_eligible_candidate_count
+ report_only_candidate_count
```

```text
strategy_evaluation_planned_count
= analysis_candidate_count × 2
```

```text
strategy_evaluation_planned_count
= strategy_evaluation_completed_count
+ strategy_evaluation_failed_count
+ resumable_pending_evaluation_count
```

위 evaluation 불변식은 `truncated_candidate_count = 0`일 때 적용한다. `resumable_pending_candidate_count`는 사용자에게 대기 종목 수를 보여주는 별도 파생 지표이며 evaluation 단위 식에 더하지 않는다.

완료 시점에는 리포트, dashboard export, `research.sqlite`, `ops.sqlite` readback의 count와 identity 집합이 같아야 한다. 불일치하면 발행 가능한 정상 실행이 아니다.

### 6.2 사용자 보고 순서

최종 한국장 리포트 상단에 다음을 짧게 표시한다.

1. 오늘 조건을 통과한 수
2. trigger 정책 뒤 후보 수
3. 중복 제거 후 core·supplemental·합산 수
4. 분석 완료·부분·실패·대기 수
5. 숨은 truncation 여부
6. 전 실행 대비 후보 수 변화

후보가 0개인 경우에도 다음을 구분한다.

- 조건을 만족한 종목이 실제로 0개
- 원천 unavailable로 후보를 만들 수 없음
- selector 오류로 결과가 없음

---

## 7. 구현 작업 연결

이 문서는 상위 계획의 다음 작업에 적용한다.

- **Task 2 — reconciliation:** 모든 funnel count와 `truncated_candidate_count=0` 불변식
- **Task 4 — candidate batch:** bounded concurrency, queue, retry, resume, 상태별 count
- **Task 5 — KR selector adapter:** 최종 3개와 trigger 출력 전체를 혼동하지 않음; pre/post-limit count 노출
- **Task 7 — supplemental leadership:** raw·unique·cross-channel overlap count
- **Task 8 — daily CLI:** 실제 count summary와 nonzero failure semantics
- **Task 9 — report/dashboard:** DB와 동일한 count 및 identity 집합 표시
- **Task 10 — history:** 날짜·selector version별 후보 수 분포와 변화 저장

Task 5는 실데이터에서 최소 다음을 증명하기 전 merge하지 않는다.

- trigger별 조건 통과 수
- trigger별 입력·pre-criterion window·사전 제외 수
- trigger별 출력 수
- core raw·unique 수
- 레거시 최종 3개와의 차이
- source/as-of/call evidence
- 어떤 후보도 분석 용량 때문에 잘리지 않았음

---

## 8. 결론

현재 코드만 보면 한국장 core 후보는 trigger 출력 기준 하루 **최대 30·40·50 raw 행**, 레거시 최종 결과는 **최대 3종목**이다. 그러나 실제 고유 후보 분포와 보강 후보 수는 아직 실측되지 않았다.

따라서 지금 확정할 정책은 다음과 같다.

> **reconciliation·분석 용량 때문에 새로운 후보 상한을 두지 않는다. 기존 trigger의 10개 출력 제한과 매크로/역발상의 100/50 사전 window는 제거된 것이 아니라 노출·계측할 레거시 발굴 정책 제한이다. 모든 단계의 전·후 count를 계측하고, reconciliation을 통과한 고유 후보는 SWING/TREND queue에서 버리지 않으며, 동시성·rate limit·재시도·재개만 제한한다. 20개 세션은 잠정 범위 확인용이고, 국면을 포함한 최소 60개 완료 거래 세션의 분포가 쌓인 뒤에만 별도 근거와 사용자 승인으로 hard cap을 재검토한다.**
