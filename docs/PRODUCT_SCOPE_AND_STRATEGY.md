# PRISM-INSIGHT 제품 범위·투자전략·운영 결정서

**상태:** Approved Scope Baseline v0.1 — 수치형 전략 파라미터는 연구·SHADOW로 확정
**대상:** 개인용 로컬 투자 의사결정 시스템
**실거래 상태:** 승인되지 않음. 이 문서와 향후 분석·코드·백테스트·paper 결과는 실거래를 승인하지 않는다.

---

## 1. 제품 북극성

> 한국·미국 시장의 주도 종목과 시장 국면을 매일 재현 가능한 데이터로 판별하고, LLM이 뉴스·거시경제·기업·기술적 맥락을 종합해 매매계획 후보를 제안하며, 결정론적 정책·리스크 엔진이 이를 검증하는 개인용 투자 의사결정 시스템.

제품 계층:

```text
OBSERVE  시장·섹터·주도주·뉴스·거시경제 관찰
RESEARCH 편향과 비용을 통제한 연구·백테스트
DECIDE   LLM 전략 proposal + 정량 피처 + 정책·리스크 검증
EXECUTE  paper 이후에만 durable intent·부분체결·대사·킬스위치
LEARN    판단·결과·반례·복기를 DB에 저장하고 검증된 교훈만 재사용
```

---

## 2. 확정된 제품 조건

| 항목 | 결정 |
|---|---|
| 사용자 | 한 명 |
| 실행 환경 | 개인 Mac 한 대 |
| 웹 대시보드 | 로컬 전용, 외부 공개 없음 |
| 기본 DB | SQLite 유지 |
| Telegram | 봇 1개·허용된 대화방 1개에서 자동 보고·알림·질문·읽기 전용 명령을 함께 운영 |
| 대상 시장 | 한국 + 미국 |
| 한국 주 데이터 | 한국투자증권 Open API(KIS)로 확정 |
| 한국 대안 데이터 | LS증권 API는 2차 대안·fallback 후보 |
| 미국 주 데이터 | Financial Modeling Prep(FMP) |
| 투자 스타일 | 단기 스윙과 중기 추세를 별도 전략·별도 성과·별도 교훈으로 운영 |
| 시황 컨텍스트 | AgentNews KR/US 보드. 결론이 아닌 조사 우선순위 지도 |
| LLM 역할 | 매수점수·시장국면·진입·손절·익절·재진입·피라미딩·사이징 배수의 구조화 후보 생성 |
| 코드 역할 | 원천 피처, 검증, 하드 veto, 실제 수량, 상태 머신, 주문·체결·대사 |
| 1차 실거래 | 없음 |
| 1차 주문 결과 | 수동 주문표 + 내부 SHADOW/paper ledger |
| 2차 실거래 | 자동 활성화하지 않음. 별도 승인 전에는 준비 상태까지만 |

외부효과는 위험에 따라 분리한다. 허용 chat/user로 보내는 Telegram 테스트 메시지와 공개 AgentNews KR/US 보드의 읽기 전용 fetch는 개발·테스트·운영 환경 모두에서 매회 별도 승인 없이 수행할 수 있다. Telegram transport는 명시적으로 활성화하고 테스트 메시지에는 `[TEST]`, 환경, request ID를 표시하며 rate limit·dedupe·감사 로그를 적용한다. AgentNews 단위 테스트는 재현성을 위해 fixture를 사용하되 live integration/smoke test와 제품 runtime은 실시간 fetch해야 한다. KIS/FMP 시장데이터의 실제 read-only integration/smoke도 승인되어 있으며 로컬 자격증명을 출력·변경하지 않고 호출량을 제한해 수행한다. 이 승인은 시세·재무·기업 이벤트·뉴스 등 시장데이터에만 적용되며 실제 계좌 조회, 잔고·보유종목, 주문·취소·정정, broker paper/live 효과에는 적용되지 않는다.

### 검증 계층과 operated readiness

Fixture·fake·mock은 unit/contract/CI의 결정론, 오류 주입, 회귀 재현을 위해 필수지만 외부 통합 완료의 증거를 대신하지 않는다. KIS, FMP, KRX/KIND/DART, SEC EDGAR, FRED/ALFRED, ECOS, AgentNews, Telegram, 외부 LLM처럼 네트워크·자격증명·구독 권한·외부 스키마에 의존하는 adapter/transport는 다음 네 상태를 구분한다.

