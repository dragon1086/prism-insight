# PRISM-INSIGHT Current-to-Target Transformation Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Every code task must be implemented on a feature branch from the then-current `origin/main`, with spec-compliance review followed by code-quality review. Do not execute broker orders, rotate credentials, commit, or push without the user's separate approval. Allowlisted Telegram test messages and public AgentNews live fetches have standing approval in development, test, and operations; they must still follow the safety contracts below.

**Goal:** 현재의 KR/US 분석·리포트·자동주문 결합 구조를 개인 Mac에서 실행되는 KIS/FMP 기반의 재현 가능한 투자 의사결정 시스템으로 전환하고, 1차에서는 실제 broker 호출 없이 `SWING_V1`/`TREND_V1`, strict LLM proposal, 연구·SHADOW·내부 paper를 제공한다.

**Architecture:** 기존 KR/US 코드를 전면 재작성하지 않고, 새 `prism_core` 계약과 `prism_app` application service를 먼저 만든 뒤 레거시 진입점을 thin wrapper로 전환한다. 데이터 snapshot → 전략별 정량 피처 → LLM `TradePlanProposal` → 결정론적 validator/policy/risk → 보고서·SHADOW·내부 paper 순서를 강제한다. Phase 1 runtime은 broker 모듈을 생성하지 않으며, Phase 2 broker paper도 `OrderIntent`/`ExecutionService`만 통과한다.

**Tech Stack:** Python 3.10+, Pydantic, SQLite(WAL), KIS market-data API, FMP, DART/KIND/KRX, SEC EDGAR, FRED/ALFRED, ECOS, OpenAI/Claude, python-telegram-bot long polling, Next.js local dashboard, launchd, pytest/GitHub Actions.

**Product source of truth:** `docs/PRODUCT_SCOPE_AND_STRATEGY.md`

---

## 0. 현재 구조와 목표 구조

### 현재 핵심 흐름

```text
trigger_batch.py / prism-us/us_trigger_batch.py
  -> 후보 선정
  -> stock_analysis_orchestrator.py / prism-us/us_stock_analysis_orchestrator.py
  -> cores/analysis.py / prism-us/cores/us_analysis.py
  -> 자유형 분석 section + 투자전략 report
  -> PDF + Telegram

stock_tracking_agent.py
  -> 대형 trading prompt
  -> buy score / Enter-No Entry / stop / target / portfolio size
  -> journal 기반 직접 score adjustment
  -> holdings/history/watchlist DB
  -> OrderIntent/ExecutionService 또는 남아 있는 직접 broker 경로
```

현재 문제:

- 분석·LLM 판단·저널·장부·주문이 큰 파일에 결합됨
- `cores/agents/trading_agents.py`가 전략 판단과 정책 숫자를 한 프롬프트에 혼합
- `ExecutionService`가 `intent=None`이면 레거시 직접 호출을 허용
- `examples/messaging/` 및 일부 test utility에 직접 broker 호출이 남음
- KR 데이터는 KRX/pykrx/MCP, US 데이터는 yfinance/SEC 등으로 분산
- FMP provider 부재
- regime·데이터 오류가 일부 fail-open
- 하나의 `stock_tracking_db.sqlite`에 연구·장부·대화·운영 데이터 혼재
- 소표본 journal/intuition이 점수를 직접 변경
- Telegram이 기본 활성·channel/subscriber/multilingual 중심이고 bot 파일이 매우 큼
- dashboard DTO가 real/sim/AI 데이터를 혼합

### 목표 핵심 흐름

```text
launchd / CLI
  -> prism_app.daily_pipeline
  -> provider adapters (KIS KR / FMP US / official evidence sources)
  -> normalized point-in-time Snapshot
  -> DataQualityGate
  -> SWING_V1 and TREND_V1 feature engines
  -> QuantScore + EvidencePacket
  -> LLM TradePlanProposal
  -> ProposalValidator (accept/clamp/recalculate/reject)
  -> PortfolioRisk + deterministic sizing
  -> research.sqlite append-only audit
  -> report/dashboard/Telegram read-only query
  -> SHADOW outcome tracking
  -> internal simulated broker in paper.sqlite
```

Phase 2 only:

```text
validated proposal
  -> paper-approved OrderIntent
  -> ExecutionService
  -> KIS broker paper/demo adapter
  -> fills/reconciliation/recovery
```

---

## 1. 재사용·수정·격리·신규 구현 분류

| 현재 컴포넌트 | 처리 | 목표 |
|---|---|---|
| `prism_core/order_intents.py` | Phase 2 재사용·강화 | 모든 broker paper intent의 durable identity |
| `prism_core/execution_service.py` | Phase 2 수정 | `intent=None` 우회 제거, broker paper only |
| `prism_core/positions.py` | 재사용·정리 | paper position ledger 기반 |
| `prism_core/exit_effects.py` | 재사용 | outbox/replay 패턴 유지 |
| `stock_analysis_orchestrator.py` | 점진적 thin wrapper | 신규 daily application service 호출 |
| `cores/analysis.py` | 보고 section 생성기로 축소 | 구조화 snapshot/evidence를 입력으로 받음 |
| `trigger_batch.py` | 후보 생성 로직 재사용 | provider/feature 계약 뒤로 이동 |
| `cores/agents/trading_agents.py` | 교체 대상 | giant prompt를 strict strategy-specific proposal prompt로 대체 |
| `stock_tracking_agent.py` | 분해·격리 | outcome tracker, legacy importer, Phase 2 executor로 분리 |
| `tracking/db_schema.py` | legacy read-only 기준 | 새 versioned schema의 source로 직접 사용하지 않음 |
| `tracking/journal.py` | legacy importer만 재사용 | 직접 score adjustment 제거, `LEGACY_UNVALIDATED`로 격리 |
| `telegram_config.py` | 수정 또는 wrapper | default OFF, one allowed chat/user, legacy fallback |
| `telegram_ai_bot.py` | legacy 격리 | 새 read-only Telegram app로 대체 |
| dashboard JSON generators | 교체·축소 | research/paper read model만 조회 |
| dashboard TypeScript types | 분리 | research/paper/real DTO를 혼합하지 않음 |
| KR KRX/pykrx 수집 | provider 내부 보조로 재사용 | KIS primary + official source normalization |
| US yfinance 수집 | 탐색/테스트 fallback으로 격리 | FMP primary |
| existing backtest scripts | 참고·fixture 재사용 | 공통 PIT engine으로 교체 |

