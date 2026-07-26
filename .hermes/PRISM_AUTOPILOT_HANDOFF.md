# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1D Task 20 — thin application services
- Branch: `prism-insight/t_a0f83a71-prism-phase-1d-task-20-thin-application`
- Base: `origin/main` at `6e2d1e6f96bcc14ece1b3580097c57f7d8f3a4e8`
- Implementation state: bounded application services, opt-in legacy seams, independent read-only review, definitive local gates, and frozen manifest inspection are complete; GitHub delivery remains
- Runtime state: new services are available only through explicit dependency injection; legacy KR/US defaults are unchanged and no production composition root, scheduler, provider/model transport, dashboard, Telegram transport, paper ledger, account, broker, order, or live path is activated

## Implemented scope

- Added `prism_app.daily_pipeline` with explicit `RuntimeSettings`, default-off application capabilities, injected snapshot/evaluator/repository/publication ports, deterministic market/date/run-type job identity, and durable SQLite app-run readback.
- Acquires one provider-composed snapshot, reuses the existing Task 7A `LeadershipRepository`, evaluates the deterministic quality gate before proposal work, and evaluates exact active `SWING_V1` and `TREND_V1` definitions independently.
- Persists leadership evidence and the complete analysis before optional publication. A publication exception returns a failure result without erasing persisted analysis.
- Non-ACCEPT quality persists an auditable skip result and suppresses all new proposal evaluation while retaining leadership/report evidence.
- Added read-only `QueryService` for leadership, exact strategy/version proposal/outcome history, and separately named inert SHADOW evaluation material; SHADOW has zero score/proposal/policy influence.
- Added thin `OutcomeTracker` over the existing append-only outcome repository and thin `ReportService` over an injected publisher. No transport, scheduler, report read-model expansion, or broker behavior was added.
- Added opt-in `run_application_pipeline` seams to the KR/US legacy orchestrators. Existing constructor behavior and `run_full_pipeline` remain the default and do not consume the new service.
- Added an explicit CI matrix step for `tests/app` and expanded compile coverage to `prism_app` and both touched wrappers.

## Contract decisions and compatibility

- Application idempotency identity is `daily:<market>:<ISO date>:<normalized run_type>` with deterministic UUIDv5 storage identity. A persisted replay does not reacquire, reevaluate, or republish.
- The existing OPS `job_runs` table stores the completed application analysis; Task 24 retains ownership of leases, attempts, heartbeat, concurrency, and publication retry orchestration.
- Research and OPS databases cannot share one SQLite transaction. A regression test proves that a transient OPS save failure can safely retry because Task 7A leadership ingestion is canonical and idempotent.
- Concurrent duplicate work/publication is not claimed safe before Task 24 lease wiring. This slice is intended for one scheduled owner.
- Publication failure durability means analysis remains queryable; automatic redelivery ownership is deferred to later publication/operations tasks rather than silently inventing Task 21/24 semantics here.
- REPORT_ONLY and REJECT both suppress new proposals. REPORT_ONLY remains a policy disposition represented in the persisted quality decision, not an observed quality status.
- The `shadow_evaluation_enabled` capability is reserved but currently does not activate production proposal input; SHADOW retrieval remains read-only and inert.

## TDD and verification checkpoint

- Observed RED→GREEN for the application package/import path, persistence-before-publication, replay idempotency, stale-core fail-closed behavior, publication failure durability, explicit publication transport gating, Phase 1 broker rejection, SHADOW isolation, outcome append/readback, and legacy opt-in wrapper seams.
- Focused app suite: `python -m pytest tests/app -q` — 13 passed.
- Focused app/core regression suite before final remediation — 145 passed.
- Compile coverage for `prism_core`, `prism_app`, both wrappers, and broker audit tool passed.
- CI YAML parses successfully and `git diff --check` passes.
- Definitive checked-in CI-equivalent groups — 967 passed with 1 intentional deselection.
- Broker-boundary audit passed with 0 violations; 22 existing legacy dangerous findings remain inventory-only.
- `python -m pip check` reported no broken requirements.
- Frozen staged manifest contains 12 intended files; compile, CI YAML parse, `git diff --check`, private-artifact/extension scans, and staged diff inspection passed.

## Independent review

- Pre-implementation verified read-only Claude architecture review ran before semantics freeze; its process exited successfully, but the substantive stdout was not retained across the prior worker's context compaction, so no findings are attributed from that call.
- Final open-repository Claude review exhausted 12 turns and was rejected as unusable.
- The verified frozen-snapshot fallback reviewed 1,964 lines / 71,679 bytes (SHA-256 `947a518c7facdbb28dde4d3b15b80163d8e818aed8e1e9eb685edc09f203de07`) using only `Read`; exit 0, empty stderr, and a complete stdout verdict were inspected.
- Claude found no CRITICAL/HIGH defect and recommended MERGEABLE contingent on Task 7A leadership idempotency. Hermes independently verified canonical idempotent `LeadershipRepository.ingest` and added a retry regression test plus an exact strategy-version mismatch regression test; both pass.
- Accepted residuals: publication redelivery and lease/concurrent-owner protection belong to Task 21/24; empty strategy evidence and the reserved SHADOW capability are non-blocking current-contract observations. No unresolved CRITICAL/HIGH/MEDIUM implementation defect remains.

## Side effects and safety

- Network activity through this checkpoint: Git fetch and read-only Claude Code review only.
- No live PRISM provider/model request, external message, credential read/change, user/legacy database access, account/broker/order call, `OrderIntent`, KIS demo, deployment, runtime activation, paper/live trading, commit, push, PR, or merge occurred.
- Only temporary test SQLite files and `/tmp` review artifacts were created.

## Remaining closeout

1. Restage the final handoff, rerun documentation/diff/private-artifact checks, commit, push, and open the Task 20 PR.
2. Verify exact feature-head Python 3.10/3.11/3.12 CI and the explicit app step in every matrix job; inspect branch protection/ruleset status.
3. Squash merge only when green; verify post-merge `origin/main`, post-merge CI, expected files, and remote branch deletion.
4. Create only the bounded Task 21 successor after merge verification, then complete the Kanban card with the returned task ID.

Stop conditions remain: block on compatibility change, destructive/user-data migration, provider/credential/account/broker/live/risk scope, merge conflict, unresolved HIGH/CRITICAL review, or unverifiable required gate.
