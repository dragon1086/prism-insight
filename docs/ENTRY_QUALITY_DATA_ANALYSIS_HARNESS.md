# PRISM 진입품질 데이터 분석 하네스

> 상태: **v1 canonical**  
> 분석 계약: `entry-quality-harness-v1`  
> 적용 대상: PRISM ClickStack 관측 원장에서 파생한 진입품질 분석  
> 실행 도구: `tools/build_entry_quality_evidence_packet.py`

이 문서는 세션과 분석자가 바뀌어도 같은 데이터에서 같은 범위의 결론을 내리도록
진실 원장, 연결 규칙, 표본 기준, 가설 실험과 승격 절차를 고정합니다.
`docs/ENTRY_QUALITY_EVOLUTION_ko.md`와 충돌하면 더 보수적인 규칙을 적용합니다.

## 1. 절대 원칙

1. ClickStack의 versioned event ledger가 관측 사실의 원장입니다.
2. Evidence Packet은 원장을 재현 가능하게 요약한 **파생물**이며 원장을 수정하지 않습니다.
3. `MISSING`은 나쁜 진입이나 통과를 뜻하지 않습니다. 단지 알 수 없다는 뜻입니다.
4. 주문 제출과 실제 체결을 분리합니다. `CONFIRMED` 이외의 주문은 실현 성과에서
   제외합니다.
5. 후보 성과와 실제 거래 성과를 합치지 않습니다.
6. backfill은 탐색과 반례 발견에만 사용하며 prospective holdout으로 인정하지 않습니다.
7. 분석 도구와 스킬은 거래 코드, 점수, 주문, 손절 규칙을 변경하지 않습니다.
8. 어떤 규칙도 자동으로 SHADOW 또는 LIVE로 승격하지 않습니다. LIVE에는 사용자의
   명시적 승인이 반드시 필요합니다.

## 2. 진실 원장과 데이터 계보

### 2.1 원장 이벤트

- `candidate.evaluated`: 진입 판단 시점의 시장·종목·정책·진입품질 context
- `candidate.outcome`: 후보를 계속 관찰했을 때의 1·3·5·7·14·30 거래일 결과
  (현재 실제 제공 범위는 7·14·30일이며 나머지는 없으면 `null`)
- `entry.executed`: simulator 기록 또는 진입 실행 context
- `entry.fill_reconciled`: broker 체결 provenance
- `exit.executed`: 연결된 포지션의 청산 context
- `trade.outcome`: 검증된 실현 성과

분석에 필요한 값은 이벤트에 기록된 판단 당시 snapshot을 사용합니다. 현재 DB나 최신
뉴스를 다시 조회해 과거 후보의 빈 필드를 채우지 않습니다.

### 2.2 필수 계보 필드

- 사건: `event_id`, `event_type`, `timestamp`, `schema_version`
- 연결: `decision_id`, `position_id`, `trace_id`
- 실행 버전: `git_sha`, `policy_version`, `config_hash`
- 분할 축: `market`, `trigger_type`, `regime`
- 진입품질: `entry_quality_context.context_schema_version`, `extractor_version`,
  `as_of`, `input_hash`, component status

계보 필드가 없으면 추정값으로 복원하지 않습니다. Packet의 coverage와
`insufficiency_reasons`에 그대로 남깁니다.

### 2.3 원본과 파생물

- 원본 JSONL은 이미 관측 계층에서 민감정보가 제거된 파일만 입력으로 허용합니다.
- Packet은 허용 목록 필드만 출력합니다. 원문 prompt, 기사 전문, account, broker order
  ID, token, cookie, authorization, secret payload는 복사하지 않습니다.
- Packet의 `packet_schema_version`, `analysis_contract_version`, `packet_id`를 보고서에
  기록합니다. 도구나 하네스 버전이 달라지면 같은 trial로 합치지 않습니다.

## 3. 결정론적 Evidence Packet

직접 SQL이나 임의 Python notebook으로 지표를 먼저 만들지 않습니다. 다음 명령으로
Packet을 생성한 뒤 이 Packet만 분석합니다.

