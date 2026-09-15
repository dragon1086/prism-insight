# Trading Change Review Harness

Use this checklist before changing screening, regime, entry, exit, or position-sizing behavior.
It is a development review harness, not a runtime trading component.

For implementation and deployment, also apply the mandatory change-scope
verification gates in `docs/SERVER_GIT_OPERATIONS_ko.md`. A strategy review or
passing unit tests does not replace real-module integration and safe production
smoke. Report the first scheduled run separately from deployment verification.

## BTC roadmap checkpoint

For BTC work, first read [BTC_ROADMAP_ko.md](BTC_ROADMAP_ko.md).
Record the milestone/task ID, dependencies, acceptance evidence, and rollback scope.
Prioritize unresolved execution/protection defects over parameter optimization.
Do not treat more timeframes, a passing test suite, or a higher backtest return as
proof of live safety or profitability. Use the linked profitability contract.
After work, update the roadmap only where evidence changed; explicitly separate
code/test/deployment/forward-validation states and identify the next task.
Memory stores the pointer and principles, not a competing copy of roadmap status.
This checkpoint adds no runtime process, network query, or LLM call to trading.

## Strategy adoption and fit gate

### 언제 적용하는가

새 전략 접목·전략 혼합/교체·새 트리거·팩터·국면 전환·청산/사이징 규칙 또는 그에
해당하는 매수/매도 프롬프트 변경을 제안하면, 이름이나 명령어와 무관하게 이 절차를
먼저 적용한다. KR·US·BTC에 공통이며 개발 시 검토 절차다. 거래마다 LLM을 호출하거나
새 분류기·모니터링 cron·자동 전략 전환을 설치하는 지시가 아니다.

출발점은 사용자가 2026-09-15 제공한 책 사진의 세 가지 질문이다. 아래는 원문 인용이
아닌 프로젝트 적용 요약이며, 사진만으로 책 제목·저자를 확인한 것으로 취급하지 않는다.

### 1. 잠깐 통하는 방법을 영구 규칙으로 바꾸는 것은 아닌가?

- 무엇이 수익 기회를 만들고, 어떤 시장/기간에서 유효하며, 무엇이 나오면 가설이 틀렸는지
  먼저 쓴다. 최근 한 종목의 손절·급등·짧은 연승만으로 전체 전략을 바꾸지 않는다.
- 일시적 시장 변화, 반복되는 국면 의존성, 지속적인 구조 변화라는 설명을 구분한다.
  유효 기간을 사후 수익률에 맞춰 고르거나 부진 구간을 빼지 않는다.
- 같은 데이터 시점·비용·체결 가정으로 기존 전략과 비교하고, 시간 순서 검증과 별도
  holdout을 둔다. 다른 트리거/국면의 반례와 최고 수익 사례 제거도 확인한다.

### 2. 기존 전략과 실제로 함께 운용할 수 있는가?

- 기존 전략의 핵심 가정·보유 기간·진입/청산·위험 한도를 먼저 기록한다. 신규 규칙이
  이를 보완하는지, 대체하는지, 충돌하는지 설명한다. 트리거(발견 이유)와 그 시점의
  진입 형태를 혼동하거나, 같은 근거를 여러 단계에서 중복 가점/차단하지 않는다.
- 예를 들어 추세 추종에 가까운 저항선 즉시 익절을 결합하거나, 변동성에 맞춰 손절을
  넓히면서 기존 수량을 유지하면 원래의 수익/위험 구조를 바꿀 수 있다. 이름만 다른
  지표를 추가하는 것으로 그 충돌이 해결되지는 않는다.
- 사용자가 감당할 수 있는 손실·낙폭·보유 기간·매매 빈도와 수동 개입 부담을 고려한다.
  자동화 시스템에는 같은 기준을 위험 예산·포지션 한도·관측/운영 비용으로 명시한다.
  중요 제약이 확인되지 않았으면 기존 한도를 보존하고 더 큰 위험을 임의 승인하지 않는다.
- 단순 추가안뿐 아니라 기존 유지, 문제 부분만 수정, 대체안, 별도 실험 레인도 비교한다.
  무조건 전략을 섞거나 늘리는 것을 개선의 기본값으로 삼지 않는다.

#### 필수: 종목 스크리닝과 매매 에이전트의 실제 연결 궁합

