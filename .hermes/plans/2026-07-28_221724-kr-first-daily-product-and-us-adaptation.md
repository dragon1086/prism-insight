# KR Daily Product Completion Plan (US Deferred)

> **For Hermes:** Implement this plan task-by-task with TDD, real-data product proof, and independent read-only Claude review at consequential finance/data-policy gates. Work on KR only. US implementation is explicitly deferred. Do not create a parallel product.

**Goal:** Connect the existing PRISM candidate-selection surface to the proven scenario kernel so one real Korean daily-close product analyzes every unique discovered candidate, produces separate SWING/TREND scenarios, deterministic validation, persistence, existing report/dashboard output, outcome feedback, and next-run SHADOW retrieval.

**Architecture:** Preserve the existing PRISM candidate selector, report, dashboard, and utilities. Add thin candidate/batch contracts and KR adapters using KIS plus DART/KIND and approved supplemental leadership inputs. Deduplicate the union by stable security identity, but impose no candidate-count or analysis-count cap: every unique discovered candidate proceeds to analysis unless a deterministic eligibility or data-quality state prevents it. The existing `DailyPipeline`, `ScenarioInputPack`, `StructuredLLMStrategyEvaluator`, `ProposalValidator`, SQLite stores, SHADOW renderer, and dashboard remain the single product kernel.

**Tech Stack:** Python 3.10–3.12, Pydantic, Decimal, SQLite, KIS/FMP/AgentNews adapters, existing ChatGPT OAuth proxy, existing Next.js dashboard, pytest.

---

## 1. Final product boundary

The KR daily-close product must become one connected flow:

```text
KR market context and source clocks
→ existing PRISM core candidates
+ supplemental leadership candidates
→ complete deduplicated candidate set
→ KIS price/history and benchmark alignment
→ DART/KIND/KIS fundamentals, filings, events
→ AgentNews market context
→ approved supplemental leadership evidence
→ separate SWING_V1 and TREND_V1 ScenarioInputPack
→ deterministic quant score
→ tool-free ChatGPT OAuth TradePlanProposal
→ ProposalValidator and scenario-completeness assessment
→ research.sqlite + ops.sqlite + internal-paper foundation
→ existing report and dashboard
→ outcome/process+outcome feedback
→ next-run SHADOW lesson retrieval
```

US implementation is out of scope for this delivery. Keep contracts market-neutral where that does not add speculative abstraction, but do not create or modify US adapters, US candidate flows, or US runtime paths in this plan.

### Non-negotiable boundaries

- Phase 1 remains SHADOW/read-only.
- No account, balance, holdings, broker, order, cancel/replace, quantity, or live capability.
- No schedule, Telegram/Discord automation, launchd, or cron activation before explicit user UAT approval.
- Fixture/synthetic tests may protect deterministic unit behavior, but no milestone may be accepted or merged from fixture-only evidence. Every runtime/provider/application milestone requires timestamped real KR data proof through the actual product seam.
- Missing/stale/conflicting core data is `DATA_UNAVAILABLE` or `ANALYSIS_INCOMPLETE`, never an invented investment `NO_ENTRY`.
- `NO_ENTRY` is complete only when required data is present and observed values fail explicit strategy predicates.
- Preserve `WATCH`, `NO_ENTRY`, `ENTRY_CANDIDATE`, `REPORT_ONLY`, and error states distinctly.
- StockEasy integration may use only an approved UI/export path after terms/permission verification. Never extract cookies, reverse-engineer internal APIs, bypass access controls, or collect account/payment/private profile areas.
- Final reports must not expose StockEasy menu names. Render generic sections such as market breadth/flows, leading groups, relative strength/highs, turnover, momentum, and peak state.
- Existing code and user surfaces are donors; do not create a second report/dashboard/product.

---

## 2. What already works and must be reused

- Real KIS market-data-only reads with timing, quality, request evidence, and fail-closed behavior.
- Actual AgentNews KR/US live fetch with source clocks and hashes.
- Actual FMP evidence where provider coverage exists.
- Existing ChatGPT OAuth proxy with `gpt-5.6-sol`, zero tools, and no separate API key.
- Separate `SWING_V1` and `TREND_V1` features, prompts, proposals, validator decisions, books, and feedback identity.
- Strict `ScenarioInputPack`, `TradePlanProposal`, `ProposalValidator`, and `scenario_completeness` contracts.
- SQLite proposal/disposition/report/job persistence, exact replay/idempotency, lease/single-runner controls, and lossless report readback.
- Existing Markdown report and `prism_dashboard_v1` export.
- Existing legacy KR trigger/candidate selection logic and report utilities.
- US assets are intentionally not touched in this KR-only delivery.