---

# Phase 0 — 문서·기준선·안전 봉인

## Task 1: 작업 기준선과 AI 지침 갱신

**Objective:** 새 제품 기준선을 모든 코딩 에이전트가 우선하도록 만들고, 구현 전 레거시 자동주문을 기본 경로에서 제외한다.

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/CLAUDE_AGENTS.md`
- Reference: `docs/PRODUCT_SCOPE_AND_STRATEGY.md`
- Test: `tests/test_project_safety_contract.py`

**Steps:**

1. 최신 `origin/main`에서 feature branch를 만들되 새 문서를 먼저 보존한다.
2. `AGENTS.md`의 source-of-truth 순서를 제품 결정서 → AGENTS → CLAUDE → agent catalog로 변경한다.
3. 1차 broker call 금지, Telegram allowlist·test-send 계약, AgentNews live-fetch 계약, fail-closed, KIS/FMP, 전략 분리, SHADOW 한계를 추가한다.
4. `CLAUDE.md`에 CURRENT와 TARGET을 분리하고 직접 `async_buy_stock`/`async_sell_stock` 예제를 금지 예제로 이동한다.
5. `docs/CLAUDE_AGENTS.md`의 기존 agent catalog에 target role과 strict output contract를 추가한다.
6. 안전 계약 테스트를 작성해 기준 문서와 필수 금지 문구가 누락되면 실패하게 한다.

**Verification:**

```bash
pytest tests/test_project_safety_contract.py -q
```

**Gate:** 문서가 Phase 1 broker 호출을 허용하지 않고, Telegram 실제 전송은 허용 chat/user·명시적 transport enable·`[TEST]` metadata·dedupe/audit 조건에서만 허용해야 한다.

---

## Task 2: Runtime mode와 외부효과 정책 추가

**Objective:** 1차 runtime에서 broker adapter가 생성될 수 없게 하고, Telegram은 환경 이름과 무관하게 명시적 transport enable과 allowlist를 만족할 때 안전한 test send가 가능하게 한다.

**Files:**
- Create: `prism_core/runtime/__init__.py`
- Create: `prism_core/runtime/settings.py`
- Create: `prism_core/runtime/effects.py`
- Test: `tests/runtime/test_settings.py`
- Test: `tests/runtime/test_external_effects.py`

**Contract:**

```python
class ProductMode(str, Enum):
    RESEARCH = "research"
    SHADOW = "shadow"
    INTERNAL_PAPER = "internal_paper"
    BROKER_PAPER = "broker_paper"  # Phase 2

