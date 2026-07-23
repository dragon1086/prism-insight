# PRISM-INSIGHT Product North Star & Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

> **제품 결정 원문:** `docs/PRODUCT_SCOPE_AND_STRATEGY.md`. 본 문서는 구현 작업 순서와 파일·테스트 gate를 담당하며, 제품 범위·전략·LLM 권한·데이터·로컬 운영 결정이 충돌할 경우 제품 결정 원문을 우선한다.

**Goal:** PRISM-INSIGHT를 “매일 시장과 주도주를 관찰하고, LLM이 뉴스·거시경제·기업·기술적 맥락을 종합해 매매 전략 후보를 제안하며, 결정론적 정책·리스크 엔진이 이를 검증·제한하고, 모의투자에서 충분히 검증된 주문만 안전하게 집행할 수 있는 개인 투자 의사결정 시스템”으로 발전시킨다.

**Architecture:** 분석·연구·전략 제안·정책 검증·리스크·주문 집행을 분리한다. LLM은 매수점수, 시장 국면, 진입 조건, 손절·익절·재진입·피라미딩, 사이징 배수의 **구조화된 전략 후보**를 근거·불확실성·무효화 조건과 함께 생성한다. 결정론적 코드는 시장 데이터·피처 계산, 스키마·신선도 검증, 하드 리스크 한도, 실제 수량 계산, 상태 머신, 주문·체결·대사를 담당하며 실행 가능한 `OrderIntent`의 유일한 생성자가 된다. 기존 KR/US 전체를 당장 공통 코어로 통합하지는 않지만, 신규 거래소·브로커는 반드시 `OrderIntent`/`ExecutionService` 계약 뒤에 어댑터로 연결하여 세 번째 복제 구현을 만들지 않는다.

**Tech Stack:** Python 3.10+, 로컬 SQLite, `prism_core`, KIS(KR primary), FMP(US data), OpenAI/Claude, localhost Next.js dashboard, Telegram bot 1개·허용 대화방 1개(outbound 보고 + long-polling 질문/읽기 전용 명령), `launchd`, pytest/GitHub Actions. LS증권·신규 거래소·Bybit 확장은 2차 후보이며 1차 범위가 아니다.

---

## 1. 제품 북극성

### 한 문장

> 시장의 주도 종목과 위험 국면을 매일 재현 가능한 데이터로 판별하고, LLM이 맥락을 종합해 매매 계획 후보를 만들며, 결정론적 정책·리스크 엔진이 이를 검증·제한하고 실제 주문은 사람의 승인 아래에서만 다루는 시스템.

### 사용자가 매일 얻어야 하는 결과

1. 오늘의 시장 국면과 전일 대비 변화
2. 강한 섹터·산업과 새로 부상한 주도 종목
3. 기존 주도 종목 중 유지·약화·이탈 종목
4. 각 종목의 매수점수와 점수 구성 근거
5. 한국·미국별 이번 주 기본/강세/약세 시나리오와 뉴스·실적·공시·거시경제 촉매 및 반대 근거
6. 진입 가능 여부와 진입을 막는 조건
7. 손절·익절·재진입·피라미딩 계획
8. 포지션 크기, 계좌·종목·섹터·시장별 위험 기여도
9. 현재 보유·주문·부분체결·미체결·취소 상태
10. 오늘 전략이 과거와 전향적 모의운영에서 어떻게 작동했는지

### 네 가지 제품 축

```text
OBSERVE   시장·섹터·주도주·뉴스·거시경제를 관찰
RESEARCH  편향과 비용을 통제한 백테스트로 가설 검증
DECIDE    LLM 전략 판단과 정량 피처를 결합하고 정책·리스크 엔진으로 검증
EXECUTE   주문 의도·중복방지·부분체결·대사·킬스위치로 안전하게 운영
```

### 비목표

- LLM이 직접 주문 수량·가격·매수·매도를 승인하는 구조
- 백테스트 결과만으로 실거래를 자동 활성화하는 구조
- 수익 보장 또는 과거 성과를 실전 성과로 표현하는 것
- KR/US 코드를 전면 재작성하는 대형 공통화 작업
- 신규 거래소를 기존 시장 코드의 복사본으로 추가하는 것
- 사용자 승인 없이 실계좌 기능이나 라이브 게이트를 켜는 것

---

## 2. LLM 전략 에이전트와 결정론적 정책·실행 엔진의 경계

### 결정론적 코드가 독점해야 하는 안전 영역

- Point-in-time 데이터 조회와 데이터 신선도 판정
- 주도주·상대강도·변동성·유동성·재무 등 원천 피처 계산
- LLM 출력 스키마·범위·데이터 신선도·출처 검증
- 진입의 하드 veto와 거래 가능 시간·가격·수량 검증
- 손절 확대 금지, 재진입 쿨다운, 최대 피라미딩 횟수 같은 상태 머신 불변식
- 계좌·종목·섹터·시장별 위험 한도와 실제 주문 수량 계산
- 주문 생성·정정·취소
- idempotency key와 중복주문 차단
- 부분체결·미체결·취소·거절·결과불명 상태 처리
- 브로커/거래소와 로컬 장부 대사
- 일일 손실 제한, 오류 회로차단기, 킬스위치
- 백테스트·수수료·스프레드·슬리피지·NAV 계산
- paper/live 환경 라우팅

### LLM이 의미 있게 담당하는 전략 영역

- 뉴스·공시·실적·거시경제 요약
- AgentNews의 한국/미국 현재 맥락 보드를 우선순위 지도(priority map)로 읽고, 이번 주 시장 시나리오와 체크 변수를 구성
- 정량 피처와 비정형 근거를 결합한 LLM 매수점수와 요인별 기여도
- 시장 국면 후보의 확률/신뢰도 분포와 무효화 조건
- 기계 평가 가능한 진입 predicate 후보
- 손절·익절 후보와 가격 수준의 논리
- 재진입·피라미딩 조건 후보와 새로운 촉매 여부
- 포지션 위험 배수 후보와 확신도·불확실성
- 주도주·시장 국면의 자연어 설명
- 강세·기본·약세 시나리오 정리
- 반대 근거와 무효화 조건 제안
- 연구 가설 제안
- 웹 대시보드와 Telegram용 보고서 작성

### 강제 경계

```text
LLM output -> TradePlanProposal -> deterministic validation/clamp/reject
validated proposal -> risk sizing -> OrderIntent -> ExecutionService
LLM output -X-> direct OrderIntent, broker API, live authorization
```