개념적으로 “우리 전략과 잘 맞는다”는 설명만으로 통과시키지 않는다. **실제 코드와
프롬프트를 함께 읽고, 스크리닝 → 보고서 입력 → BUY 판단 → 결정론적 게이트 →
주문·사이징 → SELL·보유 관리**까지 같은 후보가 어떻게 처리되는지 추적한다.
프롬프트만 읽거나 스크리닝 함수 하나만 시험한 뒤 전체 궁합을 확인했다고 하지 않는다.

- **실제 경로 확인:** KR `trigger_batch.py`, US `prism-us/us_trigger_batch.py`에서
  대상 트리거·랭킹·후보 cap·보충·fallback을 확인한다. `cores/agents/trading_agents.py`와
  `prism-us/cores/agents/trading_agents.py`, 양국 tracking agent, 공통 buy gate·regime·
  주문 예산·손절/추세 청산 경로까지 이어서 본다.
  기본 트리거뿐 아니라 매크로/역발상·파일럿·보유 종목·복구 경로 등 적용되는 분기도 확인한다.
  BTC는 없는 주식 스크리닝을 가정하지 말고 해당 신호 생성→실제 실행/보유 관리 경로를 따른다.
- **같은 매매를 가정하는가:** 진입 형태·보유 기간·시장 국면·확정봉/장중값·가격 기준시각·
  목표가·지지/저항·ATR·손절 floor·위험 예산·익절/추세 추종의 의미를 비교한다.
  예를 들어 스크리닝은 돌파 후보를 선호하면서 BUY는 가까운 고점만 목표로 삼고 SELL은
  장기 추세를 보유하는 조합이라면, 의도한 역할 분담인지 경제적 모순인지 근거로 설명한다.
- **데이터와 권한이 이어지는가:** 필드명/단위/자료 기준일, 전달 누락, 컬럼 대소문자,
  score/RR의 출처, fallback과 최종 override 순서를 확인한다. 동일 팩트를 공유하거나
  같은 계산을 재사용할 수 있는지 검토한다. 기관 보유량을 당일 순매수로 읽거나,
  없는 목표·손절·손익비를 가공 값으로 채우지 않는다. MISSING은 미확정으로 보존한다.
- **중복 보상·제재가 없는가:** 같은 근거가 랭킹·LLM 점수·하드 게이트에서 반복 반영되는지,
  손절폭 확대가 주문 수량/총위험에 반영되는지 확인한다. 단계마다 역할과 최종 판단 권한을
  명시하며, 한 단계 수정이 다른 단계의 점수 척도·순서·임계값을 바꾸는지도 검증한다.
- **같은 후보로 양방향 검증:** 유지돼야 할 정상 후보와 거절돼야 할 반례를 고정해 실제
  전달 경로를 시험한다. 스크리닝에서 이미 알 수 있었던 이유로 비싼 분석 뒤 탈락하는 낭비와,
  뒤 단계에서만 확보하는 사업/이벤트 근거로 합리적으로 탈락하는 경우를 구분한다.
  판단 시점의 입력이 없으면 사후 복원으로 일치했다고 주장하지 않는다.
- **결론을 기록:** 각 단계의 코드 위치·정책 버전·조건/입출력·최종 권한, 의도한 차이,
  발견한 모순/결측, 수정 위치와 양방향 검증 결과를 기존 계획에 남긴다. 확인하지 못한 경로는
  미확인으로 표시한다. 이 비교를 통과하지 못하면 “궁합 검토 완료”로 표시하지 않는다.

목표는 **같은 근거와 매매 전제를 사용하되 각 단계의 역할을 유지하는 것**이다.
스크리닝과 BUY가 항상 같은 결론을 내리게 하거나, 모든 검사를 앞 단계에 복제하거나,
그 차이를 메우려고 스크리닝 LLM을 자동 추가하라는 뜻이 아니다. 전 경로를 검토하되
수정은 필요한 계층에 한정하고, 다른 계층의 보존/상호작용을 검증한다.

### 3. 몇 달 뒤 성과가 나빠지면 원인을 구분할 수 있는가?

- **새 규칙의 영향 / 동시 발생한 시장 국면 변화 / 시장의 구조 변화 / 데이터·집행 오류**를
  별도 가설로 둔다. “최근 안 먹힌다”는 이유만으로 어느 하나를 원인으로 확정하지 않는다.
- 변경 전 기준선·버전·판단 당시 입력·변경 위치를 보존하고, 가능한 경우 같은 후보에
  기존/신규 판단을 주문 없이 병행 비교한다. 실험 없이 관측된 차이를 인과 효과로 부르지 않는다.