```bash
.venv/bin/python tools/build_entry_quality_evidence_packet.py \
  --input logs/prism_events.jsonl \
  --output .omx/evidence/entry-quality-evidence.json \
  --market US
```

이미 운영에서 안전하게 내보낸 JSONL이 있으면 `--input`을 반복해 지정할 수 있습니다.
도구는 네트워크와 DB에 접근하지 않습니다.

결정론 계약:

- `event_id`가 같은 이벤트는 하나만 남기고, timestamp와 canonical JSON 순서로 최신
  항목을 선택합니다.
- 같은 `decision_id`의 후보가 여러 개인 경우에도 하나만 남깁니다.
- 입력 순서가 달라도 `packet_id`가 같아야 합니다.
- `as_of`는 실행 시각이 아니라 입력 이벤트 중 마지막 시각입니다.
- Packet은 원시 attributes를 복사하지 않고 분석용 허용 필드만 출력합니다.

## 4. Prospective cohort와 legacy 분리

기본 시작점은 `entry_quality_context`가 들어 있는 첫 번째 **live**
`candidate.evaluated` 시각입니다.

- 그 시각 이전 후보는 `legacy_excluded_count`로만 보고 분석 표본에서 제외합니다.
- 그 시각 이후 context 없는 후보도 coverage의 분모에 포함합니다.
- `ingestion_mode=backfill` 후보는 prospective 표본에 포함하지 않습니다.
- 운영 배포 시각을 별도로 확정해야 하면 `--prospective-start`에 ISO-8601 시각을
  명시하고 보고서에도 그 이유를 기록합니다.
- 시작점을 결과를 본 뒤 앞뒤로 옮기는 행위는 금지합니다.

## 5. 허용되는 join

| 연결 | 키 | 허용 조건 |
|---|---|---|
| 후보 → 후보 결과 | 정확히 같은 `decision_id` | 결과 이벤트 시각이 판단 시각 이후 |
| 후보 → 진입 | 정확히 같은 `decision_id` | 진입 이벤트 시각이 판단 시각 이후 |
| 진입 → 체결 | 같은 `position_id`, 없을 때 같은 `decision_id` | 체결 이벤트 시각이 진입 이후 |
| 진입 → 실제 결과 | 정확히 같은 `position_id` | 결과 시각이 진입 이후이고 fill이 `CONFIRMED` |

금지되는 join:

- ticker와 날짜가 비슷하다는 이유로 연결
- 회사명, 가격, 주문 수량으로 연결
- `trace_id`만 같은 서로 다른 decision을 합침
- 후보 성과를 실제 체결 성과로 대체
- 연결되지 않은 결과를 손으로 특정 거래에 배정

Packet은 내부 ID 원문 대신 hash reference를 출력하지만 join 자체는 원장의 정확한 ID로
수행합니다.

## 6. Coverage와 MISSING 판정

분석자는 성과보다 먼저 다음을 보고합니다.

1. prospective decision date 수와 후보 수
2. capture rate와 `decision_id` coverage
3. 진입·후보 결과 연결률
4. component별 `OK|MISSING|ERROR` 분포
5. 진입 위치·체크리스트 필드별 non-null 비율
6. fill provenance 분포와 confirmed coverage
7. anti-leakage 제외 건수

`MISSING` 처리:

- `MISSING`을 0점, 실패, `False`, 정상으로 변환하지 않습니다.
- missing 여부 자체를 설명 변수로 시험할 수는 있지만, 사전에 별도 가설로 등록합니다.
- 표본마다 필드 coverage가 다르면 complete-case와 missing cohort를 따로 보고합니다.
- `ERROR`는 시스템 품질 문제로 분리하고 종목 품질로 해석하지 않습니다.
- component가 새로 추가된 전후의 표본을 동일 분포로 가정하지 않습니다.

## 7. Fill과 실제 성과의 기준

