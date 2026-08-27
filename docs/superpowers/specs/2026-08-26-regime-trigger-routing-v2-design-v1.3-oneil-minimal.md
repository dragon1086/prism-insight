# PRISM US 휩쏘 개선 v1.3 — O'Neil Minimal / Backtest First

## 0. 최종 결론

새 market regime 분류기를 만들지 않는다.

현재 유지할 시장 정보:

1. 기존 장기 `market_regime`: 장기 구조 설명과 기존 하드게이트 호환
2. 기존 O'Neil Market Pulse: `UPTREND / UNDER_PRESSURE / CORRECTION`, 노출·배치 정책
3. 신규 `trend_quality 0~5`: momentum 환경 설명용 연속 점수. 초기에는 SHADOW/advisory

개별 종목 승패 예측에는 market state를 사용하지 않는다. Trigger 개선과 손절·청산 검증을 별도 문제로 다룬다. 첫 구현은 신규 분류·trigger·서비스가 아니라 일회성 offline backtest다.

> O'Neil M은 알파 예측기가 아니라 시장 허가·노출 조절 장치다. Proper setup은 trigger contract가 검증하고, 손실은 deterministic risk rule이 집행한다.

### 0.1 운영 제약

- 포트폴리오는 10개 논리 슬롯이다.
- 0.5슬롯은 없다.
- 진입과 청산은 row/슬롯 단위 전량이다.
- 24시간 운영 pipeline에 검증 harness, 신규 daemon, 신규 cron을 추가하지 않는다.
- 검증은 운영 DB를 read-only로 읽는 수동 offline backtest다.
- backtest가 고른 한 변경만 기존 pipeline 코드에 반영한다.

---

## 1. 왜 v1.2도 줄였는가

### 1.1 Full-sample 분리는 좋아 보였다

US 30일 candidate outcome 821건, ticker/trigger 7일 중복 제거 후 591건에서:

- Closing Strength: trend +9.92%, range -4.21%
- Intraday Rise: trend +4.38%, range -0.18%
- Gap Up: trend -0.64%, range -4.74%

ADX/efficiency 9개 threshold grid에서도 방향이 유지됐다.

### 1.2 하지만 시간순 외부구간에서는 일반화하지 못했다

전반부 296건으로 cell 승률을 만들고 후반부 295건을 예측했다. 모든 모델은 같은 trigger 정보와 smoothing/backoff를 사용했다.

- trigger-only: Brier 0.2539, log loss 0.7015, AUC 0.529
- trigger + Market Pulse: Brier 0.2778, log loss 0.7514, AUC 0.437
- trigger + tactical 3-state: Brier 0.2767, log loss 0.7506, AUC 0.489
- trigger + trend-quality 3구간: Brier 0.2783, log loss 0.7536, AUC 0.481
- trigger + trend-quality binary: Brier 0.2729, log loss 0.7410, AUC 0.461

시장 상태를 더 붙일수록 외부구간 승패 예측이 나빠졌다.

판정:

- tactical state를 개별 종목 승패 예측과 자동 trigger circuit의 학습 key로 사용하지 않는다.
- full-sample conditional return은 진단 힌트로만 사용한다.
- state 기반 router 자동화는 보류한다.

### 1.3 시간 변화가 trigger 성과를 지배했다

시간순 반분 결과:

- Closing Strength `trend_quality>=3`: 전반 +9.78%, 후반 +4.26%
- Intraday Rise `trend_quality>=3`: 전반 +7.38%, 후반 +3.58%
- Gap Up `trend_quality>=3`: 전반 +11.98%, 후반 -3.45%

Gap Up은 시장 quality 하나로 설명되지 않고 최근 구간에서 구조적으로 악화됐다. 따라서 필요한 것은 더 세밀한 regime enum이 아니라:

- trigger 정의 개선
- trigger version별 최근 성과 변화 감시
- proper setup confirmation

이다.

---

## 2. O'Neil 하네스가 주는 제약

