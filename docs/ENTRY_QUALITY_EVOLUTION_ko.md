# 진입품질 관측에서 LIVE까지의 진화 로드맵

> 상태: **CAPTURE 구현 완료 — 다음 db-server 배포부터 수집**
> 최종 의사결정일: 2026-08-29
> 현재 구현 기준: `main`
> 다음 작업: db-server 배포 후 prospective coverage와 결측률 확인

## 1. 이 문서가 우선하는 결정

진입품질 개선은 별도의 데이터 파이프라인이나 독립된 SHADOW 시스템으로 만들지
않습니다. 이미 운영 중인 ClickStack 관측 원장을 확장합니다.

- **관측은 SHADOW가 아닙니다.** 현재 단계는 `CAPTURE`입니다.
- CAPTURE는 매수 판단, 점수, 주문, 메시지, 손절·청산 규칙을 바꾸지 않습니다.
- 구체적인 차단 규칙이 데이터로 선택되고 버전이 고정된 뒤에만 그 규칙을
  `SHADOW`로 실행합니다.
- SHADOW가 사전 등록된 기준을 통과하고 사용자가 승인한 뒤에만 `LIVE`를 검토합니다.
- 자동 LIVE 승격은 금지합니다.

이 결정은 `.omx/plans/prd-entry-quality-underwriting.md`의 초기 설계보다 우선합니다.
초기 설계에서 제안한 별도 snapshot table, 여섯 개의 `entry_quality.*` 이벤트,
`off|shadow|paper|live` 통합 모드는 현재 구현 대상이 아닙니다.

## 2. 기존 ClickStack에서 그대로 재사용할 것

현재 관측 원장은 다음 사실을 이미 수집합니다.

- `candidate.evaluated`: 시장 국면, 추세, 손익비, 점수, gate, portfolio context
- `candidate.outcome`: 관찰 후보의 7·14·30일 결과
- `entry.executed`, `exit.executed`: 진입·청산 context
- `trade.outcome`: 검증된 실제 거래 성과
- `trigger.performance_feedback`: trigger별 Candidate/Actual 성과
- `decision_id`, `position_id`, `trace_id`: 후보부터 청산까지의 연결
- `git_sha`, `policy_version`, `config_hash`: 코드·정책·설정 계보
- JSONL spool → OTLP shipper → ClickStack/ClickHouse → 5분 dashboard exporter

따라서 새 ClickHouse, 새 shipper, 새 dashboard 애플리케이션을 만들지 않습니다.

## 3. CAPTURE에서 새로 기록할 최소 정보

### 3.1 진입 시점 품질 context

기존 `candidate.evaluated.attributes` 아래에 versioned
`entry_quality_context`를 추가하는 것을 기본안으로 합니다.

- 일봉·주봉 setup 품질: base, pivot 거리, support/resistance, proper/faulty,
  quality score, confidence
- 이벤트 위험: catalyst 종류, 루머/미확정/확정/철회 상태, gap risk
- trigger 사전성과: 판단 당시 Actual/Candidate 표본 수, median, profit factor와
  해당 데이터 window
- 판단 시점성: `as_of`, source, model/prompt/classifier version, input hash
- 관측 완결성: `OK|MISSING|ERROR`를 명시하고 결측을 PASS로 바꾸지 않음

이미 존재하는 market, regime, trend, RR, score, gate 값은 복사하지 않고 기존 context를
참조합니다. 긴 보고서, 원본 prompt, 기사 전문은 ClickStack에 넣지 않습니다.

현재 CAPTURE v1은 이미 구조화된 US scenario의 key level, entry checklist,
momentum/confirmation count와 로컬 trigger 성과만 기록합니다. 별도 vision·웹 호출을
추가하지 않았기 때문에 일봉·주봉 base 판정과 구조화된 event risk는 현재
`MISSING`으로 기록합니다. `entry_quality_context.status`는 완결성 상태이며 하나라도
필수 component가 없으면 `MISSING`입니다. 실제로 context가 생성됐는지는 dashboard의
`captured_count`와 별도로 확인합니다.

### 3.2 실제 체결 provenance

현재 simulator holding 생성 또는 주문 제출을 broker-confirmed fill로 간주하지 않습니다.

- `SUBMITTED_ONLY`, `PARTIAL`, `CONFIRMED`, `REJECTED`, `CANCELLED`, `UNKNOWN`을 구분
- broker order ID는 안전한 내부 식별자로만 연결
- 실제 fill price·시각이 확인된 거래만 realized PF 학습 표본에 포함
- 체결 확인이 진입 이벤트보다 늦게 도착하면 기존 이벤트를 덮어쓰지 않고 연결된
  reconciliation event를 한 종류만 추가

### 3.3 빠른 진입 타이밍 결과

기존 7·14·30일 결과는 유지합니다. 진입 위치의 단기 품질을 보기 위해 1·3·5 거래일
결과만 추가합니다. 기존 이벤트의 의미와 idempotency를 안전하게 확장할 수 없을 때만
checkpoint event를 한 종류 추가합니다. 10·20일 결과는 기존 7·14·30일과 중복되므로
초기 CAPTURE 범위에서 제외합니다.

현재 구현은 1·3·5 거래일 가격을 추가로 가져오지 않습니다. 기존 tracker를 거래일
기준으로 안전하게 확장하는 작업은 후속 CAPTURE 작업으로 남아 있습니다.

## 4. 단계별 진행 계약