---

## 3. Shared contracts to build once

### Task 1: Define market-neutral candidate contracts

**Objective:** Represent core and supplemental candidates without importing legacy DataFrames into the product kernel.

**Files:**
- Create: `prism_core/candidates/contracts.py`
- Create: `prism_core/candidates/__init__.py`
- Test: `tests/candidates/test_candidate_contracts.py`

**Required contracts:**

```python
class CandidateChannel(str, Enum):
    CORE_PRISM = "CORE_PRISM"
    SUPPLEMENTAL_LEADERSHIP = "SUPPLEMENTAL_LEADERSHIP"

class CandidateStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    REPORT_ONLY = "REPORT_ONLY"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"

class CandidateSnapshot(BaseModel):
    market: Market
    security_id: SecurityId
    provider_symbol: str
    display_name: str
    channel: CandidateChannel
    source_id: str
    source_snapshot_id: str
    observed_at: datetime
    available_at: datetime
    ingested_at: datetime
    trigger_ids: tuple[str, ...]
    raw_scores: Mapping[str, Decimal]
    evidence_ids: tuple[str, ...]
    status: CandidateStatus
    issues: tuple[str, ...] = ()
```

**Rules:**
- Stable market/security/source identity is mandatory.
- No order, account, quantity, or execution fields.
- Core and supplemental channels remain visible after dedupe.
- Preserve raw trigger scores; do not pretend per-trigger normalized scores are one global rank.
- Candidate timestamps must be timezone-aware and PIT-valid.

**TDD:**
1. Add failing identity, duplicate-source, naive-time, forbidden-field, and stable-serialization tests.
2. Run `python -m pytest tests/candidates/test_candidate_contracts.py -q`; expect RED.
3. Implement minimal immutable contracts.
4. Re-run; expect PASS.

---

### Task 2: Add deterministic candidate-set reconciliation

**Objective:** Merge existing PRISM candidates and supplemental leadership candidates into one complete, explainable set.

**Files:**
- Create: `prism_core/candidates/reconcile.py`
- Test: `tests/candidates/test_candidate_reconcile.py`

**Required behavior:**
- Deduplicate by `market + security_id`, preserving every source/trigger/channel.
- Analyze every unique eligible candidate produced by either channel; there is no candidate-count, analysis-count, or LLM-call-count cap.
- Never replace a core candidate silently with a supplemental candidate.
- Do not globally compare scores normalized inside different legacy triggers.
- Produce explicit `included`, `excluded`, and `exclusion_reason` records.
- Deterministic order: channel allocation first, then versioned comparable ranking fields, then stable security identity.
- Empty supplemental data must not erase core candidates.
- One malformed candidate must fail that record, not the whole daily run.

**TDD:**
- Core-only, supplemental-only, overlap, duplicate ticker aliases, uncapped multi-candidate input, deterministic ordering, and partial-source cases.

---

### Task 3: Define one shared market-context contract

**Objective:** Ensure candidate selection and scenario evaluation use the exact same market regime and source clocks.

**Files:**
- Create: `prism_core/market/context.py`
- Create: `prism_core/market/__init__.py`
- Test: `tests/market/test_market_context.py`

**Contract must contain:**
- Market/session identity and as-of boundary.
- Index state, breadth, investor flows, FX/rates/volatility where available.
- Sector/group leadership and concentration.
- Deterministic regime enum and versioned regime features.
- Evidence IDs, source timestamps, quality, conflicts, and missing fields.
- No LLM-derived values inside deterministic observations.

**Regime requirement:**
- The legacy candidate selector must no longer receive `macro_context=None` when a market context exists.
- Unknown regime stays unknown and makes new proposals ineligible; it must not default silently to `sideways` for action scoring.

---

### Task 4: Add a fault-isolated multi-candidate scenario orchestrator

**Objective:** Run each candidate through the existing product kernel without one provider failure aborting every candidate.

**Files:**
- Create: `prism_app/candidate_batch.py`
- Modify: `prism_app/product_composition.py`
- Modify: `prism_app/single_runner.py`
- Test: `tests/app/test_candidate_batch.py`