LLM은 단순 설명기가 아니라 전략 제안자다. 다만 제안은 strict schema를 따라야 하며, 당시 시점의 입력 피처·원문 근거·출력·모델·프롬프트·샘플링 설정·검증/클램프 결과를 저장한다. 백테스트와 replay는 저장된 제안을 사용하고, 새 모델·프롬프트 버전은 paper 재검증 없이 기존 live 범위로 승격하지 않는다.

---

## 3. 현재 요구사항 해석

| 사용자 요구 | 제품 의미 | 난이도/위험 |
|---|---|---|
| 일별 주도주 리포트 | 시장·섹터·종목 상태 변화를 매일 산출 | 중간 |
| 뉴스·거시경제 분석 | 구조화 근거 + LLM 설명 | 중간 |
| 연구·백테스트 | PIT·비용·OOS·walk-forward 검증 | 높음 |
| 매수점수·국면별 진입 | LLM 전략점수·국면·predicate + 정량 피처 + 정책 gate | 높음 |
| 손절·익절·재진입·피라미딩 | 포지션 정책 상태 머신 | 매우 높음 |
| LLM 셀프 피드백 DB | 판단·결과·복기·교훈을 버전 관리하고 검증된 교훈만 재사용 | 높음 |
| 공통 기능 추가 불필요 | KR/US 전면 통합은 보류 | 합리적 |
| 주문 중복방지·부분체결 | 실행 안전의 선행 조건 | 최우선/P0 |
| 포지션 사이징 변경 | 전략·계좌 위험 계약 변경 | 매우 높음 |
| 거래소 추가 | 어댑터·자격증명·대사·주문계약 추가 | 마지막 단계 |
| 웹·Telegram 유지 | 사용자 인터페이스 유지 | 중간 |

`공통 기능 추가 불필요`는 KR/US 대형 리팩터링을 하지 않는다는 뜻으로 해석한다. 단, 신규 거래소는 최소 공통 계약인 `OrderIntent`/`ExecutionService`를 반드시 따라야 한다.

---

## 4. 확정된 범위와 남은 연구 파라미터

확정된 제품 범위:

1. 대상 시장은 KR+US이며 BTC·옵션·레버리지는 1차에서 제외한다.
2. 1차는 개인 Mac에서 읽기 전용 리포트·연구·LLM proposal·수동 주문표·내부 SHADOW/paper ledger까지다.
3. 1차는 실제 브로커 주문을 호출하지 않는다.
4. 한국 데이터 primary는 KIS로 확정하고, LS증권은 2차 대안/fallback 후보로 둔다.
5. 미국 데이터 primary는 FMP이며 신규 adapter가 필요하다.
6. 사이징 기본 모델은 손절거리 기반 리스크 예산이며, LLM은 코드가 제한하는 risk multiplier 후보만 생성한다.
7. 로컬 대시보드는 `127.0.0.1` 전용이며 외부 접근을 제공하지 않는다.
8. Telegram은 봇 1개·허용 대화방 1개에서 보고서·장애 알림과 자연어 질문·allowlist 읽기 전용 명령을 함께 운영한다.
9. 단기 스윙 `SWING_V1`과 중기 추세 `TREND_V1`을 별도 전략·성과·교훈으로 운영하되 통합 포트폴리오가 중복 노출을 제한한다.
10. SQLite는 `research.sqlite`, `paper.sqlite`, `ops.sqlite`로 역할을 분리하는 방향을 사용한다.
11. 실거래는 2차에서도 자동 활성화하지 않으며 별도 명시적 승인을 요구한다.

구현 전에 숫자를 추측하지 않고 연구·SHADOW·paper로 확정할 항목:

- quant/LLM 점수 결합 방식
- 거래당 위험예산과 종목·섹터·시장 최대 노출
- 국면별 진입 임계값과 보유기간
- 부분 익절·trailing 정책
- 재진입 쿨다운·최대 횟수
- 피라미딩 횟수·단계별 비율
- 부분체결 잔량 취소·재가격 정책

상세 결정과 승인 항목은 `docs/PRODUCT_SCOPE_AND_STRATEGY.md`를 따른다.

---

## 5. 단계별 구현 순서

## Phase 0 — 기준선·제품 문서·실행 안전성 고정

**Objective:** 코드 추가 전에 최신 포크 기준선과 실거래 경계를 고정하고 제품의 북극성을 저장소의 공식 문서로 만든다.

**Files:**
- Create: `docs/PRODUCT_NORTH_STAR.md`
- Create: `docs/IMPLEMENTATION_ROADMAP.md`
- Modify: `AGENTS.md`
- Review: `docs/FEATURE_FLAGS.md`
- Review: `.github/workflows/ci.yml`

**Steps:**

1. 로컬 `main`을 깨끗한 상태에서 `origin/main`으로 fast-forward한다.
2. 커밋 SHA, 테스트 범위, live/shadow/off 게이트, 브로커·거래소 경로를 다시 기록한다.
3. 이 계획의 제품 북극성·비목표·LLM 경계·live 승인 경계를 `docs/PRODUCT_NORTH_STAR.md`로 승격한다.
4. 각 요구사항을 기존 모듈 재사용/수정/교체/신규로 분류한다.
5. 현재 라이브 주문 경로와 `POSITION_PENDING_KR_ENABLED=false` 상태를 문서화한다.
6. 자격증명 값은 읽거나 출력하지 않고 변수명과 저장 위치만 감사한다.
7. Phase 0 보고서를 사용자에게 제출하고 승인 전 구현을 시작하지 않는다.

**Verification:**

```bash
git status --short
git rev-parse HEAD
git rev-parse origin/main
python3 -m compileall -q .
# 저장소 CI와 동일한 명령을 로컬 격리 환경에서 실행
```

**Gate:** KIS primary, `SWING_V1`/`TREND_V1` 별도 전략, Telegram outbound+interactive 기준선은 승인 완료됐다. Phase 0 기준선·안전 감사 보고서 승인 전에는 소스 구현을 시작하지 않는다. 신규 거래소와 live는 1차 승인 대상이 아니다.

---

## Phase 1 — 주문 안전 척추

**Objective:** 새로운 전략·사이징·거래소보다 먼저 중복주문·부분체결·대사·킬스위치를 완성한다.

