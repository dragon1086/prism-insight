# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1D Task 24 — launchd jobs, watchdog, and ops DB
- Branch: `prism-insight/t_3d90aaef-prism-phase-1d-task-24-launchd-jobs-watc`
- Base: `origin/main` at `3e7172b05d017df1eaa84781f1965568bc904863` (Task 23 merge)
- Implementation state: locally complete, fully verified, and independently reviewed; commit, push, PR, exact-head hosted CI, guarded squash merge, and post-merge verification remain
- Runtime state: durable ops primitives and inert launchd templates exist, but no template was substituted, installed, or loaded and no production startup root invokes them

## Implemented scope

- Added `prism_core.ops.job_runs.JobRunStore` over the existing versioned `ops.sqlite` migration with atomic single-owner leases, expiry takeover, immutable run input, heartbeat renewal, success/error completion, last-success lookup, persistent-worker activity lookup, append-only health transitions, and append-only successful-delivery acknowledgements.
- Expired lease takeover atomically marks prior same-job `RUNNING` attempts `ABANDONED`, preventing crashed attempts from poisoning later health checks while stale owners remain unable to write.
- Added `prism_app.watchdog` with wake/restart catch-up classification, one-shot catch-up execution, periodic lease renewal, persistent-worker heartbeat checks, ERROR/RECOVERY transition suppression, same-publisher Telegram alerts, and fail-closed macOS fallback.
- Telegram `RATE_LIMITED` is treated as undelivered. A successfully delivered Telegram or macOS alert gets a durable `JOB_HEALTH_DELIVERY` record; if both channels fail, the durable transition remains and the alert is retried on the next tick.
- Added opt-in `OsascriptMacOSNotifier`; it is disabled by default. Tests use in-memory/failing notifiers and fake/injected Telegram transports only.
- Added three valid launchd plist templates for daily calendar scheduling, Telegram supervision, and five-minute watchdog cadence. Every template is explicitly inert, contains placeholders rather than secrets, and requires separate approval before installation/loading.
- Added explicit Python 3.10/3.11/3.12 CI execution of `python -m pytest tests/ops -q`.

## Contract decisions

- Catch-up is due only after the scheduled occurrence, inside its bounded catch-up window, and when no success at or after that occurrence exists.
- Lease duration is positive, ownership is keyed by validated `job_key`, and all lease claim/takeover operations run in one `BEGIN IMMEDIATE` transaction.
- `JobHealthCheck` is intentionally for persistent workers such as Telegram; completed scheduled jobs use `last_success` and catch-up policy instead of heartbeat health.
- Catch-up runners receive only `JobExecution` values and must not share the watchdog's ops SQLite connection.
- A RECOVERY user notification is emitted only after an ERROR was successfully delivered. If both ERROR channels fail, the ops DB retains attempts but a later healthy state does not generate a contextless RECOVERY message.
- Launchd files are templates, not installed jobs. Placeholder substitution, executable composition, credentials/allowlists, real Telegram/macOS smoke, installation, loading, and operated observation remain explicit operational gates.

## Verification

- Strict RED→GREEN was observed for missing ops/watchdog modules, lease ownership and concurrency, catch-up, heartbeat/last-success, alert fallback/recovery, rate-limited fallback, fully undelivered retry, abandoned-run reconciliation, deterministic clocking, template inertness, and CI discovery.
- `python -m pytest tests/ops -q` — 22 passed.
- Exact checked-in local CI pytest groups — 1,168 passed, 1 intentionally deselected.
- `python -m compileall -q prism_core prism_app stock_analysis_orchestrator.py prism-us/us_stock_analysis_orchestrator.py tools/audit_broker_boundaries.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed with 0 violations; legacy inventory unchanged.
- `python -m pip check` — no broken requirements.
- `plutil -lint ops/launchd/*.plist.template` — all three passed.
- `git diff --check` — passed.

## Independent review

- Initial read-only Claude Opus review found two HIGH issues: templates did not state their lack of runtime wiring and a catch-up test mixed a fixed `now` with the real clock. It also identified abandoned `RUNNING` attempts after lease takeover as MEDIUM.
- GPT accepted and remediated all three: inert-template gates are explicit and tested, the catch-up test injects a fixed clock, and takeover atomically closes abandoned attempts with focused RED→GREEN coverage.
- GPT also remediated rate-limited Telegram fallback and durable delivery acknowledgement/retry semantics found during local verification.
- Targeted follow-up read-only Claude Opus review found all prior HIGH/MEDIUM findings resolved or explicitly bounded/documented, found no new CRITICAL/HIGH/MEDIUM issue, and recommended shipping.
- Accepted residuals: append-only health-state lookup is O(n) at personal-Mac scale; persistent-worker and runner-connection contracts are documented rather than type-enforced; real osascript delivery and launchd activation remain unverified operational gates.

## State separation

- Foundation: complete — durable lease/run/heartbeat/health repositories, catch-up/watchdog primitives, fake/injected alert behavior, and inert launchd templates exist.
- CI enforcement: complete — `tests/ops` is an explicit fail-closed step in every checked-in Python 3.10/3.11/3.12 matrix job.
- Runtime/application wiring: partial by design — the primitives compose with the existing Telegram publisher, but no production executable consumes `--ops-db` and templates are explicitly inert.
- Operational behavior: exercised only with temporary SQLite files, fake/injected Telegram, and in-memory/failing macOS transports; no external alert or installed job ran.
- Operated readiness: not claimed — no template substitution/install/load, user ops DB initialization, credentials/allowlist verification, actual notification smoke, restart drill, monitoring observation, or backup/recovery operation occurred.

## Side effects and safety

- No Telegram live message, Bot API request, macOS notification, launchd installation/load, provider/model/AgentNews fetch, broker/account/order/OrderIntent/KIS demo/internal-paper/live call, credential access/change, or user/legacy database mutation occurred.
- Read-only network effects were limited to Git fetch and two Claude Code review calls. No commit, push, PR, or merge has occurred at this checkpoint.

## Merge state and next task

- Merge state: local Task 24 implementation, focused/broad verification, plist lint, and independent review gates are green. Commit the frozen intended diff, push, open the PR, verify exact-feature-head Python 3.10/3.11/3.12 CI, guarded squash-merge, then verify post-merge CI and `origin/main` ancestry.
- Next approved task after verified merge only: Phase 1D Task 25 — dashboard data contract separation. Do not implement Task 25 in this branch.
- Stop conditions remain: block on merge conflict, unresolved high/medium schema/concurrency/external-effect review, compatibility break, credential exposure, live notification outside bounded allowlisted smoke controls, destructive/user-data mutation, broker/live/risk scope, repeated gate failure, or unverifiable exact-head CI/merge state.