1. **Foundation/tests:** fixture 기반 계약·정규화·실패 경로가 통과한다.
2. **Live integration:** 실제 endpoint에서 인증·권한·응답 스키마·timestamp·pagination·rate limit·timeout·redaction을 제한된 smoke로 검증한다.
3. **Runtime wiring:** 실제 application entrypoint와 scheduler가 검증된 transport를 사용한다.
4. **Operated readiness:** 예약 실행, 저장, 품질 gate, 발행, 장애·복구까지 실제 운영 경로에서 관측된다.

외부 adapter는 1만 통과한 상태를 `implemented foundation`으로만 보고하며 `live verified`, `runtime wired`, `operated ready`라고 표현하지 않는다. Live integration은 기본 unit CI와 분리된 marker/job으로 실행하고 비밀값·authorization header·계좌정보·원시 private payload를 로그나 artifact에 남기지 않는다. 자격증명이나 권한이 없으면 성공 처리하거나 demo/fixture 결과로 대체하지 않고 명시적으로 skip 또는 block한다. 알고리즘·정책·백테스트의 future-data trap, 오류, 경계 fixture는 외부 가용성 검증이 아니라 결정론적 안전 증명이므로 계속 fixture로 유지한다.

현재 standing approval은 KIS/FMP 시장데이터, 공개 AgentNews 읽기, allowlisted Telegram 테스트 발송에 한정한다. KRX/KIND/DART, SEC EDGAR, FRED/ALFRED, ECOS, 외부 LLM 등 그 밖의 실제 endpoint 호출은 해당 source의 비용·약관·자격증명·호출량·데이터 범위를 명시한 별도 source-specific capability/approval을 받은 뒤 수행한다. Live integration이 완료 기준이라는 사실 자체가 호출 권한을 부여하지 않는다.

### 한국 데이터 제공자 확정

현재 저장소는 한국투자증권(KIS) 주문·계좌·시세 관련 코드와 테스트가 이미 상당 부분 존재한다. LS증권은 저장소에서 실질적인 어댑터 구현을 찾지 못했다. 사용자는 KIS를 한국 primary로 확정했다. 1차에서 두 API를 동시에 도입하지 않고 다음처럼 진행한다.

```text
1차: KIS를 한국 primary adapter로 사용
2차: LS증권을 대체 primary 또는 장애 fallback으로 평가
```

1차 primary는 KIS에서 변경하지 않는다. LS증권은 2차에서 KIS 장애 fallback 또는 계좌·기능 대안의 필요성이 증명될 때만 평가한다. 동시에 둘 다 붙이는 것은 데이터 불일치·심볼 정규화·장애처리 범위를 불필요하게 키우므로 피한다.

### 미국 FMP 사용 범위

FMP는 미국의 가격·재무·기업 이벤트·뉴스 데이터 adapter 후보로 사용한다. FMP는 브로커 주문 실행자가 아니다. 사용 구독 등급에서 필요한 과거 데이터·호출량·기업행위·상장폐지·지수 구성종목을 제공하는지 구현 전에 capability check를 수행한다.

현재 저장소에서 FMP adapter 구현은 찾지 못했으므로 신규 구현 대상으로 본다.

---

# Part A. 1차·2차 제품 목표

## 3. 1차 목표 — 로컬 의사결정·연구·SHADOW 제품

### 목적

실제 주문을 보내지 않고도 매일 사용할 수 있는 한국·미국 투자 의사결정 제품을 완성한다.

### 1차 기능 범위

#### A. 데이터 기반

- KIS 한국 시장 adapter
- FMP 미국 시장 adapter
- 공통 내부 `security_id`
- 종목·티커·상장 상태 이력
- raw/adjusted 가격 구분
- 기업행위와 adjustment factor
- `observed_at`, `available_at`, `ingested_at`, `as_of_date`
- 데이터 신선도와 품질 상태
- 한국·미국 거래소 캘린더와 시간대

데이터 품질 상태와 정책 disposition은 구분한다.

```text
FRESH + 핵심 필드 완전                 -> ACCEPT 후보
PARTIAL + 비핵심 필드만 누락           -> REPORT_ONLY 가능
PARTIAL + 핵심 필드 누락               -> 신규 proposal REJECT
STALE / UNAVAILABLE / CONFLICT 핵심 데이터 -> 신규 proposal REJECT
```

`REPORT_ONLY`는 데이터 품질 상태가 아니라 정책 결과다. 보고서는 누락·stale·conflict를 눈에 띄게 표시하고 실행 가능한 신규 진입 proposal을 포함하지 않는다.

#### B. 매일·매주 보고서

