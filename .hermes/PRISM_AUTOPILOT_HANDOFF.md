# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1A Task 7 — security master and corporate-action storage
- Branch: `prism-insight/task-7-security-master-corporate-actions`
- Implementation state: locally complete, fully verified, and independently reviewed; commit, PR, exact-head CI, and squash merge remain
- Runtime state: dormant `research.sqlite` contracts, migration, and repositories only; no provider, scheduler, dashboard, strategy, LLM, paper-broker, broker, account, or application entrypoint is wired

## Implemented scope

- Added append-only, provider-provenanced security aliases and listing-state evidence keyed to stable UUID `SecurityId` values rather than ticker identity.
- Added point-in-time symbol resolution that filters on `available_at`, collapses revisions per provider/source, respects half-open validity intervals, reconciles listing state across each provider's latest applicable assertion, and fails closed with `CONFLICT` rather than silently choosing a provider.
- Added curated stable corporate-action identities with append-only provider evidence, exact re-ingestion dedupe, availability-aware revision handling, timezone-aware effective instants, and normalized Decimal terms.
- Added corporate-action reconciliation that hides actions before the effective boundary, coalesces equivalent provider evidence, and withholds all economic terms on provider disagreement, including disagreement whose effective dates straddle the query boundary.
- Added research migration 002 with same-database foreign keys, PIT/quality/economic checks, query indexes, append-only triggers, immutable registered securities, a frozen legacy `symbol_mappings` path, and atomic refusal to orphan populated v1 mappings.
- Exported the new public contracts/repositories and registered their tables in the research database boundary manifest.

## Contract decisions

- `query_as_of` is the single point-in-time knowledge/evaluation boundary: revisions become authoritative only after their `available_at`; a provider correction may replace a mistaken symbol, while a real ticker rename is represented as separate validity-window events mapping to the same `SecurityId`.
- Evidence identity excludes processing-only `ingested_at` and `as_of_date`, so unchanged source evidence remains idempotent across re-ingestion. It retains provider, source record/hash, revision, semantic terms, observation/availability times, and quality.
- Corporate-action `action_id` is a curated stable application identity supplied to this Task 7 repository. Deterministic provider-side curation is deliberately deferred to Tasks 8/9; equivalent evidence under one ID coalesces and divergent terms fail closed.
- Market-local `effective_date` is validated before `effective_at` is normalized to UTC storage. All persisted/query datetimes reject naive values and use canonical UTC ISO text.
- Listing resolution retains a resolved `SecurityId` when only listing status conflicts, but withholds `listing_status`; alias identity conflicts withhold `SecurityId` itself.
- Existing populated v1 `symbol_mappings` rows are not guessed or rewritten. Migration 002 fails atomically and leaves v1 history/data intact for an explicit future migration decision.

## Verification

- `python -m compileall -q prism_core tools/audit_broker_boundaries.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed, 0 violations; legacy inventory unchanged.
- `python -m pytest tests/runtime tests/safety -q` — 65 passed.
- `python -m pytest tests/storage -q` — 44 passed.
- `python -m pytest cores/llm/tests/ -q` — 111 passed.
- Remaining exact local CI command groups — 181 passed, 1 intentionally deselected.
- `python -m pytest tests/data -q` — 46 passed.
- Total local pytest result across the canonical groups plus data tests: 447 passed, 1 deselected.
- Task 7 tests use real temporary SQLite databases and cover ticker rename continuity; historical delisting; symbol corrections and future-effective listing revisions; provider listing conflict; split/dividend availability and effective boundaries; conflicting effective dates; re-ingestion idempotency; equivalent/conflicting provider action terms; KR/US timezone boundary behavior; same-database FKs; append-only triggers; v1-to-v2 history preservation; and atomic rollback on populated legacy mappings.
- `python -m pip check` — no broken requirements.
- `git diff --check` — passed after the final review remediations.

## Independent review

- The pre-implementation Claude Opus read-only architecture/data-integrity review required explicit effective instants, availability-before-revision selection, separate provenance and curated economic-action identities, deterministic Decimal reconciliation, and fail-closed conflict handling; these decisions were implemented.
- The first final Claude review raised three conditional high concerns and several medium checks. GPT verified that re-ingestion identity already excludes processing timestamps, confirmed curated `action_id` as the approved Task 7 boundary, and rejected a correction-vs-rename conflation using the explicit PIT contract and regression tests.
- GPT accepted and fixed the material effective-date disagreement finding through observed RED/GREEN: known provider disagreement now blocks early adjustment instead of applying one provider's date silently. Additional tests proved cross-run idempotency, equivalent provider coalescing, and market-local/UTC boundary behavior.
- A post-remediation Claude Opus closing review found no remaining high or medium defect within Task 7, accepted all adjudications, and recommended merge conditional on the full gate rerun above.
- Deferred adapter requirements for Tasks 8/9: classify corrections versus real rename events correctly; derive stable corporate-action IDs deterministically across providers; and supply market-local timezone-aware effective instants. These are provider-wiring requirements, not Task 7 operated readiness.

## Side effects and safety

- Only pytest temporary SQLite files were created/opened. No repository/user SQLite database or legacy `stock_tracking_db.sqlite` was opened, migrated, or modified.
- No network/provider call, broker/account/order call, credential access/change, external message, AgentNews fetch, deployment, runtime activation, or application wiring occurred.
- Feature commit `873f5c6` was pushed and PR #6 (`https://github.com/mienne/prism-insight/pull/6`) was opened against `main`. No merge has occurred at this checkpoint.

## Merge state and next task

- Merge state: local Task 7 implementation and all closing gates are green; PR #6 is mergeable and its first exact-feature-head CI matrix started for `873f5c6`. This handoff checkpoint commit requires a fresh exact-head CI matrix before squash merge, followed by post-merge `origin/main` verification.
- Next approved task after verified merge only: Phase 1A Task 8 — KIS KR market-data adapter, with no order-submission imports and the deferred provider identity/timezone requirements above.
- Stop conditions remain: block on merge conflict, unresolved high/medium data-safety review, compatibility break, destructive or in-place user-data migration, required-gate failure after three reasoned attempts, unrelated conflicting changes, credentials/broker/live/risk scope, or an unverifiable exact-head CI/merge gate.