**Files:**
- Modify: `prism_core/order_intents.py`
- Modify: `prism_core/execution_service.py`
- Modify: `prism_core/positions.py`
- Create: `prism_core/reconciliation.py`
- Create: `prism_core/risk_limits.py`
- Create: `prism_core/kill_switch.py`
- Modify: `stock_tracking_agent.py`
- Modify as needed: `prism-us/us_stock_tracking_agent.py`
- Test: `tests/test_order_intents.py`
- Test: `tests/test_execution_service.py`
- Test: `tests/test_positions.py`
- Create: `tests/test_reconciliation.py`
- Create: `tests/test_risk_limits.py`
- Create: `tests/test_kill_switch.py`
- Modify: `.github/workflows/ci.yml`

**Required state machine:**

```text
PLANNED -> APPROVED -> SUBMITTED -> ACKNOWLEDGED
                              |-> PARTIALLY_FILLED -> FILLED
                              |-> CANCEL_PENDING -> CANCELLED
                              |-> REJECTED
                              |-> UNKNOWN -> RECONCILE_REQUIRED
```

**Core contract sketch:**

```python
@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    environment: Literal["paper", "live"]
    venue: str
    account_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    strategy_id: str
    decision_snapshot_id: str
    idempotency_key: str

@dataclass(frozen=True)
class FillEvent:
    broker_order_id: str
    execution_id: str
    filled_quantity: Decimal
    fill_price: Decimal
    fee: Decimal
    occurred_at: datetime
```

**TDD cases:**

1. 동일 idempotency key의 재호출은 브로커 주문을 한 번만 생성한다.
2. 주문 전송 후 응답 유실·프로세스 재시작 시 기존 주문을 조회하고 재전송하지 않는다.
3. partial→partial→filled에서 포지션은 실제 체결량만 증가한다.
4. partial→cancel에서 잔량은 포지션에 포함되지 않는다.
5. 로컬·브로커 불일치는 신규 주문을 차단하고 알림을 생성한다.
6. 킬스위치가 켜지면 모든 신규 주문이 거부된다.
7. paper 자격증명으로 live endpoint에 도달할 수 없다.

**Gate:** 위 테스트가 필수 CI에 포함되고, KR pending lifecycle의 활성/보류 결정과 운영 대사 절차가 문서화될 때까지 다음 단계의 live 연결 금지.

---

## Phase 2 — 연구·백테스트 기반

**Objective:** 매수점수·국면·정책·사이징을 실전 연결 전에 편향 없이 검증할 수 있는 연구 환경을 만든다.

**Files:**
- Create: `research/__init__.py`
- Create: `research/data_contracts.py`
- Create: `research/backtest/engine.py`
- Create: `research/backtest/fill_model.py`
- Create: `research/backtest/cost_model.py`
- Create: `research/backtest/portfolio.py`
- Create: `research/validation/walk_forward.py`
- Create: `research/validation/experiment_registry.py`
- Create: `tests/research/test_backtest_engine.py`
- Create: `tests/research/test_point_in_time_contract.py`
- Create: `tests/research/test_walk_forward.py`
- Reuse/reference: `tools/regime_backtest.py`
- Reuse/reference: `tools/rs_rating_backtest.py`
- Reuse/reference: `tools/market_pulse_backtest.py`

**Required data fields:**

```text
security_id, observed_at, available_at, ingested_at, as_of_date,
provider, provider_symbol, revision, quality_status
```

**Required controls:**

- survivorship bias와 상장폐지 처리
- `available_at <= decision_time`
- split/dividend/corporate action
- 신호 시점과 체결 시점 분리
- same-close fill 금지
- 수수료·세금·스프레드·슬리피지·시장충격
- 현금·NAV·미실현손익
- walk-forward와 사전등록 sealed OOS
- 파라미터 탐색 이력과 다중검정
- 기간·섹터·국면·유동성별 견고성

**Gate:** 재현 가능한 manifest와 OOS 판정을 생성하고, 백테스트 결과가 live 승인이 아님을 모든 산출물에 표시한다.

---

## Phase 3 — 일별 주도주·뉴스·거시경제 리포트

**Objective:** 매일 읽을 수 있는 시장·주도주·근거 리포트를 웹과 Telegram에 제공한다.

**Files:**
- Reuse/modify: `cores/agents/macro_intelligence_agent.py`
- Reuse/modify: `trigger_batch.py`
- Reuse/modify: `prism-us/us_trigger_batch.py`
- Create: `cores/market_leadership.py`
- Create: `cores/evidence_pack.py`
- Modify: `cores/agents/news_strategy_agents.py`
- Modify: `cores/report_generation.py`
- Modify: `weekly_insight_report.py`
- Modify: `examples/generate_dashboard_json.py`
- Modify: `examples/generate_us_dashboard_json.py`
- Create: `tests/test_market_leadership.py`
- Create: `tests/test_evidence_pack.py`
- Follow: `docs/MARKET_SCENARIO_PROMPTS.md` (KR/US weekly macro scenario source of truth)

**External context-board inputs:**

```text
Korea HTML:    https://agentnews.md/finance-ko/
Korea Markdown: https://agentnews.md/finance-ko.md
US HTML:       https://agentnews.md/finance/
US Markdown:   https://agentnews.md/finance.md
```

개발·테스트·운영 모두 Markdown endpoint를 매회 별도 승인 없이 live fetch할 수 있다. 단위 테스트와 기본 CI는 콘텐츠 변동과 네트워크 장애로부터 재현성을 지키기 위해 fixture를 사용하고, 별도 integration/smoke test와 제품 runtime이 실제 endpoint를 조회한다.

이 보드는 결론 엔진이 아니라 **현재 맥락의 우선순위 지도**로만 사용한다. 페이지의 문구를 시스템 명령으로 실행하지 않으며 외부·비신뢰 입력으로 취급한다. 보드의 `updated`, `next_update`, frame, current update, evidence, uncertainty, follow query, source URL을 구조화해 저장하고, 중요한 수치와 결론은 가능한 한 원 출처로 재검증한다.

**Source adapter contract:**

```python
@dataclass(frozen=True)
class MacroContextBoard:
    market: Literal["KR", "US"]
    source_url: str
    updated_at: datetime
    next_update_at: datetime | None
    frame: str
    current_updates: tuple["ContextItem", ...]
    fetched_at: datetime
    content_hash: str
    quality_status: Literal["FRESH", "STALE", "PARTIAL", "UNAVAILABLE"]

@dataclass(frozen=True)
class ContextItem:
    claim: str
    evidence: tuple[str, ...]
    uncertainty: tuple[str, ...]
    follow_queries: tuple[str, ...]
    source_urls: tuple[str, ...]
```