# LIVE is intentionally absent until separately approved.
```

`RuntimeSettings` must include:

- `product_mode`
- `telegram_enabled=False`
- `broker_enabled=False` for all Phase 1 modes
- `research_db_path`
- `paper_db_path`
- `ops_db_path`
- `allowed_chat_id`
- `allowed_user_id`

**Tests:**

- default mode is `RESEARCH`
- no `LIVE` enum
- broker capability request in Phase 1 raises before importing trading modules
- Telegram default is OFF
- missing allowlist blocks interactive startup
- enabled allowlisted smoke send is permitted in development, test, and operations
- test envelope includes `[TEST]`, environment, request ID, and dedupe key
- secrets are redacted in repr/logging

**Verification:**

```bash
pytest tests/runtime/ -q
```

---

## Task 3: 레거시 broker path 인벤토리와 Phase 1 deny test

**Objective:** Phase 1 entrypoints에서 직접 broker 호출을 정적으로 차단한다.

**Files:**
- Create: `tools/audit_broker_boundaries.py`
- Create: `tests/safety/test_no_phase1_broker_imports.py`
- Create: `tests/safety/test_no_direct_broker_calls.py`
- Review: `stock_tracking_agent.py`
- Review: `examples/messaging/redis_subscriber_example.py`
- Review: `examples/messaging/gcp_pubsub_subscriber_example.py`
- Review: `tests/quick_test.py`
- Review: `tests/test_async_trading.py`

**Rules:**

- Phase 1 entrypoint cannot import `trading.domestic_stock_trading` or `prism-us/trading/us_stock_trading`.
- production/app code cannot call `.async_buy_stock` or `.async_sell_stock` except inside approved Phase 2 broker adapter implementation.
- example scripts that can trade must require an explicit interactive danger confirmation and remain outside Phase 1 packaging; preferred treatment is quarantine under `legacy_dangerous_examples/` in a later approved task.
- test files that hit external broker APIs are marked integration/dangerous and excluded from normal CI.

**Verification:**

```bash
python tools/audit_broker_boundaries.py
pytest tests/safety/ -q
```

**Gate:** Phase 1 application import graph contains no broker module.

---

# Phase 1A — 데이터·저장소 기반

## Task 4: Point-in-time 데이터 계약 정의

**Objective:** 모든 provider가 같은 시점·품질·종목 identity 계약을 출력하게 한다.

**Files:**
- Create: `prism_core/data/__init__.py`
- Create: `prism_core/data/contracts.py`
- Create: `prism_core/data/provider.py`
- Test: `tests/data/test_contracts.py`

**Required models:**

- `SecurityId`
- `SymbolMapping`
- `ObservationTime(observed_at, available_at, ingested_at, as_of_date)`
- `PriceBar`
- `FundamentalObservation`
- `CorporateAction`
- `EvidenceItem`
- `DataQualityStatus(FRESH, STALE, PARTIAL, UNAVAILABLE, CONFLICT)`
- `MarketSnapshot`

**Rules:**

- provider timestamp와 internal ingestion timestamp를 분리한다.
- revised fundamental/macro data는 revision으로 append한다.
- adjusted/raw price를 혼합하지 않는다.
- 모든 record에 provider와 provider symbol을 저장한다.

**Verification:**

```bash
pytest tests/data/test_contracts.py -q
```

---

## Task 5: SQLite connection과 versioned migration 기반 추가

**Objective:** ad-hoc `ALTER TABLE/except` 대신 DB별 versioned migration과 일관된 connection policy를 만든다.

**Files:**
- Create: `prism_core/storage/__init__.py`
- Create: `prism_core/storage/database.py`
- Create: `prism_core/storage/migrations.py`
- Create: `prism_core/storage/migrations/research/001_initial.sql`
- Create: `prism_core/storage/migrations/paper/001_initial.sql`
- Create: `prism_core/storage/migrations/ops/001_initial.sql`
- Test: `tests/storage/test_database.py`
- Test: `tests/storage/test_migrations.py`

**Connection policy:**

```text
journal_mode=WAL
foreign_keys=ON
busy_timeout configured
short explicit transactions
schema_migrations table per DB
```

**DB boundaries:**

- `research.sqlite`: security master, observations, features, proposals, outcomes, retrospectives, lessons, reports
- `paper.sqlite`: strategy books, cash, orders, fills, positions, NAV
- `ops.sqlite`: job runs, heartbeats, alerts, backup records

Cross-DB references use stable UUID/ULID strings and application-level validation; cross-DB foreign keys are not assumed.

**Tests:**

- empty DB migrates to current version
- migration rerun is idempotent
- transaction rollback works
- invalid/out-of-order migration is rejected
- each DB rejects tables from another DB boundary

---

## Task 6: Legacy DB read-only manifest와 copy migration

**Objective:** `stock_tracking_db.sqlite`를 파괴하거나 즉시 분할하지 않고, table-by-table mapping과 copy verification을 제공한다.

**Files:**
- Create: `docs/LEGACY_DB_MIGRATION.md`
- Create: `prism_core/storage/legacy_manifest.py`
- Create: `tools/inspect_legacy_db.py`
- Create: `tools/migrate_legacy_readonly.py`
- Test: `tests/storage/test_legacy_migration.py`

**Rules:**

- 원본 DB는 read-only URI로 연다.
- in-place UPDATE/ALTER/DROP 금지.
- table별 destination, transform, row count, checksum을 manifest에 기록한다.
- `trading_intuitions`와 `trading_principles`는 `LEGACY_UNVALIDATED`로만 가져온다.
- legacy journal은 새 score에 영향을 주지 않는다.
- rollback은 새 DB 삭제/복원으로 처리하고 원본은 변경하지 않는다.

**Gate:** fixture migration에서 source row count, transformed row count, reject count, checksum이 재현된다.

---

## Task 7: Security master와 기업행위 저장소

**Objective:** KR/US provider symbol을 안정적인 internal security ID로 연결하고 상장·티커·기업행위를 PIT로 저장한다.

**Files:**
- Create: `prism_core/data/security_master.py`
- Create: `prism_core/data/corporate_actions.py`
- Create: `tests/data/test_security_master.py`
- Create: `tests/data/test_corporate_actions.py`

**Tests:**

- ticker rename 전후가 같은 security ID에 연결됨
- delisted security도 과거 as-of query에서 조회됨
- split/dividend effective date 전에는 adjustment가 노출되지 않음
- duplicate provider event가 idempotent하게 병합됨
- conflicting providers produce `CONFLICT`, not silent overwrite

---

## Task 7A: KR/US 시계열 주도주 보고 evidence 저장 기반

**Objective:** provider adapter를 연결하기 전에 KST 01/07/13/19 보고서에서 관찰한 KR/US 주도주·상대강도·52주 신고가·유동성·수급·모멘텀/피크·전략·판정의 시간별 상태와 변화를 재현 가능한 `REPORT_ONLY` evidence로 저장한다.

**Files:**
- Create: `prism_core/reporting/__init__.py`
- Create: `prism_core/reporting/leadership_tracking.py`
- Create: `tools/ingest_market_tracking_snapshot.py`
- Test: `tests/reporting/test_leadership_tracking.py`

**Storage and trust contract:**

- 기존 `research.sqlite`의 `market_snapshots`, `observations`, `reports`를 재사용하며 별도 DB·중복 source of truth·추가 migration을 만들지 않는다.
- 한 run은 immutable market snapshot 하나, `leadership_market_state` observation 하나, 현재 종목별 `leadership_security_state` observation 하나, generic Markdown report 하나를 원자적으로 저장한다.
- `provider=hermes_agent_report`, payload `policy_disposition=REPORT_ONLY`로 표시하며 deterministic feature나 proposal로 신뢰·승격하지 않는다.
- `market_tracking_v1`은 market, KST slot/stage, as-of/observed/available/ingested time, source path/hash/URL/evidence, quality/revision, market state/events, nullable RS windows, 52-week-high state/distance, raw liquidity, nullable flow, momentum/peak, strategy labels, decision을 strict하게 보존한다.
- naive/future/역전 timestamp, invalid slot/market/stage, non-finite value, unknown enum/field, duplicate symbol, inconsistent quality/strategy/decision, executable/order/account/price field를 fail-closed로 거부한다.
- canonical identity는 processing-only `ingested_at`을 제외하고 revision을 포함하며 datetime은 UTC instant, Decimal은 normalized scale로 정규화한다. 같은 run/revision/content의 재수집은 기존 결과를 반환하고, 같은 run/revision의 다른 content는 conflict로 원자적 거부하며, 정정은 명시적인 상위 revision으로 append한다.
- 이전 상태는 각 run의 이용 가능한 최고 revision을 먼저 선택한 뒤 같은 market에서 `(available_at, ingested_at, snapshot_id)` 순으로 바로 앞선 다른 run을 선택한다. usable current evidence의 현재 종목은 `NEW`/`MAINTAINED`, 이전에만 있던 종목은 usable·complete evidence에서만 `EXITED`다. core evidence가 unusable하면 현재·부재 종목 모두 `DATA_MISSING`으로 기록한다.
- renderer는 일반적인 run/data/market/event/change/security heading만 사용하고 source-site/menu 명칭을 노출하지 않는다. core evidence가 usable하지 않으면 경고와 fail-closed 상태를 표시하며 executable price level은 schema와 report 모두에 존재하지 않는다.
- CLI는 명시적 `--db`와 JSON file/stdin만 사용하며 legacy/user DB를 탐색하지 않는다.

**KST slot contract:**

```text
01: US intraday provisional / KR prior-close confirmed context
07: US close confirmed / KR pre-open provisional observations
13: KR intraday provisional / US post-close confirmed events
19: KR close confirmed / US premarket provisional observations
```

**Downstream reuse:** Task 20은 persistence-before-publication 및 idempotent daily use case에서 이 repository를 사용하고, Task 21은 이 strict schema/readback/renderer를 확장한다. 두 task는 leadership identity, prior-run comparison, quality mapping, 저장 table을 다시 구현하지 않는다.

**Gate:** temporary SQLite에서 schema rejection, exact idempotency, correction append, atomic conflict rollback, prior-run comparison, `DATA_MISSING`, append-only trigger, deterministic readback/render, generic headings, explicit-path CLI, zero broker dependency를 증명한다.

---

## Task 8: KIS KR market-data adapter

**Objective:** 기존 KIS/KRX 로직을 주문 코드와 분리된 한국 market-data provider로 제공한다.

**Files:**
- Create: `prism_core/data/providers/__init__.py`
- Create: `prism_core/data/providers/kis.py`
- Modify as adapter source only: `krx_data_client.py`
- Test: `tests/data/providers/test_kis_provider.py`

**Rules:**

- market-data adapter imports no order submission API.
- network response는 raw payload hash와 as-of metadata를 저장한다.
- retries are bounded and observable.
- missing/stale values return quality events, not fabricated fallback.
- KRX/DART/KIND supplements retain explicit provider labels.

---

## Task 9: FMP US adapter

**Objective:** yfinance 중심 US 경로를 FMP primary contract로 교체한다.

**Files:**
- Create: `prism_core/data/providers/fmp.py`
- Create: `prism_core/data/providers/fmp_models.py`
- Test: `tests/data/providers/test_fmp_provider.py`
- Modify later as callers migrate: `prism-us/cores/us_data_client.py`
- Modify later as callers migrate: `prism-us/cores/us_analysis.py`

**Rules:**

- API key는 runtime secret에서만 읽고 repr/log에 노출하지 않는다.
- plan entitlement/capability probe를 구현한다.
- rate-limit, timeout, partial response를 구분한다.
- FMP corporate actions와 SEC official evidence를 별도 provenance로 저장한다.
- yfinance fallback은 research fixture/explicit fallback 모드에서만 사용하며 primary를 조용히 덮지 않는다.

**Tests:**

- fixture response normalization
- pagination
- 429 retry budget
- stale/partial response
- symbol mapping
- split/dividend events
- secret redaction

---

## Task 9A: AgentNews KR/US live context adapter

**Objective:** AgentNews 한국·미국 Markdown 보드를 공개 읽기 전용 현재 맥락 입력으로 실시간 수집하고 재현 가능한 snapshot으로 저장한다.

**Files:**
- Create: `prism_core/data/providers/agentnews.py`
- Create: `prism_core/data/providers/agentnews_models.py`
- Create: `tests/data/providers/test_agentnews_provider.py`
- Create: `tests/integration/test_agentnews_live.py`

**Rules:**

- KR은 `https://agentnews.md/finance-ko.md`, US는 `https://agentnews.md/finance.md`를 우선한다.
- 개발·테스트·운영 모두 live fetch가 허용되며 매회 별도 승인을 요구하지 않는다.
- raw body, URL, `fetched_at`, source updated time, content hash, freshness 상태를 저장한다.
- timeout과 retry는 bounded이며 실패 시 last-known-good snapshot을 명시적 `STALE`로 제공한다.
- 보드는 주문 신호나 최종 사실 원천이 아니라 조사 우선순위 지도다.
- 외부 콘텐츠의 명령형 문구는 untrusted data로만 처리하고 실행하지 않는다.
- 중요한 주장·수치·날짜는 원 출처로 재검증한다.