**Required behavior:**
- One immutable market context and evaluation boundary per daily batch.
- Per-candidate KIS/FMP/official-evidence snapshots, identities, and transactions.
- Separate SWING/TREND calls for every eligible candidate.
- Per-candidate status: completed scenario, report-only, analysis-incomplete, or provider-unavailable.
- No candidate-count, analysis-count, or LLM-call-count cap. Control cost and load with observable queues, idempotency, bounded concurrency, provider backoff, and resumable batches rather than silently dropping candidates.
- Exact replay does not call the model again.
- A failed candidate cannot roll back successful sibling candidates.
- Batch summary records every included/excluded/failed candidate and reason.
- No publication or broker capability.

**Failure-injection tests:**
- Second candidate provider failure.
- Second strategy model failure.
- Research DB saved but ops save interrupted.
- Duplicate concurrent runner.
- Snapshot drift creates a new invocation rather than replay.

---

## 4. KR-first implementation

### Task 5: Wrap the existing KR selector behind `CandidateSnapshot`

**Objective:** Preserve existing PRISM candidate selection while removing direct coupling to the legacy trading flow.

**Files:**
- Create: `prism_app/kr_candidate_source.py`
- Modify narrowly: `trigger_batch.py`
- Modify or reuse: `cores/naver_market_snapshot.py`
- Test: `tests/app/test_kr_candidate_source.py`
- Extend: `tests/app/test_orchestrator_wrappers.py`

**Required behavior:**
- Call only the characterized read-only candidate functions; never enter tracking, messaging, broker, or order call graphs.
- Supply the shared `KRMarketContext` instead of `macro_context=None`.
- Export trigger IDs, unmodified trigger scores, RS/extension observations, data source, and source clocks.
- Use KIS as primary product price source; KRX/Naver may remain explicit supporting/fallback candidate-discovery sources.
- Name lookup failure must not change security identity.
- Preserve old user-visible candidate behavior behind parity/SHADOW comparison.

**Acceptance:**
- Real closed-session KR run returns actual core candidates with source/as-of/call evidence.
- Legacy output and adapter output are compared and discrepancies are explained, not silently normalized.

---

### Task 6: Replace mandatory KR FMP dependency with an official evidence cascade

**Objective:** Allow arbitrary Korean candidates such as `214450` to complete technical/market analysis even when FMP lacks two PIT statements.

**Files:**
- Create: `prism_core/data/providers/dart.py`
- Create: `prism_core/data/providers/kind.py` if an approved bounded endpoint/export is available; otherwise define the port and explicit unavailable adapter.
- Create: `prism_app/kr_evidence_composer.py`
- Modify: `prism_app/live_kr_evidence.py`
- Modify: `prism_app/market_snapshot_composer.py`
- Modify: `prism_app/product_uat.py`
- Test: `tests/data/providers/test_dart_provider.py`
- Test: `tests/app/test_kr_evidence_composer.py`
- Extend: `tests/app/test_live_kr_evidence.py`
- Extend: `tests/app/test_product_uat.py`

**Provider order:**
1. DART official filings/fundamentals and acceptance timestamp.
2. KIND/KRX official event/listing/trading-status evidence where approved and available.
3. KIS-provided company/market fields within documented market-data scope.
4. FMP as supplemental normalization only for KR.

**Disposition rules:**
- Missing FMP must never abort the whole KR analysis.
- Missing supplemental fundamentals keeps technical/market analysis and sets an entry veto or `REPORT_ONLY` as defined by strategy policy.
- Missing core price, identity, session, basis, or timing remains `ANALYSIS_INCOMPLETE`.
- Trading suspension, management/delisting risk, unresolved corporate action, or severe filing conflict is a deterministic veto.
- Persist source and acceptance timestamps; do not backdate filing availability.

**Real acceptance case:**
- `214450` KIS FRESH data plus official evidence cascade must produce either a complete evidence-backed scenario or an explicit non-fabricated `REPORT_ONLY`/`ANALYSIS_INCOMPLETE`; it must not raise a generic `LiveKREvidenceError` merely because FMP coverage is absent.

---

### Task 7: Add approved supplemental leadership evidence, including StockEasy import

**Objective:** Include permitted market breadth, flows, leading groups, RS/highs, turnover, momentum, and peak-state observations without making StockEasy a hidden hard dependency.

**Files:**
- Create: `prism_core/data/contracts/leadership_supplement.py`
- Create: `prism_app/leadership_supplement.py`
- Create: `prism_app/stockeasy_snapshot_import.py`
- Modify: `prism_core/strategies/scenario_inputs.py`
- Test: `tests/app/test_leadership_supplement.py`
- Test: `tests/app/test_stockeasy_snapshot_import.py`