- 한국·미국 일별 주도주
- 시장·섹터·종목 상태 변화
- AgentNews 기반 한국·미국 주간 기본/강세/약세 시나리오
- 뉴스·공시·실적·거시경제 evidence pack
- 체크 변수·주요 일정·무효화 조건
- 로컬 웹 대시보드
- Telegram 1개 채널 발행

#### C. 연구·백테스트

- point-in-time universe
- 상장폐지·티커 변경·기업행위
- 신호 시각과 체결 시각 분리
- same-close 체결 금지
- 수수료·세금·스프레드·슬리피지
- 현금·NAV·미실현손익
- walk-forward
- sealed OOS
- benchmark 비교
- 파라미터 실험 registry

#### D. LLM 전략 판단

- `TradePlanProposal`
- LLM 매수점수와 점수 분해
- 시장 국면 분포·신뢰도·무효화 조건
- 기계 평가 가능한 진입 predicate
- 손절·익절 후보
- 재진입·피라미딩 후보
- 사이징 risk multiplier 후보
- 근거·반대 근거·불확실성
- 필드별 `accept/clamp/recalculate/reject`

#### E. 셀프 피드백 SHADOW

- 입력 snapshot과 proposal 불변 저장
- traded/untraded 후보 결과 대칭 추적
- process review와 outcome review 분리
- lesson candidate와 반례 저장
- `CANDIDATE -> SHADOW`
- 검증 전 교훈은 실제 점수·진입에 반영하지 않음

#### F. 1차 주문 범위

- 브로커 주문 호출 없음
- 수동 주문표 생성 가능
- 내부 paper ledger 가능
- 가상 부분체결·취소·거절·UNKNOWN 시뮬레이션
- 실제 계좌·실제 주문 API는 읽기 전용 계좌 조회와도 분리

1차 `internal paper`는 로컬 결정론적 simulated broker와 `paper.sqlite`만 사용한다. KIS demo/모의투자는 실제 돈을 사용하지 않더라도 외부 broker API·계좌 효과이므로 internal paper와 같지 않으며 **2차 broker paper 범위**다.

### 1차 비범위

- 실계좌 주문
- 신규 거래소
- LS증권 동시 운영
- BTC 전략 재설계
- 옵션 전략
- 공매도·마진·레버리지
- 외부 공개 대시보드
- 다중 사용자·RBAC
- LLM의 자동 프롬프트·정책 수정

### 1차 완료 기준

- 로컬 Mac에서 예약 실행 후 KR/US 보고서가 재현 가능하게 생성됨
- KIS/FMP 시장데이터와 구현된 공식 evidence adapter가 실제 endpoint live integration을 통과하고, fixture-only foundation과 구분된 검증 기록이 있음
- 모든 값에 기준시점·출처·품질 상태가 있음
- FMP/KIS 장애 또는 stale 데이터 시 신규 proposal이 fail-closed 됨
- LLM proposal 원문·버전·입력·검증 결과가 저장됨
- 백테스트가 survivorship·비용·PIT 제한을 명시적으로 처리함
- 로컬 대시보드와 하나의 Telegram 채널에 같은 핵심 결과가 표시됨
- 실제 브로커 주문 호출이 0임을 테스트로 증명함
- 셀프 피드백 교훈이 SHADOW를 넘지 않음

---

## 4. 2차 목표 — 브로커 paper·실행 안전·확장 준비

### 목적

1차 제품의 판단과 연구를 실제 돈 없이 broker paper/demo 환경에서 운영하고, 주문 수명주기·대사·복구를 검증한다.

### 2차 기능 범위

#### A. 실행 안전

- 모든 주문의 `OrderIntent` 강제
- intent 없는 브로커 주문 우회 제거
- idempotency와 broker order ID uniqueness
- 부분체결·미체결·거절·취소·UNKNOWN
- 재시작 복구
- 현금·포지션·미체결·체결 대사
- 데이터·장부 불일치 시 신규 위험 차단
- global/per-venue kill switch

#### B. Broker paper/demo

- KIS 모의투자 또는 승인된 paper 환경
- LS증권은 필요성이 확인되면 adapter 후보로 평가
- venue capability matrix
- 주문형식·호가단위·거래시간·정산·매수여력
- paper/live 자격증명·DB·프로세스 분리

#### C. 포트폴리오 위험

- 종목·섹터·시장·통화별 노출
- KRW/USD 환산 NAV
- 미체결 주문 포함 잠재 노출
- gross exposure
- 상관·팩터 집중도
- 유동성·시장충격 제한
- 일일 손실·drawdown gate

#### D. 셀프 피드백 승격

- `SHADOW -> PAPER_PROMOTED`
- 시간 분리 OOS
- support/contra evidence
- 모델·프롬프트 버전 격리
- lesson retrieval 효과의 shadow A/B
- drift 발생 시 자동 `SUSPENDED`

