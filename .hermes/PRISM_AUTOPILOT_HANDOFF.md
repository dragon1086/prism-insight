# PRISM Guarded-Autopilot Handoff

## Current milestone — Phase 1A Task 9.1

- Branch: `prism-insight/t_5ac98080-prism-phase-1a-task-9.1-fmp-core-transpo`
- State: implementation, GPT-controlled local verification, and verified read-only Claude review are green. Commit, PR, exact-head CI, merge, and successor creation remain pending at this checkpoint.
- Runtime state: dormant injected transport contracts and fixture-tested normalization only. No concrete HTTP client, credential lookup, live FMP/yfinance/SEC request, pagination, fallback, caller/application wiring, scheduler, broker/account/order path, or production database is wired.

## Task 9.1 implemented scope

- Added `FMPApiKey`, canonical non-secret `FMPRequest`, and immutable `FMPResponseEnvelope` models with detached payload copies, stable SHA-256 identity/evidence hashes, strict response metadata, and redacted credential representations.
- Added an injected `FMPTransport` protocol and an FMP-primary provider for an explicit one-page fixture path. The credential is passed separately from canonical request identity; no concrete network transport exists.
- Added explicit capability outcomes for supported, forbidden/unsupported, malformed, timeout, and rate-limit responses without fabricating entitlement.
- Added bounded observable timeout/429 retries with sanitized event metadata and deterministic retry-independent snapshot identity.
- Normalized valid US rows into existing strict `SecurityId`, `SymbolMapping`, `ObservationTime`, `PriceBar`, and `MarketSnapshot` contracts while preserving provider symbol, source identity/revision/hash, observed/available/ingested/as-of times, raw and adjusted OHLCV, and adjustment vintage.
- Missing, malformed, PIT-invalid, stale, partial, unavailable, conflicting, unmatched, inactive, and retry-exhausted evidence is explicit and fail-closed. Conflicting duplicate rows are withheld; provider-labelled degraded evidence remains labelled for the deterministic quality gate and is never relabelled fresh.
- Added recursive decoded-payload and source-identity secret-echo rejection, including JSON-escaped characters. No secret-bearing response is retained.
- Added an explicit market-local future-trading-date guard so row-level look-ahead rejection does not depend only on the transitive `PriceBar` validator. Future adjustment vintages are also withheld through the strict PIT contract.
- Exported the dormant FMP contracts from `prism_core.data.providers` and `prism_core.data`. Legacy US callers were intentionally unchanged.

## Task 9.1 verification and review