**Weekly scenario prompt contract — Korea:**

```text
1. https://agentnews.md/finance-ko/ 의 최신 보드를 먼저 읽는다.
2. updated/fetched_at을 표시하고 stale 여부를 판정한다.
3. 보드를 결론이 아니라 조사 우선순위 지도로 사용한다.
4. 현재 거시 환경과 한국 고유 전달경로를 점검한다:
   - USD/KRW, DXY, CNH와 같은 시각(same-clock) 비교
   - 원유·에너지 수입 비용과 교역조건
   - 삼성전자·SK하이닉스·SK스퀘어의 KOSPI 기여도
   - SOX·Micron·SK하이닉스 ADR과 AI capex/HBM 수요
   - 외국인 수급, 금리, 중국·일본 시장 휴장/정책 변수
5. 이번 주 기본/강세/약세 시나리오를 작성한다.
6. 각 시나리오에 확률이 아니라 조건·촉매·무효화 기준을 붙인다.
7. 반드시 체크할 변수, 일정, 데이터 발표, 기업 이벤트를 시간순으로 정리한다.
8. 검증된 사실/해석/불확실성/누락 데이터를 분리한다.
9. 투자 결론이나 자동주문을 만들지 않는다.
```

**Weekly scenario prompt contract — United States:**

```text
1. https://agentnews.md/finance/ 의 최신 보드를 먼저 읽는다.
2. updated/fetched_at을 표시하고 stale 여부를 판정한다.
3. 보드를 결론이 아니라 조사 우선순위 지도로 사용한다.
4. 현재 거시 환경과 미국 주식 전달경로를 점검한다:
   - Fed 경로와 2Y/5Y/10Y/30Y 금리, 2s10s·5s30s 곡선
   - 성장 재평가와 인플레이션/term-premium 구분
   - 유가·지정학 위험이 주식과 금리에 전달되는지
   - AI capex의 수요 검증과 밸류에이션·마진 부담 분리
   - SOX·Nasdaq·S&P 500 breadth와 메가캡 집중도
   - DXY·USD/JPY·개입 위험 및 글로벌 유동성
   - FOMC, PCE/CPI, 고용, 국채 입찰, 주요 실적 일정
5. 이번 주 기본/강세/약세 시나리오를 작성한다.
6. 각 시나리오에 조건·촉매·무효화 기준을 붙인다.
7. 반드시 체크할 변수와 이벤트를 시간순으로 정리한다.
8. 정규장 종가, 장후 반응, 선물 움직임을 혼합하지 않고 시점을 표시한다.
9. 검증된 사실/해석/불확실성/누락 데이터를 분리하고 자동주문을 만들지 않는다.
```

**Weekly scenario output schema:**

```text
market / week / as_of / generated_at
context_board_url / board_updated_at / freshness / content_hash
current_frame
base_scenario: conditions, transmission, beneficiaries, risks, falsifiers
bull_scenario: conditions, transmission, beneficiaries, risks, falsifiers
bear_scenario: conditions, transmission, beneficiaries, risks, falsifiers
variables_to_watch: value, direction, threshold, source, next_check_at
event_calendar: event, expected_at, affected_markets
verified_facts
interpretations
uncertainties_and_missing_data
source_urls
```

**Current-board examples that must remain hypotheses, not hardcoded truths:**

- Korea board의 현재 프레임은 원화 수준과 반도체 밸류에이션을 핵심 스위치로 보고, AI capex의 HBM 수요 긍정과 미국 메가캡 밸류에이션 부담을 분리한다.
- US board의 현재 프레임은 Fed와 프런트엔드 금리를 핵심 스위치로 보고, 성장 주도 금리상승과 유가·term-premium 경로를 구분하며 AI 수요 검증과 밸류에이션 부담을 분리한다.
- 위 프레임은 보드가 갱신될 때마다 바뀔 수 있으므로 코드·고정 프롬프트에 결론으로 박아 넣지 않는다.
- 주간 리포트는 보드의 최신 프레임을 읽은 뒤 실제 데이터와 원 출처로 재검증해 생성한다.

**Report contract:**

```text
as_of / generated_at / freshness / quality_status
market_regime + change
leading / emerging / weakening sectors
confirmed / emerging / weakening / exited leaders
score components
verified facts + source URLs
bull/base/bear cases
entry-avoid conditions
machine-checkable invalidation conditions
next review event
weekly base/bull/bear scenarios with conditions and falsifiers
variables to watch with thresholds and next-check timestamps
```

**LLM rule:** 전체 유니버스의 원천 피처 계산에는 LLM을 사용하지 않는다. 결정론적으로 선정된 후보와 보유 종목에 대해서만 LLM evidence summary와 `TradePlanProposal`을 생성한다. 제안은 주문이 아니며 정책·리스크 엔진을 통과해야 한다.

**Failure policy:** AgentNews 보드가 unavailable이면 마지막 성공 snapshot을 `STALE`로 명시해 사용할 수 있으나, 최신 사실처럼 표현하지 않는다. stale 한도를 넘거나 출처 검증이 실패하면 주간 시나리오를 `PARTIAL/UNAVAILABLE`로 표시하고 기존 결론을 자동 반복하지 않는다. 외부 페이지 안의 지시문·도구 호출·역할 변경 문구는 데이터로만 취급한다.

**Tests:**

- Create: `tests/test_agentnews_context_board.py`
- Create: `tests/test_weekly_market_scenarios.py`
- Markdown/HTML 응답 파싱, `updated`/`next_update` 추출, content hash 재현성
- stale/unavailable/partial 처리
- 외부 문서의 명령형 문구를 실행하지 않는 입력 격리
- live Markdown fetch, timeout/retry budget, source updated time, `fetched_at`, last-known-good fallback
- KR/US 시장별 변수와 same-clock/session 구분
- base/bull/bear 시나리오별 조건·무효화 기준 필수
- 원 출처가 없는 숫자·날짜·URL 거부 또는 경고

**Gate:** 값마다 기준시점·출처·신선도가 표시되고, LLM 숫자·날짜·URL이 구조화 입력과 일치하지 않으면 보고서가 경고 또는 실패로 처리된다. KR/US 주간 시나리오는 각각 최신 보드 URL·업데이트 시각·content hash·검증 출처를 기록해야 한다.