**Tests:**

- unit: fixture parsing, content hash, freshness, malformed/partial response, injection isolation
- integration: 실제 HTTPS fetch, timestamp/schema validation, timeout budget
- 기본 CI unit path는 network 없이 재현 가능하게 유지하고 live test를 별도 marker로 실행한다.

---

## Task 10: DataQualityGate를 fail-closed로 연결

**Objective:** 결측·stale·conflict 데이터가 LLM proposal이나 SHADOW outcome을 오염시키지 않게 한다.

**Files:**
- Create: `prism_core/data/quality.py`
- Modify: `cores/regime_policy.py`
- Test: `tests/data/test_quality_gate.py`
- Extend: `tests/test_regime_policy.py` or current equivalent

**Contract:**

```python
@dataclass(frozen=True)
class QualityDecision:
    disposition: Literal["ACCEPT", "REPORT_ONLY", "REJECT"]
    reasons: tuple[str, ...]
    missing_fields: tuple[str, ...]
    stale_fields: tuple[str, ...]
```

- core price/regime/calendar/evidence stale → no new proposal
- report-only fields missing → report with visible warning
- quality failure creates an append-only skip record
- no full-size or normal-entry fallback on exception

---

# Phase 1B — 전략·LLM·정책

## Task 11: 전략 contract와 registry