#### E. 확장

- LS증권 fallback 필요성 재평가
- 신규 거래소·브로커 adapter
- Kakao 등 추가 메시지 채널은 별도 필요가 생길 때만 검토
- 제한적 live 준비 문서·테스트는 가능하나 실제 활성화는 별도 승인

### 2차 비범위

- 사용자 승인 없는 실거래
- 자동 자금이체·출금
- 다중 고객 서비스
- 외부 공개 투자 플랫폼
- 수익 보장 또는 백테스트의 실전 성과 표현

### 2차 완료 기준

- paper/demo에서 중복 주문, 부분체결, UNKNOWN, 재시작, 대사 시나리오 통과
- broker와 내부 장부가 재현 가능하게 일치
- kill switch 훈련 성공
- 포트폴리오 NAV와 주문·체결 ledger로 성과 재현 가능
- LLM이 실제 주문 수량·하드 리스크·OrderIntent를 생성하지 못함
- `PAPER_PROMOTED` 교훈만 paper proposal에 제한적으로 검색됨
- 신규 venue는 공통 실행 계약을 우회하지 않음

---

# Part B. 투자전략 명세

## 5. 투자전략 명세를 확정하는 방법

전략을 한 번에 감으로 확정하지 않는다. 다음 세 층으로 나눈다.

```text
불변 원칙      안전·시간·데이터 계약. 즉시 확정
연구 가설      점수 가중치·임계값·보유기간. 백테스트로 확정
운영 파라미터  위험 한도·쿨다운·피라미딩 횟수. paper 후 확정
```

### 전략 확정 순서

1. 대상 시장·상품·투자 방향 확정
2. 신호가 생성되는 기준시각 확정
3. 체결 가정 확정
4. 정량 피처 정의
5. LLM proposal schema 정의
6. 하드 veto 정의
7. 사이징 함수 정의
8. 청산·재진입·피라미딩 상태 머신 정의
9. 비용·benchmark·성과지표 정의
10. train/walk-forward/sealed OOS 기간 사전등록
11. paper 운영 기준 정의
12. 변경마다 전략 버전 발급

모든 전략 실행·백테스트 결과에는 다음 버전을 저장한다.

```text
strategy_version
feature_version
policy_version
risk_version
model_version
prompt_version
data_snapshot_id
config_hash
code_sha
```

---

## 6. Strategy Spec v0.1 — 불변 원칙

### 시장과 방향

- 한국·미국 주식
- 1차는 long-only
- 현물 중심
- 공매도·마진·레버리지·옵션 제외
- 장중 초단타 제외

### 전략 분리

단기 스윙과 중기 추세를 하나의 점수·프롬프트·성과표로 혼합하지 않는다.

```text
SWING_V1
  market: KR | US
  연구 평가 horizon: 5 / 10 / 20 trading sessions

TREND_V1
  market: KR | US
  연구 평가 horizon: 20 / 60 / 120 trading sessions
```

위 horizon은 성과 추적·연구 구간이며 보유기간을 강제하는 청산 규칙이 아니다. 실제 최대 보유기간과 time stop은 walk-forward·sealed OOS·SHADOW로 별도 확정한다.

전략마다 다음을 분리한다.

- `strategy_id`와 strategy version
- feature·score·entry threshold
- prompt template와 model evaluation
- 진입·청산·재진입·피라미딩 상태
- paper virtual book와 NAV
- outcome horizon과 benchmark
- lesson candidate·support·contra evidence

동일 종목이 두 전략에서 동시에 선택될 수 있으므로 연구·내부 paper에서는 전략별 virtual book을 유지하되, 통합 포트폴리오 계층이 동일 종목·섹터·시장 노출과 미체결 주문을 합산한다. 같은 계좌에서 전략별 주문을 단순 중복 제출하지 않는다. 한 전략의 교훈은 `scope=strategy`가 기본이며, 별도 교차전략 검증 없이는 다른 전략에 상속하지 않는다.

### 의사결정 주기

- 일봉이 확정된 뒤 판단
- 시황·뉴스는 기준시각을 명시
- 신호 계산에 사용한 동일 종가로 체결하지 않음
- 다음 거래 가능 세션의 실행 가능 가격을 사용
- 장전·장후·정규장을 혼합하지 않음

### 대상 universe 초안

#### 한국

- KOSPI/KOSDAQ 보통주
- 상장 상태와 거래정지 확인
- 최소 가격·거래대금·시가총액 필터는 연구로 확정
- 관리·투자주의·상장폐지 위험 종목은 결정론적 gate