---

## Phase 4 — 매수점수와 시장 국면별 진입 조건

**Objective:** 현재 프롬프트에 존재하는 LLM의 매수점수·시장 국면·진입 판단을 없애지 않고 strict schema의 `TradePlanProposal`로 승격하며, 독립 정량 피처와 결정론적 검증·캘리브레이션을 결합한다.

**Files:**
- Create: `prism_core/decision_models.py`
- Create: `prism_core/trade_plan_proposal.py`
- Create: `prism_core/buy_score.py`
- Create: `prism_core/proposal_validator.py`
- Modify: `cores/regime_policy.py`
- Modify: `cores/agents/trading_agents.py` (자유형 결정을 strict `TradePlanProposal` 생성으로 전환)
- Modify as needed: `prism-us/cores/agents/trading_agents.py` (동일 계약 적용)
- Create: `tests/test_buy_score.py`
- Extend: `tests/test_regime_policy.py`
- Extend: `tests/test_trading_agents_prompt_rules.py`

**Contract:** LLM 점수와 독립 정량 점수를 모두 저장한다. LLM은 점수 분해·국면 분포·진입 predicate·근거·불확실성·무효화 조건을 반환하고, 코드는 필드별 `accept/clamp/recalculate/reject` 규칙을 적용한다.

```python
@dataclass(frozen=True)
class TradePlanProposal:
    proposal_id: str
    model_version: str
    prompt_version: str
    decision_snapshot_id: str
    llm_score: Decimal
    quantitative_score: Decimal
    regime_distribution: dict[str, Decimal]
    entry_predicates: tuple["Predicate", ...]
    stop_candidate: Decimal
    target_candidates: tuple[Decimal, ...]
    risk_multiplier_candidate: Decimal
    reentry_policy_candidate: "ReentryCandidate | None"
    pyramid_policy_candidate: "PyramidCandidate | None"
    evidence_ids: tuple[str, ...]
    uncertainties: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]

@dataclass(frozen=True)
class ScoreBreakdown:
    llm_total: Decimal
    quantitative_total: Decimal
    technical: Decimal
    relative_strength: Decimal
    liquidity: Decimal
    fundamental: Decimal
    catalyst: Decimal
    quality_flags: tuple[str, ...]

@dataclass(frozen=True)
class EntryDecision:
    eligible: bool
    proposal_id: str
    calibrated_score: Decimal
    regime: str
    threshold: Decimal
    vetoes: tuple[str, ...]
    clamps: tuple[str, ...]
```

**Gate:** 동일한 저장 proposal을 replay하면 같은 검증·진입 결과가 나오고, 데이터 결측·stale·스키마 실패·저신뢰이면 신규 위험이 fail-closed 된다. 모델/프롬프트 변경은 OOS·shadow·paper 캘리브레이션을 다시 통과해야 한다.

---

## Phase 5 — 손절·익절·재진입·피라미딩·사이징

**Objective:** LLM이 손절·익절·재진입·피라미딩·사이징 후보와 전략 논리를 제안하고, 결정론적 상태 머신과 리스크 예산이 실제 가격·수량·허용 여부를 확정하도록 통합한다.

**Files:**
- Create: `prism_core/position_policy.py`
- Create: `prism_core/position_sizing.py`
- Modify: `reentry_cooldown.py`
- Modify: `tools/hardstop_seller.py`
- Modify: `tools/trend_exit_seller.py`
- Modify: `stock_tracking_agent.py`
- Create: `tests/test_position_policy.py`
- Create: `tests/test_position_sizing.py`
- Extend: `tests/test_reentry_cooldown.py`
- Extend: `tests/test_sell_claim_concurrency.py`

**Policy invariants:**

- LLM stop/target/risk multiplier는 후보이며 필드별 검증 결과를 `accept/clamp/recalculate/reject`로 기록
- 실제 수량은 `계좌 위험예산 × 허용된 risk multiplier ÷ |진입가 - 확정 손절가|`를 기본으로 계산한 뒤 변동성·유동성·집중도·현금 상한 적용
- LLM은 리스크 상한을 높이거나 정책 veto를 해제할 수 없음
- 진입 후 손절을 불리한 방향으로 확대할 수 없음
- 재진입은 LLM이 새로운 촉매·논리를 제시해도 쿨다운·최대시도·누적손실 한도를 통과해야 함
- 피라미딩은 수익 중인 포지션의 명시적 add-on intent만 허용하고 총 위험 상한 적용
- actual filled quantity만 보유수량에 반영
- 피라미딩은 의도된 add-on intent로 구분하여 중복주문과 혼동하지 않음
- 최대 피라미딩·종목·섹터·계좌 노출 한도 적용
- 손실 포지션 물타기 기본 금지
- 사이징 결과가 거래소 최소수량·호가단위·가용현금을 위반하지 않음
- 손절·익절 트리거와 실제 주문은 `ExecutionService`를 우회하지 않음
- 시장 국면은 신규 노출을 조절하되 기존 장부를 임의 변경하지 않음

**Gate:** 비용·슬리피지·부분체결을 포함한 backtest와 paper에서 기존 정책 대비 위험·회전율·MDD를 비교하고, 사전등록 기준을 통과하지 못하면 승격하지 않는다.

---

## Phase 5.5 — LLM 셀프 피드백 DB와 검증된 학습 루프

**Objective:** LLM의 판단, 실제 결과, 미실행 후보의 반사실 결과, 사후 복기, 교훈 후보를 append-only로 저장한다. 1차에서는 `CANDIDATE -> SHADOW`까지만 허용하고 미래 `TradePlanProposal`에는 영향을 주지 않는다. 2차에서 충분한 OOS·shadow·broker-paper 근거가 축적된 교훈만 `PAPER_PROMOTED` 검색 컨텍스트로 사용한다. 한 번의 성공/실패나 LLM 자기평가만으로 프롬프트·점수·리스크 정책을 자동 변경하지 않는다.

**Existing components to reuse and migrate:**

- `tracking/db_schema.py::watchlist_history`
- `tracking/db_schema.py::analysis_performance_tracker`
- `tracking/db_schema.py::trading_journal`
- `tracking/db_schema.py::trading_intuitions`
- `tracking/db_schema.py::trading_principles`
- `cores/agents/trading_journal_agent.py`
- `tracking/journal.py`
- `prism-us/tracking/journal.py`

