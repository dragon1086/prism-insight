# PRISM Guarded-Autopilot Handoff

## Current milestone — Phase 1A Task 8.1

- Branch: `prism-insight/t_ccfe72a5-prism-phase-1a-task-8.1-kis-live-market`
- State: implementation, fixture tests, bounded live KIS market-data smoke, GPT-controlled local gates, and verified read-only Claude review are green. The feature commit was rebased onto `origin/main` after resolving the handoff-only conflict; push, PR, exact-head CI, squash merge, and post-merge closeout remain pending at this checkpoint.
- Runtime state: a concrete KIS production-host HTTP/OAuth transport now exists behind the existing injected `KISMarketDataProvider` boundary and was exercised explicitly by the opt-in live smoke. No application composition root, scheduler, dashboard, Telegram path, account API, broker/order/cancel/replace path, or user database is wired or changed.

## Task 8.1 implemented scope

- Added immutable, redacted `KISMarketDataCredentials`, a bounded `AioHttpKISRequester`, and `KISHTTPTransport` for exactly the production KIS OAuth token endpoint plus the domestic daily quotation endpoint.
- Pinned transport URLs to HTTPS, the allowlisted production hostname, port 9443, and the two literal paths. Static/runtime tests reject broker imports, account/order methods, non-allowlisted hosts, ports, paths, query strings, and fragments.
- Added bounded timeout, response-size, and inter-request spacing behavior; concurrent fetches share a token lock and cached token with expiry margin. 429 responses follow the existing bounded provider retry path; non-retryable HTTP/schema failures propagate fail-closed without a fabricated snapshot.
- Preserved exact quotation wire SHA-256 provenance while storing only per-fetch sanitized endpoint/status/received-at evidence. OAuth response hashes and response bodies are not retained in evidence; credential and response representations are redacted.
- Normalized the live quotation through the existing `SecurityId`, `ObservationTime`, `PriceBar`, `RawProviderPayload`, and `MarketSnapshot` contracts. Completed same-day daily bars are observed at 15:30 KST, conservatively available at 15:31, and requests before 15:31 fail closed.
- Corrected the provider ingestion timestamp to be sampled after all transport work rather than before the HTTP response; a two-tick fixture proves `request_started_at <= response <= ingested_at` semantics.
- Exported the transport contracts from `prism_core.data.providers` and `prism_core.data`. Legacy KIS trading modules and all current application callers remain unchanged.
- Added an explicitly gated `live_kis` pytest marker. Default unit/CI execution skips the live smoke; live execution requires `PRISM_RUN_KIS_LIVE=1` and never prints credentials, tokens, headers, account data, prices, or raw response bodies.

## Task 8.1 live evidence, verification, and review

- Final authorized live smoke on 2026-07-24 used KIS symbol `005930` only. OAuth POST returned 200 at `17:30:04.346088+09:00`; quotation GET returned 200 at `17:30:04.405159+09:00`; provider ingestion completed at `17:30:04.409697+09:00`.
- The live snapshot was `FRESH`; observed/available/as-of were `15:30:00`, `15:31:00`, and `17:30:04.142858` KST. The expected seven normalized fields were present, and the quotation wire hash matched the stored bar source hash. No account, portfolio, order, cancel/replace, or broker effect occurred.
- `python -m pytest tests/data/providers/test_kis_http_transport.py tests/data/providers/test_kis_provider.py tests/integration/test_kis_live_market_data.py -q` — 39 passed, 1 default live skip.
- Post-rebase `PRISM_RUN_KIS_LIVE=1 python -m pytest tests/integration/test_kis_live_market_data.py -q` — 1 passed.
- Post-rebase `python -m pytest tests/data tests/storage -q` — 177 passed.
- Post-rebase `python -m pytest tests/runtime tests/safety -q` — 70 passed.
- Canonical CI-equivalent remaining groups — 292 passed, 1 intentionally deselected.
- `python -m compileall -q prism_core tools/audit_broker_boundaries.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed with 0 violations; legacy dangerous inventory remains informational and unchanged.
- `python -m pip check` — no broken requirements; `git diff --check` passed.
- Initial verified Claude read-only review found two MEDIUM issues: shared mutable transport evidence under concurrency and the need to pin intentional propagation of non-retryable failures. GPT replaced shared evidence with immutable per-payload evidence and added decisive provider-level 429/non-retryable tests.
- GPT also accepted the review's LOW chunk-read robustness finding, reproduced a short-read truncation RED, accumulated bounded chunks until EOF, and proved both short reads and oversize rejection.
- A targeted post-fix Claude Opus 4.8 review verified all prior findings resolved, found no HIGH/MEDIUM blocker, and recommended merge. GPT independently fixed its remaining LOW live-smoke threshold mismatch; the other LOW notes are intentional/pre-existing: same-day-after-close scope, full-call multi-symbol retries, uniform retry-event timestamps, and snapshot ingestion identity.

## Task 8.1 safety and side effects

- External effects were limited to explicitly authorized read-only KIS market-data requests: three successful OAuth token POSTs and three successful quotation GETs across the initial and two evidence/final live smokes. There were no account, balance, order, cancel/replace, broker, messaging, database, deployment, credential-change, commit, push, PR, or merge effects at this checkpoint.
- Real KIS credential values and bearer tokens were never printed, stored in repository files, returned in test evidence, or included in this handoff. A changed-file scan found no match for the active KIS environment credential values and no private database/log/config artifact.
- Foundation and explicit operated smoke are green; production application wiring and operational scheduling remain deferred to the later composition/runtime task.

## Previous milestone — Phase 1A Task 9.2

- Task 9.2 added strict synthetic FMP pagination metadata, deterministic sequential multi-page collection with one aggregate request budget, ordered request/page identities, retained safe incomplete evidence without partial normalized bars, page-level provenance, conflict/repeat/skip classification, and fail-closed rejection of non-429 non-success pages.
- Its focused provider suite passed 48 tests; data/storage passed 159 tests; runtime/safety passed 65 tests; canonical CI-equivalent groups passed 292 tests with 1 intentionally deselected. Compileall, broker-boundary audit, pip check, and diff checks passed.
- Verified read-only Claude reviews drove the aggregate-budget design and found a valid-looking non-2xx laundering path plus skipped-page taxonomy ambiguity; both were reproduced and fixed with decisive tests. A targeted post-fix review found no remaining HIGH/MEDIUM defect.
- Task 9.2 remained fixture-only and dormant: no live FMP/yfinance/SEC request, credential access, user database, broker/account/order call, messaging effect, runtime activation, or deployment occurred.

## Previous milestone — Phase 1A Task 9.1

- Task 9.1 was squash-merged via PR #9 at `9b3ecdc`; its FMP fixture-only transport foundation remains dormant with no concrete HTTP client or runtime wiring.
- Its focused and canonical local gates, exact-head CI, independent review, and post-merge verification were green; no provider credential lookup, live FMP request, broker/account/order effect, or user-database effect occurred.

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