**Objective:** SWING_V1과 TREND_V1을 별도 전략 identity·피처·평가 horizon으로 운영한다.

**Files:**
- Create: `prism_core/strategies/__init__.py`
- Create: `prism_core/strategies/contracts.py`
- Create: `prism_core/strategies/registry.py`
- Create: `prism_core/strategies/swing.py`
- Create: `prism_core/strategies/trend.py`
- Test: `tests/strategies/test_registry.py`
- Test: `tests/strategies/test_swing.py`
- Test: `tests/strategies/test_trend.py`

**Models:**

- `StrategyId`
- `StrategyVersion`
- `FeatureSnapshot`
- `QuantScoreBreakdown`
- `EntryTemplate`
- `OutcomeHorizon`

**Rules:**

- strategy + market가 proposal identity에 포함됨
- SWING/TREND feature와 threshold가 서로 공유되지 않음
- lesson scope default는 strategy-specific
- same symbol의 두 전략 proposal은 허용하지만 portfolio exposure는 나중에 합산

---

## Task 12: Quant feature service

**Objective:** LLM이 계산해야 할 숫자와 코드가 계산해야 할 숫자를 분리한다.

**Files:**
- Create: `prism_core/features/__init__.py`
- Create: `prism_core/features/service.py`
- Create: `prism_core/features/technical.py`
- Create: `prism_core/features/fundamental.py`
- Create: `prism_core/features/liquidity.py`
- Create: `prism_core/features/regime.py`
- Test: `tests/features/`
- Reuse from: `trigger_batch.py`
- Reuse from: `cores/market_pulse.py`

**Output:** immutable `FeatureSnapshot` with data snapshot IDs and version.

**Gate:** 같은 snapshot과 feature version은 byte-stable normalized output을 생성한다.

---

## Task 13: strict `TradePlanProposal` schema

**Objective:** free-text Enter/No Entry scenario를 검증 가능한 구조화 proposal로 교체한다.

**Files:**
- Create: `prism_core/llm/trade_plan.py`
- Create: `prism_core/llm/proposal_service.py`
- Create: `tests/llm/test_trade_plan_schema.py`
- Create: `tests/llm/test_proposal_service.py`
- Modify later: `cores/agents/trading_agents.py`
- Modify later: `prism-us/cores/agents/trading_agents.py`

**Required fields:**

- proposal ID, strategy ID/version, market, security ID
- decision as proposal, not execution approval
- `llm_score` and score breakdown
- regime distribution, confidence, drivers, falsifiers
- machine-readable entry predicates
- stop/target candidates
- risk multiplier candidate
- re-entry/pyramiding candidates
- bull/bear evidence IDs
- missing/stale data declarations
- model/prompt/sampling versions

**Tests:**

- malformed JSON rejected
- unknown field policy explicit
- scores and probabilities bounded
- probabilities normalized or rejected
- predicate operators allowlisted
- missing evidence rejected or report-only
- raw response retained even when parsing fails

---

## Task 14: Giant trading prompt를 strategy-specific prompt로 분해

**Objective:** `cores/agents/trading_agents.py`의 all-in/all-out·no-partial-fill·즉시 Enter 강제를 제거하고 전략별 proposal 생성만 담당하게 한다.

**Files:**
- Create: `cores/agents/trade_plan_prompts.py`
- Modify: `cores/agents/trading_agents.py`
- Modify: `prism-us/cores/agents/trading_agents.py`
- Test: `tests/agents/test_trade_plan_prompt_contract.py`

**Prompt rules:**

- SWING_V1/TREND_V1 prompt 분리
- current snapshot 외 사실을 확정하지 않음
- partial fill/order size/order approval을 언급하지 않음
- specific uncertainty와 falsifier 필수
- all numeric claims reference evidence or features
- no direct portfolio slot or quantity output

---

## Task 15: Proposal validator와 field disposition

**Objective:** LLM output을 필드별로 accept/clamp/recalculate/reject하고 감사 로그를 남긴다.

**Files:**
- Create: `prism_core/policy/__init__.py`
- Create: `prism_core/policy/proposal_validator.py`
- Create: `prism_core/policy/dispositions.py`
- Test: `tests/policy/test_proposal_validator.py`

**Validation:**

- data snapshot freshness
- evidence existence
- regime/score consistency
- predicate evaluability
- stop sanity
- risk multiplier bounds
- strategy/market compatibility
- policy hard veto