기존 `analysis_performance_tracker`의 7/14/30일 성과 추적과 `trading_journal`의 복기 기능은 유지하되, LLM 판단 provenance·반례·승격 상태·모델 버전 경계를 저장할 정규화 테이블을 추가한다.

**Files:**

- Modify: `tracking/db_schema.py`
- Create: `tracking/llm_feedback_store.py`
- Create: `tracking/llm_outcome_tracker.py`
- Create: `tracking/llm_retrospective.py`
- Create: `tracking/lesson_promotion.py`
- Modify: `cores/agents/trading_journal_agent.py`
- Modify: `tracking/journal.py`
- Modify: `prism-us/tracking/journal.py`
- Create: `tests/tracking/test_llm_feedback_store.py`
- Create: `tests/tracking/test_llm_outcome_tracker.py`
- Create: `tests/tracking/test_lesson_promotion.py`
- Create: `tests/tracking/test_feedback_retrieval.py`

**New append-only entities:**

```text
llm_decision_snapshots
  snapshot_id, market, symbol, decision_at, data_as_of, availability_as_of,
  features_json, evidence_ids_json, data_hash, created_at

llm_trade_proposals
  proposal_id, snapshot_id, model_provider, model_name, model_version,
  prompt_template_id, prompt_hash, sampling_config_json,
  retrieved_lesson_ids_json, raw_output_json, parsed_output_json,
  schema_status, validator_version, clamp_log_json, final_status, created_at

llm_proposal_outcomes
  outcome_id, proposal_id, horizon, observed_at, price_source,
  traded, realized_pnl, counterfactual_return, mfe, mae,
  stop_hit, target_hit, thesis_status, process_quality, outcome_quality,
  regime_at_decision, regime_at_observation, created_at

llm_retrospectives
  retrospective_id, proposal_id, review_type,
  process_review_json, outcome_review_json, missed_signals_json,
  overreacted_signals_json, lesson_candidate_ids_json,
  reviewer_model_version, prompt_hash, created_at

llm_lesson_candidates
  lesson_id, hypothesis, condition_dsl, action_candidate,
  scope_market, scope_regime, scope_sector, scope_timeframe,
  status, support_count, contra_count, confidence,
  formed_from_start, formed_from_end, validation_start,
  model_version_distribution_json, created_at, updated_at

llm_lesson_evidence
  lesson_id, proposal_id, outcome_id, evidence_side,
  leakage_check_status, created_at

llm_feedback_runs
  run_id, run_type, code_version, config_hash, started_at, completed_at,
  input_cutoff, result_json, status
```

**Feedback lifecycle:**

```text
TradePlanProposal 저장
  -> 거래 여부와 무관하게 시계열 outcome 추적
  -> process review와 outcome review 분리
  -> lesson candidate 생성
  -> support/contra evidence 누적
  -> SHADOW: 미래 판단에 주입하지 않고 예측만 기록
  -> PAPER_PROMOTED: paper 컨텍스트에 제한적으로 검색
  -> LIVE_ELIGIBLE: 별도 사용자 승인 범위 안에서만 검색 가능
  -> RETIRED: 반례·드리프트 증가 시 제외
```

**Two-pass retrospective:**

1. `PROCESS_REVIEW`: 결정 당시 이용 가능했던 입력·근거·정책만 보고 판단 과정의 누락, 모순, 스키마·리스크 품질을 평가한다. 미래 수익률은 보여주지 않는다.
2. `OUTCOME_REVIEW`: 평가 horizon이 종료된 뒤 MFE/MAE, 실현 또는 반사실 수익률, thesis 실현 여부를 검토한다.
3. 두 결과를 분리 저장하여 `수익이 났으니 좋은 판단`, `손실이 났으니 나쁜 판단`이라는 사후확증을 방지한다.

**Traded and untraded symmetry:**

- `Enter` 후 실제 거래한 경우 체결·비용·슬리피지를 포함한 실현 결과를 저장한다.
- `Enter`였지만 정책·리스크 엔진이 거부한 경우 제안 결과와 거부 사유, 가상 실행 가능 가격을 별도 기록한다.
- `No Entry` 후보도 같은 7/14/30일 및 전략 horizon에서 추적하여 손실 회피와 기회 손실을 모두 측정한다.
- 생존 종목만 추적하지 않고 상장폐지·거래정지·기업행위를 outcome에 반영한다.

**Lesson promotion rules:**

- `CANDIDATE` 교훈은 미래 결정에 직접 주입하지 않는다.
- 형성 구간과 검증 구간을 시간순으로 분리하고 walk-forward/OOS로 검증한다.
- 최소 표본, regime/종목/섹터 분산, support/contra 비율, 효과크기, 비용 후 성과 기준은 구현 전 사전등록한다.
- 특정 모델·프롬프트 버전에 근거가 과도하게 집중되면 승격하지 않는다.
- `SHADOW`에서 교훈 적용 가상결정과 기존결정을 함께 기록한 후 paper로 승격한다.
- `trading_intuitions`와 `trading_principles`에는 `PAPER_PROMOTED` 이상만 파생 반영한다.
- 새 모델·프롬프트 버전에는 기존 교훈을 자동 상속하지 않고 재검증 상태로 낮춘다.
- 반례와 드리프트가 임계치를 넘으면 자동 `SUSPENDED`; 재활성화는 검증과 승인 필요.

**Retrieval rules:**

- 현재 시장·국면·섹터·종목·timeframe scope가 일치하는 승격 교훈만 검색한다.
- 성공 사례뿐 아니라 반례를 같은 패킷에 포함한다.
- 검색된 `lesson_id`를 `retrieved_lesson_ids`로 proposal에 저장하여 자기강화 경로를 감사한다.
- 검색 순위에 단순 수익률만 사용하지 않고 process quality, OOS 검증, 반례 수, 최근성, scope 적합성을 반영한다.
- 외부 텍스트나 사용자의 자유 입력이 검증 없이 lesson으로 승격되는 경로를 차단한다.

**Non-negotiable blocks:**