**Safety and product rules:**
- First record terms/permission verification and the exact approved UI/export scope.
- Accept only a sanitized bounded snapshot generated from an approved UI/export path.
- Reject credentials, cookies, tokens, internal endpoint details, account/payment/profile fields, and unexpected schema keys.
- Record capture/export time, page/section identity internally, content/image hash, freshness, and capture outcome.
- Delete temporary images after successful extraction and verify deletion; persist only sanitized structured evidence and allowed hashes.
- KIS/KRX remain authoritative for prices, returns, and trade values.
- StockEasy disagreement becomes a visible conflict, never a silent overwrite.
- Collection failure yields `UNAVAILABLE`; it does not erase KIS/KRX analysis.
- User-facing reports use generic section names and never expose StockEasy menu names.

**Supplemental candidate policy:**
- Supplemental evidence may nominate any number of candidates; reconciliation removes duplicate security identities but does not truncate the unique set.
- These candidates traverse the same KIS/official-evidence/SWING/TREND/validator path.
- They remain visibly distinct from `CORE_PRISM` candidates.

---

### Task 8: Build the KR daily-close composition and CLI

**Objective:** Expose one copyable read-only command that runs the complete KR candidate product.

**Files:**
- Create: `prism_app/kr_daily_product.py`
- Modify: `prism_app/cli.py`
- Modify: `prism_app/__main__.py`
- Test: `tests/app/test_kr_daily_product.py`
- Extend: `tests/app/test_cli.py`

**Proposed command:**

```bash
python -m prism_app kr-daily \
  --as-of <timezone-aware-close-boundary> \
  --research-db <private-path>/research.sqlite \
  --paper-db <private-path>/paper.sqlite \
  --ops-db <private-path>/ops.sqlite \
  --report-output <private-path>/report.md \
  --dashboard-output <private-path>/dashboard.json \
  --stockeasy-snapshot <optional-approved-sanitized-json>
```

**Command proof requirements:**
- Real candidate list, not fixed symbol only.
- Actual KIS and approved official/context providers.
- Actual ChatGPT OAuth calls for every unique eligible candidate, controlled by observable queues and bounded concurrency without truncation.
- Separate SWING/TREND rows for each completed candidate.
- Sanitized stdout: counts, source statuses, quality, job keys, output paths, no secrets/raw payloads.
- Nonzero exit when no genuine candidate scenario completes.
- `broker_called=false`, `schedule_activated=false`, `uat_accepted=false`, `operational_readiness=false` until explicit UAT.

---

### Task 9: Render one user-oriented KR report without losing audit detail

**Objective:** Keep the existing report/dashboard while adding a concise first layer for daily use.

**Files:**
- Modify: `prism_app/shadow_report.py`
- Modify: `prism_app/dashboard_export.py`
- Modify: `prism_app/user_surface_uat.py`
- Modify: `examples/dashboard/app/page.tsx`
- Test: `tests/app/test_shadow_report.py`
- Test: `tests/app/test_user_surface_uat.py`
- Test: `tests/app/test_dashboard_export.py` or the current dashboard test location

**Required report order:**
1. Source/as-of/call evidence and data-quality summary.
2. KR market regime, breadth/flows, and leading/weak groups.
3. Candidate table: channel, trigger, SWING, TREND, current state, top support, top counter-evidence.
4. Per-candidate SWING/TREND cards.
5. Conditional entry/avoid predicates, structure/ATR invalidation, reduction/exit scenarios where complete.
6. Data gaps/conflicts and why exact levels are suppressed when applicable.
7. Change since previous run: `NEW`, `MAINTAINED`, `EXITED`, `DATA_MISSING`.
8. Next events and review time.
9. Collapsible audit details: IDs, raw dispositions, hashes, validator internals.

**Rules:**
- No quantity, account, execution approval, or order language.
- If data is stale/partial/conflicting, do not publish actionable price levels.
- Do not convert incomplete analysis to `NO_ENTRY`.
- Do not expose StockEasy menu names.

---

### Task 10: Complete leadership history and feedback retrieval

**Objective:** Prove the full daily learning loop for real KR candidates.

**Files:**
- Modify: `prism_app/outcome_tracker.py`
- Modify: `prism_app/query_service.py`
- Add or modify repositories under: `prism_core/persistence/`
- Create: `tests/app/test_kr_candidate_feedback_cycle.py`
- Extend: `tests/feedback/`

**Persist by date and strategy:**
- Candidate membership and channel.
- Relative strength, 52-week high state, momentum, and peak state.
- Sector/group state: `LEADING`, `EMERGING`, `NARROW`, `FADING`.
- Process outcome: data/evidence/predicate/validator quality.
- Market outcome: forward returns over strategy-specific horizons, with no look-ahead in the original decision.
- `SUPPORT` and `CONTRA` lessons.
- Next-run SHADOW lesson retrieval keyed by market/security/strategy/version/regime.