- 한 번에 한 의사결정 계층을 바꾸고, 시도한 후보/임계값 수를 기록한다. 표본·결측·실행
  증거가 부족하면 불충분으로 남기며, 지표·프롬프트·손절을 동시에 바꿔 원인을 숨기지 않는다.
- 시장이 원래 상태로 돌아왔을 때의 **기존 전략 복귀 조건**, 신규 규칙의 중단/유지 조건,
  최소 관측 기간과 허용 위험을 **결과를 보기 전에** 정한다. 기준이 충족돼도 운영 전환은
  기존 승인 절차를 따른다. 최근 손익에 따라 매번 왕복하는 규칙 변경을 자동화하지 않는다.

### 구현 전 남길 최소 검토 기록

기존 계획/실험 문서에 다음 여섯 항목을 간결하게 남긴다. 별도 상주 시스템은 만들지 않는다.

1. 변경 유형: 순수 오류 수정 / 경제적 의사결정 변경 / 둘 다. 범위와 기준선 버전.
2. 신규 가설의 작동 이유·유효 국면·반증 조건.
3. 기존 전략과의 보완/충돌, 사용자 위험 제약과 운영 부담. 위 스크리닝↔매매 에이전트
   단계별 비교와 근거 코드 위치·의도한 차이·모순·미확인 항목을 포함한다.
4. 동일 조건 비교·반례·시점성·holdout·필요 표본 및 비용/집행 가정.
5. 부진 원인 구분 방법, 유지·중단·복귀 조건과 관측 종료 시점.
6. 현재 결론과 근거: 기존 유지/보류/사전등록 비교/SHADOW 검토/LIVE 검토 중 무엇인지.
   새 SHADOW/LIVE 활성화는 기존 하네스와 사용자 승인 조건을 우회하지 않는다.

**오류 수정과의 경계:** 전송 수량과 원장 불일치, API 열 구조, 잘못된 경보 채널처럼
입증된 구현 결함은 해당 회귀와 안전 검증으로 바로 고친다. 그 결함을 그대로 유지하는
것을 “기존 전략 보존”이라고 부르지 않는다. 반대로 손절·비중·선발 기준을 실질적으로
바꾸면서 단순 버그 수정이라는 이름으로 이 검토를 건너뛰지도 않는다.

## Review lenses

- **William O'Neil / CAN SLIM**: market direction, leadership, accumulation versus
  distribution, proper entry, and cutting losses without truncating winners.
- **Mark Minervini**: trend template, volatility contraction, extension and
  climax risk, and precise entry quality.
- **Stanley Druckenmiller**: regime, liquidity, macro inflection, and whether the
  rule fits the current market rather than only a long-term trend label.
- **Warren Buffett**: business quality, durability, valuation, and the risk of a
  price-only signal selecting a weak underlying business.
- **Quant risk manager**: sample size, expectancy, profit factor, drawdown,
  parameter sensitivity, multiple testing, and out-of-sample stability.
- **Systems reviewer**: operational simplicity, deterministic behavior,
  observability, rollback, and failure modes across KR and US pipelines.

The lenses challenge a proposal; persona consensus does not replace production
data or a falsifiable test.

## Required sequence

1. **State the contract and scope.** Name the market, trigger, regime, and
   behavior the rule is supposed to identify. For strategy adoption or economic
   rule changes, complete the six-item adoption/fit record above first.
2. **Reproduce with production evidence.** Join the decision with its market,
   candidate, entry, exit, and outcome context. Separate code versions where
   possible.
3. **Try to falsify the proposal.** Check the same pattern in other triggers,
   regimes, and profitable counterexamples before calling it universally bad.
4. **Compare smaller alternatives.** Consider no change, logging only,
   screening-only, deterministic gate, and prompt guidance in that order.
5. **Change one decision layer.** Do not encode the same penalty in screening,
   a buy gate, and an LLM prompt unless each layer has a distinct measured job.
6. **Protect both sides with tests.** Add one rejected counterexample and one
   valid retained example. Verify unrelated triggers remain unchanged.
7. **Make the effect observable and reversible.** Log a stable reason code,
   preserve enough identifiers for later outcome analysis, and keep rollback
   to one small code change or feature flag.

## Gotcha: local evidence is not a global gate

A pattern that is harmful inside one trend-following trigger can be profitable
inside reversal, breakout, or capital-inflow triggers. Never promote a single
trade, candle shape, or trigger-local result into a global hard gate or prompt
penalty until cross-trigger and cross-regime counterexamples have been tested.

Prefer a narrow trigger-contract repair when it explains the failure. Expand
the rule only after a larger, version-aware sample shows the same effect.