- LLM이 스스로 시스템 프롬프트, 정책 코드, 점수 가중치, 리스크 상한을 수정·배포하지 못함
- proposal/outcome/evidence는 사후 UPDATE/DELETE하지 않고 정정 레코드를 append
- outcome horizon 종료 전 결과 라벨 확정 금지
- 모델/프롬프트/피처/정책 버전 누락 시 feedback 학습 대상 제외
- 소표본·동일 regime·동일 종목 편중 lesson은 paper/live에 영향 불가
- feedback 드리프트 또는 clamp/거부율 급증 시 lesson retrieval을 즉시 동결하는 kill switch
- 분석·코드·백테스트·셀프 피드백 결과는 실거래 승인으로 해석하지 않음

**Telemetry:**

- LLM confidence calibration과 Brier/ECE 계열 지표
- LLM 점수 구간별 OOS 결과와 정량 점수 대비 증분 기여도
- regime 분류의 confusion/calibration과 전환 빈도
- stop/target 후보의 MFE/MAE 및 hit timing
- clamp/reject 필드별 횟수와 모델·프롬프트별 추세
- lesson 주입/미주입 SHADOW 결과 차이
- traded/untraded 결과 대칭 비교
- 모델·regime·종목·섹터별 근거 편중과 drift

**Gate:** fixture DB 마이그레이션, append-only 불변식, as-of 누출 방지, traded/untraded outcome, two-pass retrospective, lesson 상태 전이, 반례 포함 retrieval, 모델 버전 격리, feedback kill switch를 테스트한다. `CANDIDATE` lesson이 paper/live 판단에 영향을 주면 실패다.

---

## Phase 6A — 1차 내부 paper ledger 및 전향적 SHADOW 검증

**Objective:** 실시간 데이터에서 실제 돈과 브로커 주문 API 없이 내부 simulated broker/ledger로 주문 상태·복구·대사·리스크 정책을 검증한다. LLM 셀프 피드백은 `SHADOW`까지만 기록하고 실제 proposal에는 반영하지 않는다.

**Files:**
- Create: `paper_trading/ledger.py`
- Create: `paper_trading/broker.py`
- Create: `paper_trading/reconciliation.py`
- Create: `tests/paper_trading/test_restart_recovery.py`
- Create: `tests/paper_trading/test_partial_fills.py`
- Create: `tests/paper_trading/test_daily_reconciliation.py`

**Gate:** `paper.sqlite`를 사용하고 브로커 자격증명·주문 호출 없이 중복주문·부분체결·UNKNOWN·재시작·대사 불일치·킬스위치 시뮬레이션을 통과한다. 이 gate가 1차 제품의 실행 범위 종료점이다.

### Phase 6B — 2차 broker paper/demo 및 `PAPER_PROMOTED`

**Objective:** KIS 모의투자 또는 별도 승인된 paper/demo 환경을 내부 ledger와 연결하여 실제 브로커 주문 수명주기·정산·대사를 검증한다.

**Required boundaries:**

- production/live 자격증명과 물리적으로 분리된 paper 자격증명
- paper 전용 DB·프로세스·feature gate
- 모든 주문에 `OrderIntent` 필수
- 브로커 포지션·현금·미체결·체결과 로컬 장부의 시작 시·주기적 대사
- 장부 불일치·데이터 stale·outcome UNKNOWN 시 신규 위험 fail-closed
- SHADOW/OOS 기준을 통과한 교훈만 `PAPER_PROMOTED`로 검색

**Gate:** 최소 수개월의 전향적 paper 운영 증거, 중복주문·부분체결·UNKNOWN·대사·재시작·킬스위치 훈련을 통과한다. 이 gate는 live를 승인하지 않는다.

---

## Phase 7 — 신규 거래소·브로커 어댑터

**Objective:** 기존 시장 구현을 복사하지 않고 하나의 어댑터로 새 거래소를 추가한다.

**Files:**
- Create after venue selection: `trading/adapters/<venue>.py`
- Create: `tests/trading/adapters/test_<venue>.py`
- Modify: `prism_core/execution_service.py`
- Modify: `.env.example` (변수명과 placeholder만)
- Modify: `docs/SETUP_ko.md`

**Required adapter interface:**

```python
class BrokerAdapter(Protocol):
    async def submit(self, intent: OrderIntent) -> BrokerOrder: ...
    async def get_order(self, broker_order_id: str) -> BrokerOrder: ...
    async def list_open_orders(self, account_id: str) -> list[BrokerOrder]: ...
    async def list_fills(self, account_id: str, since: datetime) -> list[FillEvent]: ...
    async def cancel(self, broker_order_id: str) -> BrokerOrder: ...
    async def balances(self, account_id: str) -> list[Balance]: ...
    async def positions(self, account_id: str) -> list[BrokerPosition]: ...
```

**Gate:** sandbox/demo에서 idempotency, timeout, 부분체결, 정정·취소, 재시작 대사를 통과하기 전 live endpoint를 사용할 수 없다.

---

## Phase 8 — 웹 대시보드와 Telegram 통합

**Objective:** 기존 인터페이스를 유지하면서 새 리포트·연구·위험·주문 상태를 노출한다.

**Files:**
- Modify: `examples/dashboard/types/dashboard.ts`
- Modify: `examples/dashboard/app/page.tsx`
- Create: `examples/dashboard/components/market-leadership-page.tsx`
- Create: `examples/dashboard/components/research-validation-page.tsx`
- Create: `examples/dashboard/components/risk-and-orders-page.tsx`
- Modify: `examples/generate_dashboard_json.py`
- Modify: `examples/generate_us_dashboard_json.py`
- Modify: `telegram_config.py`
- Modify: `telegram_summary_agent.py`
- Modify: `trading/portfolio_telegram_reporter.py`

**Dashboard sections:**

1. Daily Leadership
2. News & Macro Evidence
3. Score & Regime
4. LLM Feedback / Calibration / Lesson Lifecycle
5. Research / OOS Validation
6. Portfolio Risk & Sizing
7. Orders / Partial Fills / Reconciliation
8. Feature Gates / Kill Switch Status

**Telegram policy:** 요약·경고·보고서 링크를 전송하되, Telegram 메시지나 명령이 live 주문을 직접 승인하지 않는다.

주간 발행에는 한국·미국 각각 `기본/강세/약세 시나리오`, `이번 주 체크 변수`, `주요 일정`, `프레임 무효화 조건`, `기준시점·출처·신선도`를 포함한다. 메시지 길이를 넘는 상세 근거는 웹 대시보드 또는 PDF 링크로 연결한다.

---

## 6. Telegram 토큰 전환