**Real proof:**
- Run day N with real candidates.
- Advance to a later completed session without modifying day-N evidence.
- Compute process and outcome records.
- Run day N+1/new boundary.
- Show the prior lesson retrieved in SHADOW only, with no automatic score/risk mutation.

---

## 5. KR acceptance gates

KR is not complete until one real close-to-report cycle proves all of the following:

- Existing PRISM returns real core candidates.
- Approved leadership evidence can add real supplemental candidates or explicitly reports none.
- At least one non-`005930` real candidate traverses KIS → official evidence → SWING/TREND → validator.
- Candidate-specific provider failure is isolated and visible.
- Both strategy families remain separate through DB and report.
- SQLite readback reproduces the report losslessly.
- Existing report and dashboard show the same snapshot and decisions.
- Source/as-of/call evidence is visible for KIS, AgentNews, DART/KIND/KRX where used, and supplemental sources.
- No secret/private field appears in stdout, DB export, report, or dashboard.
- No broker/account/order/message/schedule effect occurs.
- A real outcome/process+outcome feedback record is later persisted and retrieved in the next SHADOW run.
- User performs explicit UAT on the report/dashboard and accepts the daily output.

**Verification commands:**

```bash
python -m pytest \
  tests/candidates \
  tests/market \
  tests/app \
  tests/features \
  tests/strategies \
  tests/data/providers \
  tests/llm \
  tests/policy \
  tests/feedback -q

python -m pytest tests/safety tests/runtime -q
python -m compileall -q prism_core prism_app cores/llm
python tools/audit_broker_boundaries.py
git diff --check
```

Also run:
- Exact live KR read-only UAT in a private ignored directory.
- SQLite read-only queries proving counts, identities, statuses, normalized proposals, and dispositions.
- Report/readback hash comparison.
- Existing dashboard production build.
- Credential/private-value mechanical scan without printing values.
- Independent Claude read-only review of candidate reconciliation, PIT evidence policy, failure isolation, and final report state mapping.

---

## 6. Delivery sequence

Each numbered milestone is delivered independently through `commit -> push feature branch -> PR -> exact-head CI -> squash merge -> fetch/prune and verify origin/main` before the next milestone starts.

Implement in this order:

1. Shared candidate contracts and reconciliation.
2. Shared market context.
3. KR selector adapter with regime parity.
4. KR official evidence cascade and removal of mandatory FMP dependence.
5. Approved leadership supplement/StockEasy snapshot import.
6. Fault-isolated candidate batch.
7. KR daily CLI.
8. Existing report/dashboard user layer.
9. Leadership history and feedback retrieval.
10. Real KR close-to-report UAT and independent review.
11. Explicit user acceptance.
12. Only after separate operational approval: scheduling and allowlisted Discord/Telegram publication.

Suggested commit boundaries:

```text
feat: add market-neutral candidate contracts
feat: reconcile core and supplemental candidates
feat: share deterministic market context
feat: adapt legacy KR candidate selector
feat: compose official KR evidence cascade
feat: ingest approved leadership supplements
feat: run fault-isolated candidate scenarios
feat: add KR daily product command
feat: render integrated KR report and dashboard
feat: persist KR leadership outcomes and shadow lessons
test: prove real KR daily product UAT
```

---

## 7. Stop conditions

Stop and ask for explicit direction if implementation requires any of the following:

- Account, balance, holdings, broker, order, cancel/replace, or live capability.
- Credential changes or sharing.
- A StockEasy collection method that conflicts with terms, permissions, or approved UI/export boundaries.
- Destructive migration or modification of existing user DBs.
- Breaking candidate/report/dashboard compatibility rather than adding a thin adapter.
- Risk-limit, policy, kill-switch, or execution-scope changes.
- Unresolved CRITICAL/HIGH independent-review finding.
- Repeated live-provider/CI failures without a verified root cause.

---

## 8. Definition of done

KR is done when the user can run one command and see:

```text
real KR core candidates
+ real supplemental leadership candidates
→ valid SWING/TREND scenarios or honest incomplete states
→ deterministic validation
→ existing report/dashboard
→ SQLite readback
→ later process+outcome feedback
→ next-run SHADOW lesson retrieval
```

with source/as-of/call evidence, no fabricated data, no hidden provider substitution, no fixture-only acceptance, no candidate-count/analysis-count truncation, and no broker/account/order effects. US work remains deferred and is not part of this delivery.