#### 미국

- NYSE/Nasdaq 상장 보통주와 승인된 일반 ETF
- OTC 제외
- 1차에서 레버리지·인버스 ETF 제외
- 최소 가격·거래대금·시가총액 필터는 연구로 확정
- 파산·상장요건·기업행위는 공식 데이터 기반 gate

### 시장 국면

```text
strong_bull
moderate_bull
sideways
moderate_bear
strong_bear
```

- 코드가 정량 baseline regime을 계산
- LLM이 regime distribution·신뢰도·근거·무효화 조건 제안
- 저신뢰·데이터 결측·충돌 시 신규 노출을 보수화 또는 차단
- 한 번의 LLM 응답으로 국면이 진동하지 않도록 hysteresis 적용

### 매수점수

점수를 두 개로 분리 저장한다.

```text
quantitative_score  0..100
llm_score           0..100
```

초기에는 임의의 고정 가중치로 하나의 점수로 합치지 않는다. `decision_score`의 결합 방법은 walk-forward와 sealed OOS에서 결정한다.

필수 점수 분해:

- 추세·가격 구조
- 상대강도
- 거래량·수급
- 변동성·유동성
- 재무·실적
- 촉매·뉴스
- 섹터 주도성
- 시장 국면 적합성
- 반대 근거·불확실성 감점

### 진입

- LLM은 기계 평가 가능한 predicate 후보를 생성
- 코드는 실제 데이터로 predicate 평가
- 유효기간·기준가격·시장 세션 필수
- stale·거래정지·유동성 부족·리스크 초과는 hard veto
- LLM은 hard veto를 해제할 수 없음

### 사이징

기본 모델은 **손절거리 기반 리스크 예산**을 권고한다.

```text
base_quantity
  = account_risk_budget / abs(entry_price - validated_stop)

final_quantity
  = base_quantity
  × validated_llm_risk_multiplier
  → 현금·종목·섹터·시장·통화·유동성 상한 적용
```

LLM은 risk multiplier 후보와 근거를 제안한다. 코드가 실제 수량을 계산하고 상한을 적용한다.

정확한 다음 값은 위험 성향과 연구 결과를 반영해 별도 승인한다.

- 거래당 위험예산
- 종목 최대 비중
- 섹터 최대 비중
- 시장별 최대 노출
- 총 gross exposure
- 일일 손실 한도
- drawdown gate

이 값들은 1차 SHADOW에서는 여러 후보를 병렬 평가하고, 2차 paper 전 하나의 설정을 사전등록한다.

### 손절·익절

- LLM은 thesis invalidation·기술적 구조·ATR 등을 바탕으로 후보 제안
- 코드는 가격 유효성·최대 손실·최소 손익비 검증
- 진입 후 손절을 불리한 방향으로 확대 금지
- LLM 장애와 무관하게 보호성 청산은 동작
- 부분 익절·trailing 여부와 비율은 연구 가설로 검증

### 재진입

- LLM은 이전 가설과 다른 새 촉매·추세 복구를 설명
- 코드는 쿨다운·최대 재진입·누적손실을 gate
- 동일한 근거를 반복하면 새 proposal로 인정하지 않음
- 정확한 쿨다운과 횟수는 SHADOW 비교 후 paper 전 확정

### 피라미딩

- 수익 중인 포지션만 후보
- 손실 포지션 물타기 금지
- 별도 add-on intent
- 각 추가 진입은 새 데이터·새 조건을 요구
- 총 포지션 위험과 최대 추가 횟수는 코드가 제한
- 단계별 추가 비율과 횟수는 SHADOW에서 연구 후 paper 전 확정

### 전략 평가

- 포트폴리오 NAV 기준
- benchmark 포함
- CAGR, MDD, volatility, Sharpe/Sortino
- turnover와 모든 거래비용
- regime·기간·섹터·유동성별 성과
- calibration과 clamp/reject 비율
- traded/untraded 대칭 평가
- 단일 최고 성과보다 견고성과 OOS 재현성을 우선

---

# Part C. LLM 권한 확정

## 7. LLM 권한 매트릭스

