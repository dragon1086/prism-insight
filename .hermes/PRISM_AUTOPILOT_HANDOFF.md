# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1D Task 25 — dashboard data contract separation
- Branch: `wt/t_989ef6cb`
- Base: `origin/main` at `4df4c3ba30bc2187338db56409f99a2842dd6e63` (Task 24 merge)
- Implementation state: locally complete, definitively verified, and independently reviewed; commit, push, PR, exact-head CI, guarded merge, and post-merge verification remain
- Runtime state: a read-only JSON export foundation and localhost npm scripts exist; the legacy React page/components are intentionally not rewired by this plan-bounded task and still reference removed legacy types behind the pre-existing `ignoreBuildErrors` setting

## Implemented scope

- Added separate TypeScript contracts for authoritative research, internal-paper, and operations data, composed by `DashboardData` without KIS account, real portfolio, or real-trading fields.
- Added `prism_app.dashboard_export` to read three explicitly supplied existing SQLite stores through `mode=ro` connections, enforce query-only reads, apply PIT boundaries, preserve provenance, and atomically replace one JSON export.
- Export sections cover data freshness/jobs, latest KR/US daily leaders, separate `SWING_V1` and `TREND_V1` proposals, scenario/evidence/falsifiers, honest unavailable research/OOS state, SHADOW feedback, and internal-paper books/positions/NAV/state counts.
- Replaced the two legacy dashboard generators with thin wrappers over the unified safe exporter. They no longer load `.env`, KIS config, trading/account adapters, yfinance, translation models, or the mixed legacy tracking database; all research/paper/ops paths are explicit required CLI arguments.
- Bound checked-in dashboard `dev` and `start` npm scripts to `127.0.0.1` and added an explicit fail-closed `tests/dashboard` CI matrix step.

## Contract decisions

- `prism_dashboard_v1` contains top-level `research`, `paper`, and `ops` boundaries and identifies itself as localhost-only; no account-shaped field is accepted or emitted.
- Latest daily leaders are restricted to the latest snapshot per market at the declared PIT boundary, with revision-aware observation selection.
- Proposal and SHADOW feedback reads require both `available_at <= as_of` and stored `as_of_at <= as_of`, and select the latest available revision without exporting raw model output.
- Research/OOS is visibly `UNAVAILABLE` because the current experiment registry has no persistent dashboard read contract; no result is inferred or fabricated.
- Internal paper reads only `paper.sqlite`; empty stores report `FOUNDATION_ONLY`. No Task 26 broker simulation or order lifecycle behavior was implemented.
- The legacy React page is outside Task 25's authoritative file list and is not runtime-wired to the new envelope. This task establishes and CI-enforces the safe data boundary, not a claim that the current UI renders the new sections.

## Verification

- Strict RED→GREEN observed for the missing exporter module, explicit CI discovery, safe wrapper execution, and latest-snapshot daily leader selection.
- `python -m pytest tests/dashboard -q` — 7 passed.
- Exact checked-in local CI pytest groups — 1,175 passed, 1 intentionally deselected.
- Populated hand-built fixtures verify proposal/SHADOW JSON mapping; populated stores created by the real versioned migrations verify every exporter SQL statement against authoritative research/paper/ops schemas.
- `python -m compileall -q prism_app/dashboard_export.py examples/generate_dashboard_json.py examples/generate_us_dashboard_json.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed with 0 violations; legacy inventory unchanged.
- `python -m pip check` — no broken requirements.
- `npm ci --legacy-peer-deps && npm run build` in `examples/dashboard` — production build passed. Plain `npm ci` is blocked by the pre-existing React 19 / vaul peer dependency conflict.
- Isolated public contracts passed `npx tsc --noEmit --strict --skipLibCheck --target ES2020 --moduleResolution node --module commonjs types/research.ts types/paper.ts types/ops.ts types/dashboard.ts`.
- Full dashboard `npx tsc --noEmit` remains red from pre-existing component/UI typing issues plus expected legacy-page incompatibility after removing the old mixed contract; Next currently skips type validation by repository configuration.
- `git diff --check`, JSON/YAML parsing, changed-file inspection, and privacy scan — passed; privacy matches were test-only forbidden-token literals.

## Independent review

- The first usable read-only Claude review found no CRITICAL/HIGH defect. It classified legacy React UI wiring as a MEDIUM scope/claim limitation rather than a Task 25 blocker because the authoritative file list bounds this slice to contracts/exporters and no real-account data can reach the new JSON.
- Claude identified two LOW gaps: populated migrated-schema coverage and old leadership snapshots appearing in a daily view. GPT accepted both and remediated them; the latter used observed RED→GREEN.
- A targeted follow-up review found both resolved and no new CRITICAL/HIGH/MEDIUM defect, recommending ship. Its position tie-break LOW was rejected because the authoritative paper migration already enforces `UNIQUE (book_id, security_id, as_of_at)`; its direct-connection note is bounded to injected tests while all path-based exports use hard `mode=ro` SQLite URIs.

## State separation

- Foundation: complete locally — separated contracts, safe read-only exporter, thin wrappers, PIT/provenance rules, atomic JSON output, and localhost script defaults exist.
- CI enforcement: complete locally — `tests/dashboard` is an explicit matrix step and contract tests prohibit real-account fields/fetch paths and non-local npm defaults.
- Runtime/application wiring: partial by design — generators call the exporter and npm scripts bind localhost; legacy React page/components do not consume the new envelope.
- Operational behavior: exercised only with temporary fixture/migrated SQLite stores and temporary JSON files; no user store was opened or mutated.
- Operated readiness: not claimed — no user databases were initialized/read, no installed dashboard server was started, no real scheduled export ran, and no real-data UI rendering was verified.

## Side effects and safety

- No Telegram/macOS notification, launchd installation/load, provider/model/AgentNews fetch, broker/account/order/OrderIntent/KIS demo/external paper/live call, credential access/change, or user/legacy database read/mutation occurred.
- Network effects so far: Git fetch, npm package installation for local build verification, and two usable read-only Claude reviews (plus one unusable deferred Claude response that was rejected).
- No commit, push, PR, or merge has occurred at this checkpoint.

## Merge state and next task

- Merge state: implementation, independent review, exact checked-in local CI groups, compile/type-contract/build/broker/privacy/diff gates are green against current `origin/main` `4df4c3b`. Freeze/commit/push/open PR and verify exact-feature-head Python 3.10/3.11/3.12 CI before guarded squash merge and post-merge verification.
- Next approved task after verified merge only: Phase 1E Task 26 — deterministic simulated broker, bounded to the authoritative plan and without external broker/KIS demo/live effects.
- Stop conditions remain: merge conflict, unresolved high/medium schema/PIT/security review, destructive/user-data mutation, credentials/account/broker/live/risk scope, repeated gate failure, or unverifiable exact-head CI/merge state.
