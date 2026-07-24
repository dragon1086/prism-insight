# PRISM Guarded-Autopilot Handoff

## Current milestone — Phase 1A Task 8

- Branch: `prism-insight/t_900c8832-prism-phase-1a-task-8-kis-kr-market-data`
- State: implementation, independent review, all required local gates, and first exact-feature-head CI are green. PR #8 is open; this handoff checkpoint requires fresh exact-head CI before merge.
- Runtime state: dormant provider contracts only. No transport implementation, credentials, scheduler, application entrypoint, legacy caller, broker/account/order code, or production database is wired.

## Task 8 implemented scope

- Added an injected market-data-only KIS transport protocol and a KIS-primary Korean provider with optional explicitly labelled KRX/DART/KIND supplements.
- Added immutable canonical raw-payload capture and SHA-256 hashing, stable provider source IDs/revisions, complete PIT observation times, KST-local daily/effective instants, and deterministic snapshots.
- Added bounded observable timeout/rate-limit retries, sanitized events, explicit stale/partial/missing/conflict/malformed outcomes, and no fabricated fallback.
- Preserved same-security supplement bars/evidence separately instead of overwriting KIS. Supplement prices are never promoted for a security without a valid KIS primary bar. A degraded supplement lowers a fresh KIS snapshot to `PARTIAL`; cross-provider OHLCV disagreement is `CONFLICT`.
- Added Task 7 repository-compatible symbol correction/rename evidence, listing evidence, and corporate-action evidence. Curated corporate-action identity uses security, action type, and KR effective date rather than provider-native event keys, so equivalent official evidence reconciles while source IDs remain provider-specific.
- Added construction-time uniqueness checks for provider symbols to prevent silent cross-security misattribution.
- Exported the adapter contracts from `prism_core.data.providers` and `prism_core.data`. `krx_data_client.py` was intentionally unchanged because no safe legacy wiring is required for this foundation.

## Task 8 verification and review state

- Strict TDD RED→GREEN evidence was observed for PIT normalization, retry exhaustion, provider conflict, correction-versus-rename semantics, missing/partial/stale outcomes, malformed and semantically invalid supplement isolation, semantic corporate-action identity, stale-supplement aggregation, duplicate provider-symbol rejection, unmatched supplement observability, and per-security KIS-primary enforcement.
- `python -m pytest tests/data/providers/test_kis_provider.py -q` — 21 passed.
- `python -m pytest tests/data tests/storage -q` — 111 passed.
- `python -m pytest tests/runtime tests/safety -q` — 65 passed.
- Canonical CI-equivalent groups — 292 passed, 1 intentionally deselected.
- `python -m compileall -q prism_core tools/audit_broker_boundaries.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed with 0 violations; legacy inventory unchanged.
- `python -m pip check` — no broken requirements; `git diff --check` passed.
- A successful read-only Claude architecture/data-integrity review found one high semantic-validation escape; GPT verified it and corrected the adapter by validating untrusted rows through the strict PIT contract models before normalization. The definitive full review found no critical defect and one conditional medium issue: a supplement-only price could survive for a security missing its KIS primary. GPT reproduced that case RED, added per-security suppression, and returned the provider suite to green. A final targeted post-fix Claude review verified the guard preserves same-security conflict evidence and reported no blocker with a merge recommendation.
- A concurrent frozen-bundle review found one additional medium escape for negative correction revisions. Strict TDD reproduced it; prevalidation now constructs `SecurityAliasEvidence`, the focused/broad suites were rerun, and a targeted post-fix Claude review found no blocker and recommended merge.
- Non-blocking residual assumptions: daily bars use the EOD 15:30 KST convention; curated corporate-action identity assumes at most one same-type action per security/effective date; batch validation conservatively rejects a whole provider envelope when any row is malformed. No runtime wiring relies on these dormant contracts yet.
- Feature commit `14a5364a297df7e26d45e23f2e520ff7b4a15a6c` was pushed and PR #8 (`https://github.com/mienne/prism-insight/pull/8`) opened against `mienne/prism-insight:main`. CI run 30072490779 passed Python 3.10/3.11/3.12 against that exact head.
- This handoff checkpoint changes the feature head. Fresh exact-head CI, squash merge, post-merge `origin/main`/CI verification, and successor creation remain pending.

## Task 8 safety and side effects

- Tests use fixtures and temporary SQLite databases only. No live KIS/KRX/DART/KIND or AgentNews request occurred.
- No credential lookup/output/change, broker/account/order call, Telegram send, user/legacy database access, runtime activation, or deployment occurred. The scoped feature branch was committed/pushed and PR #8 was opened; no merge has occurred at this checkpoint.

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