| 항목 | LLM 권한 | 코드 권한 |
|---|---|---|
| 뉴스·거시경제 | 요약·시나리오·반대 근거 | 기준시점·출처·수치 검증 |
| 매수점수 | LLM score와 분해 생성 | quant score·캘리브레이션·범위 검증 |
| 시장 국면 | 분포·신뢰도·무효화 조건 | baseline·hysteresis·저신뢰 정책 |
| 진입 | predicate 후보 | 실제 데이터 평가·hard veto |
| 손절·익절 | 후보 가격·논리 | 확정 가격·최대손실·손절 확대 금지 |
| 사이징 | risk multiplier 후보 | 실제 수량·계좌/노출/유동성 상한 |
| 재진입 | 새 촉매·가설 복구 판단 | 쿨다운·횟수·누적손실 |
| 피라미딩 | add-on 조건 후보 | 총위험·횟수·중복 방지 |
| 교훈 | lesson candidate 제안 | 증거 집계·상태 전이·승격 gate |
| 주문 | 권한 없음 | `OrderIntent`와 `ExecutionService`만 가능 |
| 실거래 승인 | 권한 없음 | 사용자 별도 승인 필요 |

## 8. LLM 필수 저장 항목

- 입력 snapshot
- 이용 가능 시각
- evidence IDs와 원문 URL
- model/provider/version
- prompt template와 hash
- sampling 설정
- raw output
- parsed proposal
- validator version
- field disposition log
- retrieved lesson IDs
- 결과와 retrospective

## 9. LLM 실패 정책

```text
LLM timeout         -> 신규 proposal 실패
schema invalid      -> 신규 위험 차단
근거 불충분          -> 신규 위험 차단 또는 report-only
데이터 stale         -> 신규 위험 차단
LLM 서비스 중단      -> 기존 보호성 정책은 계속 작동
```

## 10. 환경별 권한

```text
RESEARCH  새 전략·점수·정책 후보 자유롭게 제안
SHADOW    실제 판단에 영향 없이 병렬 기록
PAPER     승격된 필드·교훈만 제한 반영
LIVE      별도 승인·더 낮은 상한·추가 hard gate
```

LLM은 어떤 환경에서도 시스템 프롬프트, 정책 코드, 리스크 상한 또는 live 설정을 스스로 수정·배포하지 못한다.

---

# Part D. 데이터 제공자

## 11. 데이터 소스 매트릭스

| 데이터 | 1차 권고 | 보조·2차 후보 | 주의점 |
|---|---|---|---|
| 한국 시세·거래량 | KIS | LS증권 | 호출한도·정정·장중/종가 시각 |
| 한국 계좌 읽기 | KIS | LS증권 | 1차 계좌 조회·주문 호출 금지, Phase 2 별도 승인 |
| 한국 기업 공시 | DART | KIND/KRX | 발표·접수 가능 시각 저장 |
| 한국 상장·기업행위 | KRX/KIND 또는 KIS 제공 범위 | LS증권 | 티커 이력·정지·분할·배당 |
| 한국 거시경제 | 한국은행 ECOS | 통계청·기재부 | 발표시각·개정 이력 |
| 미국 가격·재무 | FMP | 별도 보조 제공자 추후 | 구독 등급·호출량·역사 범위 |
| 미국 공시 | SEC EDGAR | FMP 정규화 데이터 | SEC 제출시각을 기준으로 저장 |
| 미국 기업행위 | FMP 제공 범위 + 공식 교차확인 | 거래소/SEC | delisting·split·dividend |
| 미국 거시경제 | FRED/ALFRED | BLS/BEA 원 출처 | 역사 연구는 vintage 사용 |
| KR/US 시황 프레임 | AgentNews | 원 출처 | 결론 엔진이 아닌 priority map |
| 뉴스 | AgentNews 링크·공식 공시·제공자 metadata | 추가 뉴스 provider 추후 | 원문 재배포 권리 확인 |
| 환율·금리·원유 | FMP/FRED/ECOS 및 공식 자료 | 보조 provider 추후 | 같은 시각 비교·시장 세션 구분 |
| 캘린더 | 거래소 공식 일정 + 검증된 calendar library | provider calendar | 휴장·반일장·DST |

### 공급자 원칙

- adapter마다 capability matrix 작성
- raw response를 append-only 저장
- 내부 표준 스키마로 정규화
- provider 장애와 source disagreement 기록
- fallback 데이터는 source와 quality를 명확히 표시
- fallback이 primary를 조용히 덮어쓰지 않음
- 데이터 저장·캐시·재배포 라이선스 확인

---

# Part E. 개인 Mac·SQLite·대시보드·Telegram

## 12. 개인 Mac에서 실행한다는 의미

개인 Mac 구성은 다음을 의미한다.

```text
사용자 1명
로컬 프로세스
외부 서비스 공개 없음
낮은 동시 쓰기
직접 관리 가능한 자격증명
서버 비용 없음
```

1차의 일별 보고서·연구·SHADOW에는 적합하다.

### 장점