- `CONFIRMED`: 실제 거래 성과 표본에 포함 가능
- `PARTIAL`: 초기 v1 실제 성과 표본에서 제외
- `SUBMITTED_ONLY`: broker가 주문을 받았을 뿐이므로 제외
- `REJECTED`, `CANCELLED`, `UNKNOWN`: 제외

simulator holding, 주문 번호, 성공 응답만으로 `CONFIRMED`를 추정하지 않습니다.
confirmed fill coverage가 95% 미만이면 실제 거래 PF·승률을 승격 근거로 사용하지
않습니다. 후보 결과 분석은 계속할 수 있지만 명확히 별도 표본으로 표시합니다.

## 8. 최소 표본과 통계 해석

CAPTURE 완료 판단의 최소값:

- prospective decision date `n >= 20`
- candidate `n >= 100`
- linked actual entry `n >= 30`
- `decision_id` coverage 100%
- confirmed fill coverage 95% 이상
- 미래정보 누수 0

규칙 비교에 사용하는 matured outcome은 최소 30건입니다. 이보다 작으면 모든 수치는
**기술 통계**이고 차단 규칙이나 승격 근거가 아닙니다.

항상 함께 보고할 값:

- 표본 수, 중앙 수익률, 상승 비율 또는 승률, Profit Factor
- stop/risk-exit 비율
- trigger × regime × policy version cohort
- 최고 수익 한 건 제거 전후 방향
- 규칙이 제거했을 winner와 통과시켰을 loser 반례

평균만 단독으로 보고하거나 서로 다른 trigger·regime·policy version을 이유 없이
합치지 않습니다.

## 9. 미래정보 누수 방지

다음 중 하나라도 있으면 해당 row를 분석에서 제외하고 전체 분석을 승격 불가로
표시합니다.

- `entry_quality_context.as_of > candidate.timestamp`
- 후보 결과 시각이 판단 시각보다 빠름
- 진입 시각이 판단 시각보다 빠름
- 체결 시각이 진입 시각보다 빠름
- 실제 결과 시각이 진입 시각보다 빠름
- 판단 뒤 생성된 최신 지표로 과거 snapshot을 덮어씀
- 현재 trigger 성과를 과거 판단 당시 prior처럼 사용

결과 컬럼으로 threshold를 선택한 뒤 같은 표본의 성과를 검증 결과라고 부르지
않습니다.

## 10. 다중 가설과 과적합 방지

여러 trigger와 threshold를 실험할 수 있지만 다음 규칙을 지킵니다.

1. 한 trial에는 primary metric 하나와 secondary metric 최대 두 개만 둡니다.
2. trigger, threshold, regime, feature 조합을 한 번이라도 확인했으면 시도 횟수에
   포함합니다. 실패한 조합도 삭제하지 않습니다.
3. p-value를 사용하면 같은 family의 모든 시도를 포함해 Holm 방식으로 보정한 값을
   함께 보고합니다. 유의확률만으로 승격하지 않습니다.
4. 효과 크기, 표본 수, winner removal, 비용·슬리피지 민감도를 함께 봅니다.
5. threshold나 feature를 바꾸면 새 `profile_version`과 새 trial입니다.
6. 결과를 본 뒤 cohort 정의, 시작 시각, primary metric을 바꾸지 않습니다.

## 11. Holdout 계약

- 탐색 표본과 holdout을 시간 순서로 분리합니다. 무작위 셔플은 사용하지 않습니다.
- 후보 규칙을 고정한 시각 이후에 생성된 prospective 사건만 holdout입니다.
- historical/backfill, threshold 선택에 사용한 기간, SHADOW 시작 전 기간은 holdout이
  아닙니다.
- 규칙, prompt, model, classifier, threshold, feature 정의가 바뀌면 holdout을 다시
  시작합니다.
- 여러 후보 중 가장 좋아 보이는 하나를 고른 경우, 선택 과정 전체를 trial에 기록하고
  새 데이터에서 다시 검증합니다.

## 12. Trigger 실험 생명주기

### 12.1 Hypothesis

검증 가능한 한 문장으로 씁니다.