네 소유의 봇·채널을 사용할 경우 토큰과 채널 ID 변경이 필요하다. 코드 변경보다 배포 환경의 secret 교체 작업이다.

**필요 변수:**

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_CHAT_ID
TELEGRAM_ALLOWED_USER_ID
```

1차는 봇 1개·허용된 bot DM 또는 비공개 supergroup 1개만 사용한다. `[KR DAILY]`, `[US DAILY]`, `[WEEKLY MACRO]`, `[PAPER]`, `[RISK]`, `[ERROR]`, `[RECOVERY]` prefix로 메시지 종류를 구분한다. 같은 chat에서 자연어 질문과 `/help`, `/status`, `/daily`, `/weekly`, `/symbol`, `/portfolio`, `/paper`, `/health` 읽기 전용 명령을 받는다. 별도 ops 채널은 필요하지 않으며, 보고서와 경고의 양이 한 채널을 방해할 때만 2차에서 검토한다.

순수 broadcast channel은 질문을 직접 받을 수 없으므로 단일 목적지를 유지하려면 bot DM 또는 비공개 supergroup을 사용한다. inbound update는 Mac의 단일 long-polling worker가 소비하고 chat ID와 user ID allowlist를 모두 검증한다. `TELEGRAM_CHANNEL_ID`는 기존 설정 호환 fallback으로만 지원한다. `/buy`, `/sell`, `/cancel`, `/live`, 리스크 상향·킬스위치 해제·자격증명·정책 변경은 명령으로 제공하지 않는다.

**안전한 절차:**

1. BotFather에서 새 봇을 만들거나 기존 본인 소유 봇의 토큰을 회전한다.
2. 봇을 대상 채널/그룹에 추가하고 필요한 최소 관리자 권한만 부여한다.
3. 채널 ID를 확인한다.
4. 실제 값은 로컬/서버 `.env` 또는 secret manager에만 저장한다.
5. `.env.example`에는 변수명과 placeholder만 유지한다.
6. 기존 토큰이 외부에 노출됐거나 원 소유자가 다르면 즉시 revoke한다.
7. 실제 채널에 보내지 않는 dry-run으로 payload를 먼저 확인한 뒤 단일 테스트 메시지를 승인받아 전송한다.
8. 로그·오류·Git diff에 토큰이 포함되지 않았는지 검사한다.

장애 알림 채널을 별도로 만들지는 않지만 장애 감지는 필요하다. 주 프로세스와 분리된 `launchd` watchdog가 heartbeat를 확인하고 같은 채널에 `[ERROR]`/`[RECOVERY]`를 보내며, Telegram까지 실패하면 macOS local notification과 `ops.sqlite`에 기록한다.

---

## 7. 전체 완료 조건

- 제품 북극성과 비목표가 저장소 공식 문서에 있음
- 매일 리포트가 as-of·출처·신선도·quality를 표시
- 백테스트가 PIT·상장폐지·비용·슬리피지·OOS를 처리
- LLM 전략 proposal과 정량 피처가 버전·근거·불확실성과 함께 저장되고 검증 결과가 재현 가능
- 매수점수·국면·진입·사이징·포지션 정책의 LLM 후보와 최종 정책 결정을 구분
- traded/untraded outcome, two-pass retrospective, lesson support/contra evidence가 DB에 축적
- 검증되지 않은 lesson은 SHADOW/PAPER/LIVE 판단에 영향을 주지 못함
- 모델·프롬프트 변경 시 lesson과 전략 proposal이 재검증 게이트를 통과
- 주문은 durable intent와 idempotency를 가짐
- 부분체결은 실제 체결량만 장부에 반영
- 시작 시·주기적 대사가 수행되고 불일치 시 신규 위험 차단
- global/per-venue kill switch가 테스트됨
- paper/live 자격증명·DB·프로세스가 분리됨
- 안전 핵심 테스트가 필수 CI에 포함됨
- 신규 거래소가 어댑터 계약을 따름
- 웹 대시보드와 Telegram이 유지됨
- 실거래 활성화는 별도의 명시적 사용자 승인 없이는 수행되지 않음

---

## 8. 위험과 트레이드오프

1. 실행 안전을 먼저 하면 눈에 보이는 새 리포트 기능이 늦어진다. 그러나 주문·장부 오류는 전략 오류보다 직접적인 손실 위험이 크므로 이 순서를 유지한다.
2. KR/US 전면 공통화는 보류하지만, 신규 거래소 계약만큼은 공통화해야 복제 비용과 실행 불일치를 막을 수 있다.
3. 뉴스·거시경제 LLM 판단은 매수점수·국면·진입·포지션 정책 후보에 실질적으로 참여한다. 대신 입력 snapshot, proposal, 모델·프롬프트 버전, 검증·clamp 결과와 outcome을 저장하지 않으면 재현성과 백테스트가 무너지므로 해당 proposal은 의사결정에서 제외한다.
4. 포지션 사이징 변경은 기존 all-in/all-out 계약과 대시보드·저널·실주문 간 불일치를 만들 수 있으므로 독립 모듈과 paper gate가 필요하다.
5. Telegram 토큰 교체는 코드 문제가 아니라 소유권·비밀관리·권한 문제다. 실제 값은 저장소에 기록하지 않는다.
6. 거래소 추가는 대상·자산·sandbox·idempotency 지원 여부를 확인하기 전 정확한 구현 범위를 정할 수 없다.
7. 셀프 피드백은 자동 자기개선이 아니라 검증된 교훈의 제한적 검색이다. LLM이 단일 거래 결과로 프롬프트·가중치·리스크 정책을 자동 변경하면 자기확증·데이터 누출·정책 드리프트가 발생하므로 금지한다.

---

## 9. 제품 기준선 승인 상태와 이후 gate

다음 제품 결정은 승인 완료됐다.

1. 한국 primary: KIS
2. 전략 family: 단기 스윙 `SWING_V1`과 중기 추세 `TREND_V1` 분리
3. Telegram: bot 1개·허용 대화방 1개에서 자동 보고·알림과 자연어 질문·읽기 전용 명령 병행

남은 점수 결합·위험예산·보유기간·청산·재진입·피라미딩 수치는 연구·SHADOW에서 후보를 비교하고 broker paper 전에 사전등록한다. 자격증명 변경·Telegram 실제 테스트 전송·broker 주문·live 활성화는 각각 별도 승인 없이는 수행하지 않는다.