- 설정이 단순함
- 민감한 계좌·연구 DB가 로컬에 남음
- 별도 클라우드 서버·DB 비용 없음
- 로컬 파일과 대시보드 접근이 쉬움
- 단일 사용자 제품에 충분한 성능

### 한계

- Mac이 잠자면 예약 작업이 실행되지 않을 수 있음
- 네트워크가 끊기면 데이터 수집·Telegram이 중단됨
- 24시간 주문 감시·대사가 필요한 live에는 부적합할 수 있음
- 고장·분실 시 복구를 위해 별도 백업 필요
- 여러 프로세스가 동시에 SQLite에 쓰면 lock 관리 필요

### 권장 로컬 구조

```text
macOS launchd
  -> collector jobs
  -> report/research jobs
  -> feedback tracker
  -> local watchdog

SQLite
  -> research.sqlite
  -> paper.sqlite
  -> ops.sqlite

Dashboard
  -> 127.0.0.1 only

Telegram
  -> bot 1개 / channel 1개
```

실제 주문이 없는 1차에서는 이 구조가 적합하다. 2차 paper에서도 충분할 수 있지만, 장시간 broker 감시가 필요해지면 Mac의 sleep·재부팅·네트워크 정책을 별도 검증한다.

---

## 13. SQLite를 유지한다는 의미

SQLite는 한 파일 기반 DB이지만 toy DB라는 뜻은 아니다. 개인용·단일 머신·낮은 동시 쓰기에서는 적합하다.

### 유지 이유

- 서버형 DB 운영 불필요
- Python 지원이 기본적이고 안정적
- 트랜잭션·unique constraint·WAL 지원
- 백업과 이동이 쉬움
- 현재 저장소가 이미 SQLite 중심

### 필요한 운영 규칙

- WAL mode
- foreign keys ON
- busy timeout
- 짧은 write transaction
- 한 테이블에 여러 writer가 몰리지 않도록 job ownership
- schema version과 단일 migration 체계
- SQLite Online Backup API 또는 안전한 backup 방식
- 정기 `integrity_check`
- backup restore rehearsal
- 파일 권한 최소화와 FileVault

### DB 분리

```text
research.sqlite
  데이터 snapshot, feature, proposal, outcome, lesson

paper.sqlite
  paper cash, position, order, fill, NAV

ops.sqlite
  job run, heartbeat, alert, migration, backup 상태
```

실계좌 단계가 별도 승인되면 live DB는 paper와 물리적으로 분리해야 한다.

### PostgreSQL 전환 조건

다음 중 하나가 발생할 때만 검토한다.

- 다중 사용자
- 여러 머신에서 동시 쓰기
- 외부 공개 API
- 높은 동시 주문·이벤트 처리
- HA·복제·원격 운영 필요

현재 조건에서는 PostgreSQL로 바꿀 이유가 없다.

---

## 14. 로컬 대시보드

- `127.0.0.1`에만 bind
- 외부 네트워크 공개 금지
- 브라우저에 API secret 전달 금지
- 분석·SHADOW·paper 상태를 명확히 표시
- live 버튼 없음
- 입력 snapshot→LLM proposal→검증→결과→교훈 replay 제공

로컬 전용이므로 1차에서 복잡한 RBAC는 필요 없다. 다만 OS 계정·화면 잠금·FileVault는 사용한다.

---

## 15. Telegram 채널

### 결정

개인 사용자이므로 **Telegram bot 1개와 허용된 대화방 1개**에서 다음을 함께 운영한다.

- 시스템이 보내는 단방향 일별·주간 보고서
- 위험·장애·복구 알림
- 사용자의 자연어 질문
- allowlist 기반 읽기 전용 명령

자동 unit/CI 경로의 Telegram transport는 기본 OFF지만 환경 자체로 발송을 금지하지 않는다. 개발·테스트·운영 어디서든 로컬 설정으로 transport를 활성화해 허용 chat/user에 테스트 메시지를 보낼 수 있으며, 이 범위에는 사용자의 상시 승인이 적용된다. 임의 목적지·다중 broadcast·주문 명령은 포함되지 않는다.