### 2.1 살아남은 O'Neil 요소

- M: Market Pulse와 distribution day/FTD
- 하락하는 50일선 아래 매수 금지(T1)
- 하락하는 MA20 아래 깊이 밀린 종목 금지(T2)
- 7~8% catastrophic loss discipline
- 상대강도와 거래량 확인
- 승자는 추세가 깨질 때까지 보유
- 승자 보존율을 1급 지표로 사용

### 2.2 일반화에 실패한 O'Neil 요소

저장소 하네스 결과:

- O'Neil 비전 base gate: 승자 통과 10.3%, 손절 차단 91.8%
- 승자와 손절을 약 90%씩 함께 차단
- 숫자 진입 품질 모델 AUC 0.524
- 정석 base rubric이 PRISM의 턴어라운드·뉴스·모멘텀 혼합 전략과 불일치

판정:

- O'Neil 정석 base를 모든 trigger의 universal hard gate로 사용하지 않는다.
- base/pivot은 그 가설을 전제로 하는 trigger contract 안에서만 사용한다.
- O'Neil 관점을 별도 LLM risk agent로 추가하지 않는다.

### 2.3 O'Neil 관점의 역할 분리

- Market Pulse: 노출과 분석 배치
- T1/T2: 명백한 하락추세 veto
- Trigger contract: proper setup 확인
- Risk kernel: stop/target/R-R 산술
- Sell discipline: hardstop, trend-exit, winner hold

이 다섯 층을 넘는 새로운 시장 분류는 증거가 생길 때까지 추가하지 않는다.

---

## 3. 최소 Market Context

새 state machine 없이 기존 로그를 확장한다.

```json
{
  "ts": "2026-08-25 23:15:05",
  "market": "US",
  "market_regime": "strong_bull",
  "market_regime_semantics": "long_term_structure",
  "market_pulse": "UPTREND",
  "trend_quality": 3,
  "trend_quality_components": {
    "above_ma20": false,
    "ma20_slope_up": true,
    "adx_ge_20": false,
    "efficiency_ge_020": true,
    "confirm_index_20d_positive": true
  },
  "data_cutoff": "..."
}
```

### 3.1 이름의 의미를 명확히 한다

사용자 메시지에서 `strong_bull`을 단독으로 “강한 강세장”이라고 번역하지 않는다.

예:

```text
장기 구조: 상승
O'Neil M: UPTREND
단기 추세 품질: 3/5 (혼조, 강한 추세 아님)
```

현재와 같은 장기 상승·스윙 박스를 정확히 설명할 수 있다.

### 3.2 `trend_quality`는 예측기가 아니다

구성 후보:

- index > MA20
- MA20 slope > 0
- ADX14 >= 20
- efficiency ratio >= 0.20
- 확인 지수의 20일 수익률 > 0

용도:

- prompt 경고
- trigger SHADOW 분석
- 운영 메시지
- later gate 연구

초기 비사용:

- 개별주 승패 확률
- 자동 매수 차단
- circuit breaker key
- sell trailing 폭 자동 변경

---

## 4. 기존 Market Pulse 유지

`cores/market_pulse.py`는 그대로 시장 M의 단일 상태 소스로 유지한다.

- UPTREND: 정상 분석
- UNDER_PRESSURE: top-down/momentum 감속
- CORRECTION: 기존 batch-rest/recovery 정책

Market Pulse 단독 candidate 관찰:

- UPTREND Intraday Rise 중앙값 +3.63%
- UPTREND Gap Up -0.68%
- UNDER_PRESSURE Gap Up -5.77%
- CORRECTION Closing Strength +3.15%

해석:

- Pulse는 시장 허가에 유효하지만 trigger별 proper setup을 대신하지 못한다.
- CORRECTION 전면 중단은 승자를 놓칠 수 있으므로 기존 제한 창구 정책을 유지한다.
- 새 tactical state로 Pulse를 대체하지 않는다.