No accepted proposal directly creates `OrderIntent` in Phase 1.

---

## Task 16: Position policy, sizing, and consolidated exposure

**Objective:** strategy별 virtual book을 유지하면서 실제/가상 총노출은 하나의 결정론적 portfolio policy로 제한한다.

**Files:**
- Create: `prism_core/policy/position_policy.py`
- Create: `prism_core/policy/sizing.py`
- Create: `prism_core/portfolio/__init__.py`
- Create: `prism_core/portfolio/models.py`
- Create: `prism_core/portfolio/risk.py`
- Test: `tests/policy/test_position_policy.py`
- Test: `tests/policy/test_sizing.py`
- Test: `tests/portfolio/test_consolidated_exposure.py`

**Rules:**

- actual quantity is deterministic
- stop widening prohibited
- loss-position averaging down prohibited
- pyramiding only on profitable strategy position candidate
- symbol/sector/market/currency/open-order exposure aggregated across strategies
- LLM can reduce via multiplier but cannot raise configured maximum

---

# Phase 1C — 연구·백테스트·피드백

## Task 17: PIT research engine

**Objective:** 현재의 개별 backtest script를 survivorship·cost·PIT aware engine으로 대체한다.

**Files:**
- Create: `prism_core/research/__init__.py`
- Create: `prism_core/research/backtest.py`
- Create: `prism_core/research/portfolio.py`
- Create: `prism_core/research/costs.py`
- Create: `prism_core/research/experiment_registry.py`
- Test: `tests/research/`
- Reference: `tools/rs_rating_backtest.py`
- Reference: `tools/regime_backtest.py`

**Required behavior:**

- point-in-time universe
- delisting and corporate actions
- no same-close fill
- fees/tax/spread/slippage
- cash/NAV/unrealized PnL
- separate SWING/TREND books plus consolidated portfolio
- walk-forward and sealed OOS
- config hash, data snapshot, code SHA

**Gate:** future-data trap fixture must fail the test; today's-constituents-only fixture must be rejected as performance evidence.

---

## Task 18: Proposal/outcome append-only storage

**Objective:** 모든 proposal과 미실행 후보를 전략별로 추적한다.

**Files:**
- Create: `prism_core/feedback/__init__.py`
- Create: `prism_core/feedback/repository.py`
- Create: `prism_core/feedback/outcomes.py`
- Test: `tests/feedback/test_repository.py`
- Test: `tests/feedback/test_outcomes.py`

**Tables:**

- `decision_snapshots`
- `trade_plan_proposals`
- `proposal_dispositions`
- `proposal_outcomes`
- `retrospectives`
- `lesson_candidates`
- `lesson_evidence`
- `feedback_runs`

**Rules:** append corrections, no destructive update/delete of evidence.

---

## Task 19: Two-pass retrospective와 SHADOW-only lessons

**Objective:** process review와 outcome review를 분리하고 검증되지 않은 교훈이 미래 점수에 영향을 주지 않게 한다.

**Files:**
- Create: `prism_core/feedback/retrospective.py`
- Create: `prism_core/feedback/lessons.py`
- Create: `prism_core/feedback/retrieval.py`
- Modify: `tracking/journal.py`
- Test: `tests/feedback/test_retrospective.py`
- Test: `tests/feedback/test_lesson_lifecycle.py`
- Test: `tests/feedback/test_legacy_lessons_blocked.py`

**Phase 1 states:**

```text
LEGACY_UNVALIDATED
CANDIDATE
SHADOW
SUSPENDED
RETIRED
```

`PAPER_PROMOTED`는 Phase 2 전까지 활성화하지 않는다.

---

# Phase 1D — application, reports, Telegram, dashboard

## Task 20: Thin application services

**Objective:** 1,400줄 orchestrator와 stock tracker를 직접 확장하지 않고 새 use-case service를 만든다.

**Files:**
- Create: `prism_app/__init__.py`
- Create: `prism_app/daily_pipeline.py`
- Create: `prism_app/query_service.py`
- Create: `prism_app/report_service.py`
- Create: `prism_app/outcome_tracker.py`
- Test: `tests/app/test_daily_pipeline.py`
- Test: `tests/app/test_query_service.py`
- Modify after parity: `stock_analysis_orchestrator.py`
- Modify after parity: `prism-us/us_stock_analysis_orchestrator.py`

**Pipeline contract:**

- no broker dependencies
- explicit runtime settings
- provider snapshot and quality decision
- both strategy families evaluated independently
- outputs saved before publication
- publication failure does not erase analysis
- idempotent job key by market/date/run type
- Task 7A `LeadershipRepository`를 호출해 보고 evidence를 publication 전에 저장하고 같은 identity/comparison 규칙을 재사용한다.

---

## Task 21: 일별·주간 report read model

**Objective:** 파일·자유형 report 대신 dashboard/Telegram이 공통 구조화 결과를 읽게 한다.

**Files:**
- Create: `prism_core/reporting/models.py`
- Create: `prism_core/reporting/daily.py`
- Create: `prism_core/reporting/weekly.py`
- Test: `tests/reporting/test_daily_report.py`
- Test: `tests/reporting/test_weekly_report.py`
- Reference: `docs/MARKET_SCENARIO_PROMPTS.md`

**Output must show:**

- as-of/source/quality
- KR/US market regime
- leading sectors/stocks
- SWING/TREND proposal differences
- bull/bear evidence and falsifiers
- SHADOW status
- no language that implies live approval
- Task 7A `market_tracking_v1` readback와 generic renderer를 daily leadership 기반으로 재사용하며 별도 leadership table·identity·change classifier를 만들지 않는다.

---