Telegram의 순수 broadcast channel은 일반 메시지·질문을 직접 받는 대화 표면으로 적합하지 않다. 하나의 목적지만 유지하려면 봇 DM 또는 비공개 supergroup을 사용한다. 기존 broadcast channel을 반드시 유지하면 발행 channel과 질문용 bot DM/group의 chat ID가 두 개 필요하다.

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_CHAT_ID
TELEGRAM_ALLOWED_USER_ID
```

`TELEGRAM_ALLOWED_CHAT_ID`는 자동 보고의 목적지이자 inbound update의 허용 대화방이다. `TELEGRAM_ALLOWED_USER_ID`로 명령·질문을 보낸 사용자를 한 명으로 제한한다. 외부 webhook을 열지 않고 Mac에서 Telegram Bot API long polling을 사용하므로 로컬 대시보드를 외부 공개할 필요가 없다.

현재 저장소의 일부 레거시 README·archive·BTC 설정에는 `TELEGRAM_AI_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, 시장·언어별 channel ID, 별도 BTC ops channel이 남아 있다. 구현 시 `TELEGRAM_CHANNEL_ID`는 `TELEGRAM_ALLOWED_CHAT_ID`의 호환 fallback으로만 읽고 새 로컬 경로는 별도 AI bot·언어별·시장별·ops channel을 요구하지 않도록 격리한다.

메시지 종류를 prefix로 구분한다.

```text
[KR DAILY]
[US DAILY]
[WEEKLY MACRO]
[PAPER]
[RISK]
[ERROR]
[RECOVERY]
```

### 대화형 질문·명령

1차 대화형 인터페이스는 read-only다. 자연어 질문과 다음 allowlist 명령을 제공한다.

```text
/help
/status
/daily kr|us
/weekly kr|us
/symbol <ticker>
/portfolio
/paper
/health
```

- `/portfolio`는 1차에서 내부 paper snapshot만 표시한다. 실제 KIS 계좌의 읽기 전용 snapshot도 2차로 미루며, 별도의 범위 승인 없이는 조회하지 않는다.
- `/paper`는 paper 상태·성과·최근 가상 proposal을 조회하며 주문을 생성하지 않는다.
- 자연어 질문은 저장된 snapshot·report·evidence 범위에서 답하고 기준시점·출처를 표시한다.
- 허용되지 않은 chat/user의 update는 응답하지 않고 감사 로그에 metadata만 기록한다.
- prompt injection을 포함한 외부 뉴스·메시지는 명령으로 실행하지 않는다.

다음 명령·자연어 의도는 구현하지 않거나 명시적으로 거부한다.

```text
/buy /sell /cancel /live
실주문 생성·승인
live 전환
리스크 한도 상향
킬스위치 해제
자격증명 조회·변경
시스템 프롬프트·정책 수정
```

보고 발행과 inbound 처리는 같은 bot token을 사용하되, Telegram update를 소비하는 long-polling worker는 하나만 둔다. 보고 job은 Bot API outbound 전송을 요청하고 interactive worker가 승인 chat/user와 명령 allowlist를 검증한다.

### 장애 알림 채널

별도의 장애 채널은 1차에 필요하지 않다. 같은 Telegram 채널에 `[ERROR]`와 `[RECOVERY]`를 보낸다.

그러나 **장애 감지 기능 자체는 필요하다.** 본 프로세스가 죽으면 본 프로세스는 알림을 보낼 수 없으므로 별도의 가벼운 `launchd` watchdog가 필요하다.

```text
watchdog checks heartbeat
  -> 정상: 조용히 유지
  -> 지연/중단: 같은 Telegram 채널에 [ERROR]
  -> 네트워크도 실패: macOS local notification + ops.sqlite 기록
  -> 복구: [RECOVERY]
```

2차에서 알림량이 많아지거나 paper 운영 경고가 보고서를 방해할 때만 별도 ops 채널을 고려한다.

---

# Part F. 남은 결정

## 16. 지금 확정하지 않아도 되는 항목

다음은 1차 연구·SHADOW 결과를 본 후 확정한다.

- quant/LLM 점수 결합 가중치
- 거래당 위험예산
- 종목·섹터·시장 최대 비중
- 국면별 진입 임계값
- 정확한 보유기간
- 손절·익절 거리
- 부분 익절 비율
- 재진입 쿨다운
- 피라미딩 횟수·비율
- LS증권 도입 여부
- 신규 거래소

## 17. 제품 기준선 승인 상태

사용자가 다음을 승인했다.

1. 한국 primary는 KIS
2. 단기 스윙과 중기 추세를 별도 전략으로 운영
3. Telegram bot 1개에서 자동 보고·알림과 대화형 질문·읽기 전용 명령을 함께 운영

따라서 제품 범위·전략 family·메시지 인터페이스에 대한 Phase 0 기준선 결정은 완료됐다. 1차 구현 전 남은 값은 사용자 취향을 추측해 고정하지 않고 연구·SHADOW로 비교할 수치형 파라미터다. 자격증명 변경·Telegram 실제 테스트 전송·broker API 주문·live 활성화는 각각 별도 승인을 요구한다.