---

## 5. Trigger 개선

### 5.1 Raw Gap Up

최근 성과 악화가 뚜렷하고 trend quality만으로 설명되지 않는다.

정책 후보:

- Raw Gap Up 유지
- Raw Gap Up 일시 제외
- 진입 확인을 추가한 GapAndHold

어느 것도 선결론으로 채택하지 않는다. offline backtest에서 Raw Gap Up 제외가 최근 성과를 개선하는지 먼저 확인한다. 제외가 큰 승자를 과도하게 제거하면 최근 5분봉으로 GapAndHold 가능성을 별도 비교한다.

GapAndHold를 검토하게 될 경우의 최소 contract:

- 명확한 catalyst
- opening range low 유지
- VWAP 또는 첫 range reclaim
- 과도한 MA20/ADR extension 아님
- 거래량 확인
- sector/confirm index가 급격히 반대 방향이 아님

O'Neil base/pivot은 이 contract에서만 선택적으로 확인한다. universal gate가 아니다.

### 5.2 Closing Strength

전반부·후반부에서 trend-quality 조건부 성과가 비교적 일관됐다.

초기 정책:

- 현행 유지
- `trend_quality < 3`이면 SHADOW 경고
- 실제 차단은 holdout 재검증 전까지 하지 않음
- close가 session range 상단/pivot 위에 안착했는지 contract로 검증

### 5.3 Intraday Rise

초기 정책:

- 현행 유지
- 개장 직후가 아니라 완료에 가까운 data를 우선
- `trend_quality < 3` 경고
- range 상단 추격 여부 확인

### 5.4 Volume Surge Top

quality 구간과 무관하게 중앙값이 대체로 음수였다.

초기 정책:

- 최근 trigger definition과 데이터 품질 감사
- 거래량 증가 자체가 아닌 price-location/close confirmation 추가
- SHADOW에서 version 분리

### 5.5 Volume Surge Sideways

낮은 market trend quality에서도 상대적으로 생존했다. Momentum trigger와 같은 문턱을 적용하지 않는다.

- 하락하는 MA 아래 drifting stock은 T1/T2로 차단
- base/support 근처 여부만 확인
- trend-quality gate 비적용

### 5.6 Macro Sector Leader

market quality보다 sector breadth와 개별주 위치가 중요하다.

- sector 동반 참여 수
- sector RS
- 개별주 RS와 extension
- 단일 종목 급등만으로 추가 확인 +1을 주지 않음

---

## 6. Circuit Breaker 보류

Regime 조건부 circuit breaker를 만들지 않는다. 첫 US 휩쏘 수정 전에는 자동 circuit breaker 자체를 구현하지 않는다.

초기 key:

```text
(market, trigger_id, trigger_version)
```

향후 필요할 때의 최소 상태:

```text
ACTIVE | SHADOW_PAUSED | PROBATION
```

### 6.1 판단 단위

- 5/7일 outcome 우선
- distinct market days
- distinct sectors
- 일별 cohort 중앙값
- 주 단위 block bootstrap
- 최근 창과 장기 baseline의 변화

### 6.2 현재 단계에서는 수동 판단

자동 차단하지 않는다. offline report가 최근 trigger 열화를 보여주면 운영자가 trigger를 유지/중단한다.

```text
PAUSE_RECOMMENDED
RECOVERY_CANDIDATE
INSUFFICIENT_EVIDENCE
```

만 출력한다.

Gap Up처럼 전반부 양수·후반부 음수로 바뀐 trigger는 최근 창 악화가 핵심이다. 전체기간 평균만으로 유지하거나 폐기하지 않는다.

---

## 7. Buy/Sell Prompt와 Hard Gate

### 7.1 Prompt

새 regime matrix를 추가하지 않는다.

KR/US buy/sell prompt에 공통 compact block만 주입한다.

```text
장기 구조: ...
Market Pulse: ...
Trend quality: n/5 (diagnostic)
Trigger contract: ...
```