> 예: strong_bull에서 Volume Surge 후보 중 primary resistance가 3% 이내인 진입을
> 제외하면, 30일 중앙 수익률을 높이면서 winner removal을 10% 이하로 유지한다.

### 12.2 Preregister

결과를 보기 전에 다음을 고정한 문서를 `docs/entry-quality-experiments/` 아래에 둡니다.

- `experiment_id`, 작성 시각, 작성자
- 데이터 cut-off와 Packet ID
- 대상 market, trigger, regime, policy version
- feature, threshold, 결측 처리, rule profile version
- primary/secondary metric과 최소 효과
- 최소 표본, holdout 시작 조건, 종료 조건
- 반례와 winner removal 허용선
- 다중 가설 family와 지금까지의 시도 수
- 성공, 실패, 중단, rollback 판정

### 12.3 Offline replay

고정된 Packet에 규칙을 적용합니다. 탐색 결과에는 반드시 표본·분포·반례·최고 winner
제거 결과와 `insufficiency_reasons`를 붙입니다. 데이터가 부족하면 `CONTINUE_CAPTURE`,
개선이 재현되지 않으면 `RETIRE`입니다.

### 12.4 Rule SHADOW

offline replay를 통과한 **구체 규칙 하나**만 `would_block`과 reason code로 기록합니다.
실제 score, signal, 주문, 메시지는 바꾸지 않습니다. 시작 이후 prospective holdout만
평가합니다.

### 12.5 Limited LIVE

SHADOW가 `docs/ENTRY_QUALITY_EVOLUTION_ko.md` 기준을 통과하더라도 자동 전환하지
않습니다. `docs/TRADING_CHANGE_REVIEW_HARNESS.md` 검토, rollback, 단일 최종 gate,
제한된 계좌·시장·비중·기간, 사용자의 명시적 승인이 모두 필요합니다.

### 12.6 Promote 또는 Retire

- 기준을 충족하고 제한 LIVE에서도 효과가 유지되면 기존 trigger를 새 version으로
  교체하거나 병행합니다.
- 효과 소멸, winner removal 초과, 표본 편향, 데이터 계보 오류가 있으면 RETIRE 또는
  rollback합니다.
- 성공한 규칙도 영구 규칙이 아닙니다. policy version별 성과와 rollback 조건을 계속
  관측합니다.

## 13. 승격 금지선

다음 중 하나라도 해당하면 다음 단계로 넘어가지 않습니다.

- Packet의 `data_sufficient=false`
- unresolved leakage 또는 exact join 실패
- confirmed fill coverage 95% 미만인데 실제 성과를 근거로 사용
- 한 건의 극단값을 빼면 개선 방향이 뒤집힘
- winner removal 10% 초과
- trigger·regime·policy version 혼합이 결론을 좌우함
- preregistration 이후 규칙 변경
- holdout 오염
- 비밀정보 노출
- 사용자 승인 없음

도구와 스킬은 `automatic_shadow_forbidden=true`, `automatic_live_forbidden=true`를
항상 유지합니다.

## 14. 정형 분석 보고 순서

1. **Packet 신원:** Packet ID, 계약·schema version, market, as-of
2. **데이터 품질:** 중복, 누수, join, 비밀정보 검사
3. **Coverage:** prospective/legacy, component missing, fill 분포
4. **가능한 질문:** 현재 데이터로 답할 수 있는 범위
5. **기술 통계:** trigger × regime × policy cohort
6. **반례:** winner removal과 통과 loser
7. **불충분 사유:** reason code를 생략하지 않음
8. **판정:** `CONTINUE_CAPTURE | PREREGISTER_REPLAY | START_RULE_SHADOW_REVIEW |
   LIMITED_LIVE_REVIEW | RETIRE`
9. **다음 행동:** 가장 작은 검증 작업 하나

분석 결과는 인과관계가 아니라 관측 또는 실험 결과로 표현합니다. 사용자가 막연히
“매수품질 데이터를 분석해줘”라고 요청해도 이 순서를 생략하지 않습니다.