- `python -m pytest tests/data/providers/test_fmp_provider.py -q` — 38 passed.
- `python -m pytest tests/data tests/storage -q` — 149 passed.
- `python -m pytest tests/runtime tests/safety -q` — 65 passed.
- Canonical CI-equivalent remaining groups — 292 passed, 1 intentionally deselected.
- `python -m compileall -q prism_core tools/audit_broker_boundaries.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed with 0 violations; legacy inventory unchanged.
- `python -m pip check` — no broken requirements; `git diff --check` passed.
- Independent review during the original worker run found one MEDIUM false-FRESH path: non-object rows in a valid page were silently skipped. The worker reproduced it RED, added a sanitized `MALFORMED`/`PARTIAL` event, and reran local gates.
- A later verified read-only Claude architecture/security/data-integrity review found no HIGH defect and recommended merge. GPT accepted its MEDIUM robustness concern that future row dates were rejected only transitively, added the explicit market-local date guard and decisive PIT/secret tests, and reran every local gate. A targeted post-fix Claude review found no HIGH/MEDIUM blocker and recommended merge.
- GPT rejected as a blocker the review concern about retaining provider-labelled `CONFLICT` bars: the existing authoritative contract explicitly separates observed data quality from policy disposition, and the snapshot/bar remain labelled `CONFLICT` for the deterministic fail-closed quality gate. Reconciliation conflicts are still withheld.
- Non-blocking deferred notes: `FMPRequest` cannot independently know whether an arbitrary non-secret-named value equals a runtime credential, so the provider remains the credential-separation boundary; failed-snapshot identity intentionally lacks operational attempt identity; live transport must preserve numeric strings or equivalent lossless decoding; US calendar-aware half-day/session intervals remain a later slice concern.

## Task 9.1 safety and side effects

- Tests used injected fakes/fixtures only. No live FMP/yfinance/SEC/provider request, credential lookup/output/change, user/private database access, broker/account/order call, Telegram/AgentNews effect, runtime activation, or deployment occurred.
- This task establishes foundation/tests only; production/runtime behavior is unchanged.

## Previous milestone — Phase 1A Task 8

- Task 8 implementation was squash-merged via PR #8 at `654900fe3b011135409e6927ac2eb4625d6d6f97`; post-merge CI run 30072800800 passed Python 3.10/3.11/3.12.
- The merged KIS foundation remains dormant and market-data-only. No provider, broker, account, order, credential, or user-database effect occurred.

## Previous milestone — Phase 1A Task 7A

- Task: Phase 1A Task 7A — KR/US time-sliced leadership tracking foundation
- Branch: `prism-insight/t_97ca40ff-prism-phase-1a-task-7a-kr-us-leadership`
- Implementation state: committed, pushed, fully verified, and independently reviewed; PR #7 first-head CI passed and the handoff checkpoint requires fresh exact-head CI before squash merge
- Runtime state: dormant `research.sqlite` schema/repository and an explicit-path ingest/readback CLI only; no provider, scheduler, dashboard, Telegram, strategy, LLM, paper-broker, broker, account, or application runtime is wired

## Implemented scope

- Added strict `market_tracking_v1` models for KR/US leadership reports at KST 01/07/13/19 with market-specific stage and provisional/confirmed semantics.
- Preserved report provenance, observed/available/as-of/ingested times, quality and reasons, market state/events, nullable relative-strength windows, 52-week-high state/distance, raw liquidity, optional flow, momentum/peak, strategy labels, and decision status.
- Added strict fail-closed validation for naive/future/reversed timestamps, unsupported market/slot/stage/version/enums, non-finite values, duplicate normalized symbols, invalid KR/US symbol formats, inconsistent quality/completeness/strategy/high-state combinations, and all unknown executable/order/account/price fields.
- Added canonical UTC/Decimal serialization and immutable identity that includes revision and semantic/source content while excluding processing-only `ingested_at`.
- Added `LeadershipRepository` over the existing `market_snapshots`, `observations`, and `reports` tables without a new migration or parallel database.
- Each ingest atomically stores one market snapshot, one `leadership_market_state`, current `leadership_security_state` rows, and one generic Markdown report with `provider=hermes_agent_report` and `policy_disposition=REPORT_ONLY`.
- Added exact replay idempotency, same-run/revision conflict rejection, explicit higher-revision append semantics, database-backed uniqueness, deterministic readback, and rollback of the whole evidence unit on report failure.
- Added point-in-time prior-run comparison using each eligible run's highest available revision and `(available_at, ingested_at, snapshot_id)` ordering, producing `NEW`, `MAINTAINED`, `EXITED`, or conservative `DATA_MISSING` states.
- Added a generic renderer that never exposes source-site/menu names, suppresses security metrics when core evidence is unusable, and never contains executable price levels.
- Added `tools/ingest_market_tracking_snapshot.py`, which requires an explicit `--db`, accepts a JSON file or stdin, migrates that explicit research database, and prints canonical JSON summary or persisted Markdown.
- Updated the authoritative transformation plan with inserted Task 7A and explicit Task 20/21 reuse requirements.

## Contract decisions

- This evidence is an untrusted human/agent report snapshot for research display only. It is not a deterministic feature snapshot, proposal, sizing input, order intent, or execution approval.
- `ingested_at` is processing metadata: an exact replay at a later ingestion instant returns the existing immutable record. UTC-equivalent datetimes and Decimal values with equivalent scales share identity.
- Reusing a run/revision with different canonical content fails atomically. A correction must use the next higher revision and appends a new immutable snapshot; old evidence is never updated or deleted.
- Prior comparison excludes the current run, ignores corrections unavailable before the current run, and does not trust symbols from an unusable prior snapshot.
- A missing prior symbol becomes `EXITED` only when current core evidence is usable and the current leadership universe is complete; otherwise it is `DATA_MISSING`.
- Per-security observation payloads include run quality/usability/completeness context so direct readers cannot mistake a row from an unusable run for trusted leadership.
- Task 20 must reuse this repository for persistence-before-publication. Task 21 must reuse/extend this schema, readback, renderer, identity, quality mapping, and change classifier rather than create parallel leadership storage.

## Verification

- `python -m compileall -q prism_core/reporting tools/ingest_market_tracking_snapshot.py` — passed.
- `python -m pytest tests/reporting/test_leadership_tracking.py -q` — 61 passed.
- `python -m pytest tests/storage tests/data tests/runtime tests/safety -q` — 155 passed.
- Canonical CI-equivalent remaining groups — 292 passed, 1 intentionally deselected.
- Total local pytest evidence for this closeout: 508 passed, 1 deselected.
- `python tools/audit_broker_boundaries.py` — passed with 0 violations; legacy inventory unchanged.
- `python -m pip check` — no broken requirements.
- `git diff --check` — passed.
- Tests use real temporary SQLite databases and prove KR/US acceptance, strict rejection cases, exact replay, corrections, atomic conflicts/rollback, prior comparison, unavailable revisions, `DATA_MISSING`, append-only triggers, DB uniqueness, deterministic readback/rendering, generic headings, fail-closed suppression, explicit-path CLI behavior, and no implicit legacy DB creation.

## Independent review

- A successful frozen-patch Claude read-only final review returned no HIGH finding and approved merge conditionally on the already-proven storage constraints/tests.
- GPT verified the review's database caveat directly against the existing `UNIQUE(provider, source_record_id, revision)`, append-only triggers, and `BEGIN IMMEDIATE` transaction behavior through source inspection and passing tests.
- GPT accepted the one material MEDIUM finding: security-level rows could be misread without run quality context. TDD added a failing direct-read test, then embedded `quality`, `quality_reasons`, `core_evidence_usable`, and `leader_universe_complete`; the focused suite returned to green.
- GPT also implemented all decisive reviewer tests: security-row uniqueness, PARTIAL-but-usable renderer behavior, and self-describing unusable security observations.
- Two later attempts to repeat the review against the unchanged final code exceeded Claude turn limits and were unusable; they produced no contrary finding and are not counted as reviews. The earlier successful final review plus GPT-controlled remediation and full rerun are the closing evidence.

## Side effects and safety

- Only repository files and pytest temporary SQLite files were created/opened. No repository/user SQLite database or legacy `stock_tracking_db.sqlite` was opened, migrated, or modified.
- No provider/network call, external message, AgentNews fetch, broker/account/order call, credential access/change, runtime activation, deployment, or cron mutation occurred.
- Feature commit `a083ea9` was pushed to the scoped branch and PR #7 (`https://github.com/mienne/prism-insight/pull/7`) was opened against `mienne/prism-insight:main`; its Python 3.10/3.11/3.12 checks passed against exact head `a083ea9e28a6212288ef7c8722338af939a0a6f5`. No merge has occurred at this checkpoint.

## Merge state and next task

- Merge state: local gates and the first exact-feature-head CI matrix are green. This handoff checkpoint commit changes the head, so a fresh Python 3.10/3.11/3.12 matrix must pass against the final pushed SHA before squash merge, followed by post-merge `origin/main` and merge-CI verification.
- Canonical successor after verified merge is the existing Task 8 card `t_900c8832` (KIS KR market-data adapter). Do not create a duplicate successor.
- Task 8 must remain market-data-only with no order-submission imports and must preserve Task 7A report evidence as `REPORT_ONLY` rather than promoting it to deterministic provider data.
- Stop conditions remain: block on merge conflict, unresolved high/medium data-safety review, compatibility break, destructive or in-place user-data migration, required-gate failure after three reasoned attempts, unrelated conflicting changes, credentials/broker/live/risk scope, or an unverifiable exact-head CI/merge gate.