Agent는 이 값을 재분류하지 않는다.

### 7.2 기존 buy hard gate

현재 deterministic gate를 유지한다.

- T1/T2
- score/R-R/max loss
- fundamental/momentum confirmation
- ATR/ADR SHADOW

새 trend quality를 하드게이트에 바로 넣지 않는다.

O'Neil harness가 universal entry-quality gate의 승자 오차단 위험을 이미 증명했기 때문이다.

### 7.3 Extension 의미 검증

기존 하네스가 지적한 extension 부호 문제를 우선 확인한다.

- MA20 아래를 비과열 만점으로 주지 않음
- MA20 위 과열과 아래 하락추세를 분리
- 변경 시 승자 보존율을 최우선 평가

새 indicator를 추가하기 전에 기존 indicator 의미 오류를 먼저 제거한다.

### 7.4 Sell

- catastrophic hardstop 유지
- O'Neil winner-hold 유지
- Market Pulse/trend quality는 sell prompt 설명용으로만 먼저 추가
- US stop 후 1·3·5일 회복과 ATR ratio를 계측
- stop 변경은 replay에서 한 규칙만 선택

---

## 8. US 휩쏘 Offline Backtest

### 8.1 목적

질문은 둘뿐이다.

1. 잘못된 종목·타이밍에 진입해서 손절됐는가?
2. 진입은 괜찮았지만 intraday hardstop이 정상 변동에 잘렸는가?

두 원인을 동시에 고치지 않는다. 어느 쪽 기여가 더 큰지 먼저 분리한다.

### 8.2 데이터 가용성

운영 db-server에서 확인됨:

- US 전체 종결 거래: 110건, 2026-01-28~2026-08-25
- 전체 거래의 일봉: 기존 US data path/yfinance로 조회 가능
- 2026-06-27 이후 최근 거래: 28건, 22 ticker
- 최근 5분봉: 2026-06-01~2026-08-25 조회 가능

따라서 과거 전체는 일봉, 최근 휩쏘 구간은 5분봉으로 검증한다.

### 8.3 실행 위치

- 신규 production process 없음
- 신규 cron 없음
- 신규 DB table 없음
- db-server에서 수동 1회 실행하거나 운영 DB snapshot을 읽는 별도 분석 환경에서 실행
- DB는 SQLite `mode=ro`
- 결과는 stdout/Markdown report

`harness`라는 런타임 개념을 만들지 않는다. 구현물이 필요하면 `tools/backtest_us_whipsaw.py` 한 파일이면 충분하다.

### 8.4 비교안

#### A — Baseline

- 운영 DB의 실제 진입·청산 결과
- trigger별 최근/전체 성과
- stop 후 1·3·5일 경로

#### B — Entry 문제

- Raw Gap Up 거래를 제외했을 때 결과
- 전체기간과 최근 60일을 분리
- 제거되는 승자 수와 gross winner return을 함께 보고

GapAndHold는 B 결과에서 Raw Gap Up 제외가 유효하지만 승자 손실이 너무 클 때만 최근 5분봉으로 추가 검토한다.

#### C — Exit 문제

- 현재 intraday hardstop
- 동일 stop line의 daily-close confirmation

다른 stop 폭과 reclaim은 첫 비교에 넣지 않는다. 현재 prompt의 closing-price 원칙과 실제 고빈도 hardstop 사이 차이부터 검증한다.

#### D — Entry+Exit

- Raw Gap Up 제외 + close-confirmed stop

원인 상호작용 확인용으로만 계산한다. D가 가장 좋아도 첫 배포에서 두 변경을 동시에 켜지 않는다.

### 8.5 계산 규칙

- 10개 슬롯은 포지션 크기 산술에 사용하지 않음. 모든 거래는 동일한 1슬롯 관찰 1건으로 비교
- 실제 수익률과 counterfactual 수익률을 거래별로 나란히 기록
- 최근 60일과 전체기간 분리
- 같은 ticker 반복 거래 별도 표시
- 수수료·슬리피지가 동일하게 포함되지 않으면 별도 명시
- 미래 데이터 참조 금지