### 단계 A — CAPTURE

목표는 판단 당시 사실과 이후 결과를 정확히 연결하는 것입니다.

- 단일 kill switch: `ENTRY_QUALITY_CAPTURE_ENABLED`
- fail-open 로컬 append만 허용
- 매수 score, BUY signal, OrderIntent, 주문 수, 메시지 변화 0
- 별도 `ENTRY_QUALITY_MODE`, PAPER, LIVE 분기 없음
- 별도 snapshot table은 기본적으로 만들지 않음
- exporter와 기존 dashboard에 coverage·결측·분포만 추가

CAPTURE 완료 증거:

- 20 US 거래 세션 이상
- candidate coverage `n >= 100`
- actual entry `n >= 30`
- `decision_id` 연결 누락 0
- as-of 미래정보 누수 0
- 중복 snapshot 0
- broker-confirmed fill coverage 95% 이상
- 계좌·token·원문 payload 노출 0
- CAPTURE on/off 간 decision/order diff 0

표본이 부족하면 기간이 지나도 다음 단계로 넘어가지 않습니다.

### 단계 B — OFFLINE REPLAY

CAPTURE 결과로 여러 가설을 비교하되 거래 경로에서는 실행하지 않습니다.

- historical/backfill은 규칙 탐색과 반례 발견에만 사용
- trigger, regime, archetype을 이유 없이 합치지 않음
- NOW·HOOD 같은 winner 반례의 제거 여부를 반드시 보고
- 한 개의 candidate rule과 threshold를 `profile_version`으로 고정
- threshold를 바꾸면 새 version과 새 trial로 취급

규칙이 재현 가능한 개선을 보이지 못하면 SHADOW를 만들지 않고 CAPTURE를 계속합니다.

### 단계 C — RULE SHADOW

`Entry Quality` 전체를 SHADOW로 만드는 것이 아니라, 단계 B에서 고정한 **한 개의
구체적인 차단 규칙**만 동일한 실시간 입력으로 계산합니다.

- `would_block`과 reason code만 기록
- 실제 score, signal, order, message는 계속 불변
- SHADOW 시작 이후의 prospective 표본만 승격 판단에 사용
- 규칙·model·prompt·threshold 변경 시 holdout을 다시 시작

LIVE 검토 최소 증거:

- 추가 20 US 거래 세션 이상
- prospective actual entry `n >= 30`
- matured outcome `n >= 30`
- confirmed fill coverage 95% 이상
- median, benchmark excess, PF 중 2개 이상 개선
- stop/risk-exit 비율 감소
- winner removal 10% 이하
- 극단값 한 건을 제거해도 개선 방향 유지
- 미해결 연결·누수·비밀정보 노출 0

### 단계 D — LIVE 검토

다음 조건을 모두 충족해야 합니다.

1. 단계 C의 증거 보고서가 저장되어 있음
2. `docs/TRADING_CHANGE_REVIEW_HARNESS.md` 검증 완료
3. 최종 gate 한 곳에만 `existing_entry_eligible AND entry_quality_allowed`로 연결
4. screening, prompt, journal에 같은 penalty를 중복 적용하지 않음
5. 즉시 OFF 가능한 rollback과 제한 배포 계획 존재
6. **사용자의 명시적 승인**

LIVE 이후에도 policy version별 성과를 분리하고 rollback 조건을 계속 평가합니다.

## 5. 다음 세션 인수인계 규칙

다음 세션은 “진입품질 작업을 계속하자”는 요청을 받으면 다음 순서로 진행합니다.

1. 이 문서와 `.omx/plans/prd-entry-quality-observability.md`를 먼저 읽습니다.
2. 코드와 운영 지표를 확인해 현재 단계가 어디까지 실제로 완료됐는지 증거로 판정합니다.
3. 완료되지 않은 현재 단계의 가장 작은 작업부터 수행합니다.
4. 단계가 끝날 때 이 문서 상단의 상태·다음 작업과 검증 수치를 갱신합니다.
5. CAPTURE 데이터를 SHADOW 성과라고 부르지 않습니다.
6. 구체적인 규칙이 고정되기 전에 SHADOW lifecycle을 만들지 않습니다.
7. 사용자 승인 없이 LIVE로 전환하지 않습니다.

## 6. 현재 인수인계 상태

- 완료: ClickStack 공통 관측 원장과 dashboard 운영
- 완료: 진입품질 저하 징후 및 PYPL fill provenance 문제 확인
- 완료: 관측과 SHADOW를 분리하는 설계 결정
- 완료: `candidate.evaluated.entry_quality_context` CAPTURE v1 구현
- 완료: `SUBMITTED_ONLY|REJECTED|UNKNOWN` 주문 provenance 연결 이벤트 구현
- 완료: 과거 후보를 제외한 prospective coverage·결측·fill dashboard 집계 구현
- 완료: CAPTURE OFF/ON의 broker call·signal·OrderIntent 불변성 테스트
- 미완료: 운영 db-server 배포 및 첫 실제 이벤트 확인
- 미완료: 구조화된 일봉·주봉 base 품질 입력
- 미완료: 구조화된 event risk 입력
- 미완료: broker-confirmed fill reconciliation
- 미완료: 1·3·5 거래일 outcome checkpoint
- 미완료: 운영 coverage 축적
- 미완료: offline replay로 candidate rule 선택
- 미완료: 구체 규칙 SHADOW
- 미완료: LIVE 검토
