# PRISM Phase 1 Product Vertical Integration Plan

> **For Hermes:** Execute task-by-task with strict TDD. Phase 1 remains read-only with respect to every broker/account/order surface. Claude is read-only reviewer; Hermes owns all edits and verification.

**Goal:** Preserve the existing PRISM candidate/report product while making one safe user-operable path work end-to-end from actual KIS/FMP market data through structured LLM proposals, deterministic validation, persistence, SHADOW reporting, CLI execution, and UAT.

**Architecture:** Add a thin composition root under `prism_app` that adapts the existing provider transports, feature engine, provider-agnostic LLM backend, proposal parser/validator, feedback repository, and daily report renderer. Integrate the resulting SHADOW section into the existing report artifact through a narrow renderer seam; do not route through `stock_tracking_agent.py` or the giant legacy trading prompt. Default CI uses injected fixture transports; actual KIS/FMP/LLM smoke remains separately capability-gated and sanitized.

**Tech Stack:** Python 3.10+, asyncio, Pydantic, SQLite, existing `cores.llm.LLMBackend`, KIS/FMP read-only transports, pytest.

---

## Completion contract

Implementation is complete only when one product path is exercised and the user can inspect the resulting artifact. Unit/fixture results, live integration, runtime wiring, and operated readiness are reported separately.

Required observable flow:

```text
existing candidate/report input
-> KIS or FMP market-data-only snapshot
-> PIT/data-quality gate
-> separate SWING_V1 and TREND_V1 feature/quant evidence
-> actual LLM backend (tool-free strict JSON)
-> ProposalService
-> ProposalValidator
-> append-only research.sqlite proposal/disposition records
-> existing PRISM Markdown report + clearly inert SHADOW section
-> CLI/UAT artifact
```

Non-negotiable boundaries:

- zero account, balance, holdings, order, cancel, replace, broker-paper, or live calls;
- no import of broker modules from Phase 1 composition;
- no silent fallback from actual provider/LLM to fixtures;
- missing/stale/partial/conflicting core evidence fails closed with visible `NO_ENTRY`/unavailable status;
- no user/legacy DB mutation during tests or UAT rehearsal;
- existing candidate-selection and report/PDF surface remains the product shell.

---

## Milestone 1 — Structured LLM strategy evaluator

**Objective:** Convert one immutable feature/evidence bundle into a model call, strict parse, deterministic validation, append-only persistence, and `StrategyAnalysis` output.

**Files:**
- Create: `prism_app/strategy_evaluator.py`
- Modify: `prism_app/daily_pipeline.py`
- Test: `tests/app/test_structured_strategy_evaluator.py`

**TDD behaviors:**

1. Tool-free `AgentSpec` contains the exact strategy/market prompt contract and strict output schema.
2. User input is deterministic canonical JSON containing only supplied feature values, quant score, evidence, and identities.
3. Valid fake-backend output is parsed, validated, persisted, and returned as SHADOW analysis.
4. Malformed, identity-mismatched, unknown-evidence, or stale output is persisted as rejected evidence and never becomes an actionable strategy result.
5. SWING/TREND exact identities and prompt versions remain separate.
6. Backend failure records a fail-closed result without fabrication.

**Verification:**

```bash
python -m pytest tests/app/test_structured_strategy_evaluator.py -q
python -m pytest tests/llm tests/policy tests/feedback tests/app -q
```

---

## Milestone 2 — Provider snapshot to strategy input composition

**Objective:** Adapt actual KIS/FMP `MarketSnapshot` outputs and existing structured evidence into PIT-valid feature inputs for both strategies.

**Files:**
- Create: `prism_app/market_snapshot_composer.py`
- Create: `prism_app/provider_runtime.py`
- Test: `tests/app/test_market_snapshot_composer.py`
- Test: `tests/app/test_provider_runtime.py`

**TDD behaviors:**