## Task 22: Telegram config/auth/publisher

**Objective:** 봇 1개와 허용 대화방 1개에서 fake-by-default unit path, 환경 독립 allowlisted live test send, read-only interaction을 제공한다.

**Files:**
- Create: `prism_core/telegram/__init__.py`
- Create: `prism_core/telegram/config.py`
- Create: `prism_core/telegram/auth.py`
- Create: `prism_core/telegram/publisher.py`
- Test: `tests/telegram/test_config.py`
- Test: `tests/telegram/test_auth.py`
- Test: `tests/telegram/test_publisher.py`
- Modify for legacy fallback: `telegram_config.py`

**Tests:**

- default disabled
- chat and user must both match
- token redacted
- legacy `TELEGRAM_CHANNEL_ID` fallback warning
- dry-run serializes but does not call Bot API
- duplicate report job does not double-send
- live smoke test may send one `[TEST]` message to the allowlisted chat in any environment
- live smoke payload includes environment, request ID, and dedupe key and writes an audit record

---

## Task 23: Read-only Telegram conversational app

**Objective:** 기존 3,800줄 bot를 확장하지 않고 별도의 작은 allowlisted long-polling app를 만든다.

**Files:**
- Create: `prism_app/telegram_bot.py`
- Create: `prism_core/telegram/commands.py`
- Test: `tests/telegram/test_commands.py`
- Test: `tests/telegram/test_prompt_injection_boundary.py`
- Keep isolated: `telegram_ai_bot.py`

**Allowlist commands:**

```text
/help /status /daily /weekly /symbol /portfolio /paper /health
```

**Deny:**

```text
/buy /sell /cancel /live
risk increase, kill-switch disable, credentials, policy/prompt mutation
```

Natural-language answers query only stored snapshots/reports/evidence via `QueryService`; raw user text cannot select tools that mutate state.

---

## Task 24: launchd jobs, watchdog, and ops DB

**Objective:** Mac sleep/restart/failure를 견디는 local batch operations를 제공한다.

**Files:**
- Create: `ops/launchd/com.prism.daily.plist.template`
- Create: `ops/launchd/com.prism.telegram.plist.template`
- Create: `ops/launchd/com.prism.watchdog.plist.template`
- Create: `prism_app/watchdog.py`
- Create: `prism_core/ops/job_runs.py`
- Test: `tests/ops/test_job_idempotency.py`
- Test: `tests/ops/test_watchdog.py`

**Rules:**

- wake-after-missed-run catch-up policy
- one owner/lease per job key
- heartbeat and last success
- same Telegram chat for ERROR/RECOVERY
- Telegram failure falls back to macOS notification + ops DB

---

## Task 25: Dashboard data contract 분리

**Objective:** real/sim/AI가 혼합된 frontend type과 KIS real-account fetch를 Phase 1에서 제거한다.

**Files:**
- Create: `examples/dashboard/types/research.ts`
- Create: `examples/dashboard/types/paper.ts`
- Create: `examples/dashboard/types/ops.ts`
- Modify: `examples/dashboard/types/dashboard.ts`
- Create: `prism_app/dashboard_export.py`
- Test: `tests/dashboard/test_export.py`
- Modify or replace: `examples/generate_dashboard_json.py`
- Modify or replace: `examples/generate_us_dashboard_json.py`

**Phase 1 dashboard sections:**

- Data Freshness / Jobs
- KR/US Daily Leaders
- SWING_V1 Proposals
- TREND_V1 Proposals
- Scenario/Evidence/Falsifiers
- Research/OOS
- SHADOW Feedback
- Internal Paper
- No real-account panel

Bind to `127.0.0.1` only.

---

# Phase 1E — 내부 paper

## Task 26: deterministic simulated broker

**Objective:** 외부 broker 없이 주문 수명주기와 부분체결을 테스트한다.

**Files:**
- Create: `prism_core/paper/__init__.py`
- Create: `prism_core/paper/models.py`
- Create: `prism_core/paper/broker.py`
- Create: `prism_core/paper/ledger.py`
- Create: `prism_core/paper/reconciliation.py`
- Test: `tests/paper/test_broker.py`
- Test: `tests/paper/test_partial_fills.py`
- Test: `tests/paper/test_restart_recovery.py`
- Test: `tests/paper/test_reconciliation.py`

**Simulated states:**

```text
CREATED -> ACCEPTED -> PARTIALLY_FILLED -> FILLED
                         |               -> CANCELED
                         -> REJECTED
                         -> UNKNOWN
```

**Gate:** internal paper imports no KIS trading modules and uses `paper.sqlite` only.

---

## Task 27: Phase 1 end-to-end acceptance

**Objective:** 사용자의 1차 목표가 코드 경계와 실제 실행 결과로 증명되게 한다.

**Files:**
- Create: `tests/e2e/test_phase1_daily_pipeline.py`
- Create: `tests/e2e/test_phase1_no_external_effects.py`
- Create: `tests/e2e/test_phase1_strategy_separation.py`
- Create: `tests/e2e/test_phase1_feedback_shadow_only.py`

**Acceptance scenarios:**

1. KIS/FMP fixture로 KR/US daily report 생성
2. 동일 종목에 SWING/TREND proposal 별도 생성
3. consolidated exposure가 중복 위험 제한
4. stale FMP data에서 proposal 차단
5. malformed LLM output이 policy로 넘어가지 않음
6. no-entry 후보도 outcome 생성
7. legacy lesson이 score를 바꾸지 않음
8. Telegram disabled 상태에서 네트워크 호출 0
9. Telegram dry-run output 생성
10. broker imports/calls 0
11. internal paper partial fill/restart/reconcile 통과
12. dashboard export contains no real-account data