### 8.6 지표

- 평균·중앙 거래 수익률
- profit factor
- 승률
- 평균 승자/평균 손실
- 최대 손실
- 최대 연속 손실
- stop 발생률
- stop 후 1·3·5일 매수가/손절가 회복률
- 제거된 승자 수와 gross profit 보존율

### 8.7 선택 규칙

#### B가 명확히 우수

- US Raw Gap Up만 기존 trigger 목록에서 중단
- 신규 trigger를 즉시 만들지 않음
- 다른 trigger는 그대로

#### C가 명확히 우수

- US hardstop만 close-confirm 방식으로 변경
- KR hardstop은 변경하지 않음
- trigger는 그대로

#### B와 C 모두 일부 개선

- 개선 폭이 큰 하나만 먼저 적용
- 나머지는 다음 실험으로 보류

#### 둘 다 개선 없음

- 코드 변경 없음
- 그때 GapAndHold 또는 ATR stop을 다음 backtest 후보로 설계

### 8.8 배포

선택된 한 변경만 기존 pipeline에 feature flag로 넣는다.

- Raw Gap Up 중단이면 trigger 생성부 한 곳
- Close-confirm이면 US hardstop 판단부 한 곳
- 신규 서비스·테이블·스케줄 없음
- 10슬롯 semantics 불변
- 기존 로그에서 변경 결과 확인

---

## 9. 구현 프로세스

### Step 1 — Offline backtest

- A/B/C/D 비교
- 코드 동작 변경 없음

### Step 2 — 한 변경 선택

- Entry 또는 Exit 중 하나
- GapAndHold는 필요성이 입증된 경우에만 설계

### Step 3 — 기존 pipeline 한 곳 수정

- 1슬롯 전량 매매 유지
- feature flag 하나
- 관련 단위·replay test

### Step 4 — 기존 로그로 관찰

- 별도 monitoring process 없음
- 기존 batch/hardstop 로그와 trading_history 사용

### Step 5 — 다음 판단

- 개선 확인: 유지
- 악화: flag rollback
- 불충분: 표본 누적 후 동일 offline backtest 재실행

---

## 10. 검증 기준

### 필수

- as-of/no-lookahead
- 시간순 holdout
- threshold sensitivity
- market-day block
- winner retention >=95%
- missed winner와 avoided loss 동시 보고
- recent window와 full history 분리

### 거부

- full-sample conditional return만 좋아짐
- AUC/Brier/log loss가 trigger-only보다 악화
- 후보 row를 독립 n으로 사용
- O'Neil base rubric을 universal veto로 사용
- prompt에 새 matrix를 추가해 길이와 분기만 증가
- legacy 코드 위에 장기 SHADOW branch가 계속 누적

---

## 11. 최종 결정

1. tactical 3-state 구현을 보류한다.
2. 기존 long-term regime과 Market Pulse를 유지한다.
3. trend quality는 0~5 diagnostic score이며 초기 hard gate가 아니다.
4. market state를 개별 종목 승패 예측기로 사용하지 않는다.
5. O'Neil은 M·T1/T2·손절·승자보존의 guardrail로 사용한다.
6. O'Neil 정석 base는 해당 trigger contract 안에서만 사용한다.
7. 자동 trigger circuit은 첫 휩쏘 수정 전에는 구현하지 않는다.
8. GapAndHold는 확정안이 아니라 offline backtest 이후의 선택 후보다.
9. 첫 단계는 A/B/C/D offline backtest이며 runtime harness가 아니다.
10. 결과가 고른 Entry 또는 Exit 한 곳만 수정한다.
11. 0.5슬롯은 사용하지 않으며 10개 논리 슬롯 semantics를 유지한다.
12. 새 service, table, cron 없이 시작한다.