1. KR selects only KIS market-data transport; US selects only FMP primary transport.
2. No account/order method exists on injected runtime protocols.
3. Provider bars map to aligned raw/adjusted price and benchmark series without mixing bases.
4. Required catalyst/regime/earnings/industry evidence is explicit and PIT-bound; absence rejects instead of inventing values.
5. FRESH input produces separate feature snapshots; stale/partial/conflict blocks model calls.
6. `ingested_at` occurs after transport completion.

**Verification:**

```bash
python -m pytest tests/app/test_market_snapshot_composer.py tests/app/test_provider_runtime.py -q
python tools/audit_broker_boundaries.py
```

---

## Milestone 3 — User CLI and existing report SHADOW seam

**Objective:** Provide one command that produces a persisted, inspectable Markdown/JSON artifact and append that SHADOW content to the existing PRISM report without replacing its candidate-selection/report shell.

**Files:**
- Create: `prism_app/cli.py`
- Create: `prism_app/composition.py`
- Create: `prism_app/shadow_report.py`
- Modify: `stock_analysis_orchestrator.py`
- Modify: `prism-us/us_stock_analysis_orchestrator.py`
- Test: `tests/e2e/test_phase1_product_vertical_slice.py`
- Test: `tests/app/test_shadow_report_integration.py`

**TDD behaviors:**

1. `python -m prism_app.cli --help` is an actual CLI.
2. `--dry-run-fixture` creates temporary/rehearsal stores and an inspectable report labeled `FIXTURE_CONTRACT`.
3. Actual mode cannot start when provider or LLM prerequisites are missing; no fallback occurs.
4. Existing Markdown remains byte-for-byte unchanged before one appended `Phase 1 SHADOW` section.
5. Re-running the same job is idempotent and does not duplicate the section or proposal records.
6. A report contains source, as-of, quality, model/prompt/validator versions, SWING/TREND separation, and explicit no-order notice.

**Verification:**

```bash
python -m pytest tests/e2e/test_phase1_product_vertical_slice.py tests/app/test_shadow_report_integration.py -q
python -m prism_app.cli --help
```

---

## Milestone 4 — Actual integration smoke and UAT

**Objective:** Exercise bounded actual read-only endpoints and the real application composition, then provide a user-run acceptance checklist.

**Files:**
- Create: `tests/integration/test_phase1_llm_live.py`
- Create: `tests/integration/test_phase1_product_live.py`
- Create: `docs/PHASE1_UAT.md`
- Update: `.hermes/PRISM_AUTOPILOT_HANDOFF.md`

**Evidence states reported separately:**

1. Foundation/tests.
2. KIS/FMP/LLM actual live integration.
3. Runtime wiring through the CLI/composition root.
4. User-observed report/database/dashboard/Telegram behavior.
5. Operated readiness after schedule/recovery observation.

**Live boundary:** KIS/FMP market data only. No account or broker capability. Sanitize endpoint family, status, timestamp, schema/quality, latency, correlation, model ID, and prompt version; never print secrets or raw private payloads.

**Verification:**

```bash
python -m pytest tests/e2e tests/app -q
python -m pytest -m 'fmp_live_market_data or kis_live_market_data or llm_live' tests/integration -q
python tools/audit_broker_boundaries.py
python -m compileall -q prism_core prism_app stock_analysis_orchestrator.py prism-us/us_stock_analysis_orchestrator.py
```

---

## Final gates

- Independent Claude read-only review of composition, provenance, fail-closed behavior, LLM schema boundary, persistence, and no-broker imports.
- Focused regression tests for every accepted material finding.
- Full repository CI command groups pass on the final tree.
- `git diff --check`, dependency check, broker-boundary audit, and private-artifact inspection pass.
- User runs or observes the UAT artifact and explicitly approves before launchd/Telegram scheduling is activated.

## Stop conditions

Stop and request user input for credentials/entitlement changes, account/broker/live/risk scope, destructive or compatibility-breaking migration, unresolved HIGH/CRITICAL review findings, repeated failure, or unverifiable live gates. Never ask the user to paste secrets into chat.