---

# Phase 2 — KIS broker paper/demo

## Task 28: `ExecutionService` hard boundary

**Objective:** 모든 broker paper 주문이 durable `OrderIntent`를 요구하게 한다.

**Files:**
- Modify: `prism_core/execution_service.py`
- Modify: `prism_core/order_intents.py`
- Create: `tests/execution/test_intent_required.py`
- Extend: `tests/test_execution_service.py`
- Extend: `tests/test_order_intents.py`

**Required change:**

```python
if intent is None:
    raise MissingOrderIntent(...)
```

No behavior-preserving direct delegation remains in approved application paths.

---

## Task 29: KIS broker-paper adapter

**Objective:** KIS 모의투자를 paper-only capability로 연결한다.

**Files:**
- Create: `prism_core/brokers/__init__.py`
- Create: `prism_core/brokers/protocol.py`
- Create: `prism_core/brokers/kis_paper.py`
- Test: `tests/brokers/test_kis_paper_adapter.py`

**Rules:**

- paper credentials isolated
- live endpoint/account rejected
- intent idempotency preserved
- broker order ID and fills normalized
- timeout classified UNKNOWN
- no automatic retry after unknown outcome

---

## Task 30: Broker reconciliation and `PAPER_PROMOTED`

**Objective:** broker paper 장부가 안정된 후에만 검증된 lesson을 paper proposal에 제한적으로 사용한다.

**Files:**
- Create: `prism_core/brokers/reconciliation.py`
- Modify: `prism_core/feedback/lessons.py`
- Test: `tests/brokers/test_reconciliation.py`
- Test: `tests/feedback/test_paper_promotion.py`

**Gate:** 수개월의 전향적 증거와 kill-switch drill 전에는 live 관련 모드·명령·adapter를 추가하지 않는다.

---

# CI 및 검증 전략

## Task 31: 테스트 계층 정리

**Objective:** focused CI가 새 안전 경계를 항상 검증하고 optional/integration test를 명확히 분리한다.

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create/modify: `pytest.ini`
- Create: `requirements-test.txt` or equivalent locked test dependency file

**Test groups:**

```text
unit: no network, no secrets, fake Telegram transport
contract: provider fixtures, LLM schema
safety: no broker effects; Telegram requires enable + allowlist + dedupe/audit
integration_local: SQLite and app pipeline
integration_external: AgentNews live fetch and allowlisted Telegram smoke send permitted; other providers remain capability-gated
broker_paper: Phase 2 only
live: nonexistent until separately approved
```

**Required CI commands:**

```bash
python -m compileall -q prism_core prism_app
pytest tests/runtime tests/safety tests/data tests/storage -q
pytest tests/strategies tests/features tests/llm tests/policy -q
pytest tests/research tests/feedback tests/reporting -q
pytest tests/telegram tests/dashboard tests/ops -q
pytest tests/paper tests/e2e -q
```

---

# 마이그레이션 순서와 금지사항

## 필수 순서

```text
runtime safety gates
-> data contracts
-> storage/migrations
-> KIS/FMP adapters + security master + quality
-> strategy contracts/features
-> LLM proposal
-> deterministic validator/risk
-> research/backtest
-> feedback SHADOW
-> application/reporting
-> Telegram/dashboard
-> internal paper
-> Phase 2 broker paper
```

## 금지

- 레거시 DB in-place 분할
- yfinance 기반 전략을 먼저 만들고 FMP를 나중에 끼우기
- journal/intuition score adjustment를 새 feedback에 그대로 승계
- `stock_tracking_agent.py`에 새 책임 계속 추가
- giant Telegram bot에 새 command 계속 추가
- dashboard backend만 바꾸고 real/sim/AI 타입 혼합 유지
- fail-open 데이터로 append-only outcome 생성
- Phase 1에서 demo라는 이유로 broker API 호출
- Telegram 자연어 질문을 state-changing command로 해석
- 백테스트 결과로 live 자동 활성화

---

# 완료 정의

## 1차 완료

- KIS KR/FMP US normalized snapshot
- SWING_V1/TREND_V1 별도 proposal·성과·lesson
- strict LLM schema와 policy disposition
- PIT/cost/OOS research
- CANDIDATE→SHADOW feedback
- local dashboard
- one allowlisted Telegram chat with outbound + read-only interaction
- internal simulated paper
- broker imports/calls 0
- live mode/codepath 0

## 2차 완료

- KIS broker paper credentials/DB/process isolation
- mandatory OrderIntent
- partial fill/UNKNOWN/restart/reconciliation
- PAPER_PROMOTED lessons
- kill-switch drills
- live remains separately unapproved

---

# 주요 위험과 대응

1. **레거시 DB 결합:** copy-only manifest migration과 원본 보존.
2. **숨은 broker 호출:** AST/static audit와 Phase 1 import graph test.
3. **데이터 계약 재작업:** FMP/KIS provider contract를 전략보다 먼저 구현.
4. **LLM 리스크 잠식:** proposal schema와 deterministic field disposition 분리.
5. **자기확증 feedback:** legacy lessons 격리, SHADOW-only, support/contra 저장.
6. **전략 중복 노출:** separate books + consolidated risk.
7. **Telegram side effect:** default OFF, chat/user allowlist, read-only QueryService.
8. **Mac sleep/restart:** launchd catch-up, idempotent job keys, watchdog.
9. **대시보드 실계좌 누출:** Phase 1 DTO에서 real-account fields 제거.
10. **전면 리팩터링 위험:** 새 application service를 만든 후 레거시 entrypoint를 thin wrapper로 전환.
