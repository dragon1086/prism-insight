# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1D Task 22 — Telegram config/auth/publisher
- Branch: `prism-insight/t_55f5e8e6-prism-phase-1d-task-22-telegram-config-a`
- Base: `origin/main` at `e6478b92bf0db8a0baa173389378e8eae8da21ca` (Task 21 query/report wiring merge)
- Implementation state: locally complete, committed on the task branch, fully verified, and independently reviewed; push, PR, exact-head hosted CI, squash merge, and post-merge verification remain
- Runtime state: target Telegram contracts and opt-in Bot API transport exist, but no application entrypoint, scheduler, durable ops store, long-polling command app, or user runtime configuration is wired

## Implemented scope

- Added `prism_core.telegram.TelegramConfig` with transport disabled by default, explicit `PRISM_TELEGRAM_ENABLED`, one bot token, one allowlisted chat, and one allowlisted user; secret and allowlist values are excluded from representations.
- Added an exact two-dimensional `TelegramAuthorizer`; both chat ID and user ID must match before an inbound surface can be accepted.
- Added a fake-by-default async `TelegramPublisher` with deterministic JSON dry-runs, strict envelope validation, per-report dedupe keys, concurrent in-flight duplicate suppression, separate report/smoke rate-limit buckets, and append-only audit sink contracts.
- Added opt-in `TelegramBotApiTransport` using async-safe stdlib request execution. Bot API errors are sanitized: definitive `ok:false` responses are audited as retryable rejections, while network, malformed, and ambiguous outcomes are audited as UNKNOWN and block blind retry.
- Added capability-bound smoke publication that consumes the existing `TelegramTestSendCapability`; payloads retain `[TEST]`, environment, request ID, and dedupe key and must match the publisher's chat/user allowlist.
- Preserved `TELEGRAM_CHANNEL_ID` only as a warned fallback behind `TELEGRAM_ALLOWED_CHAT_ID` in both the target config and the narrow legacy `telegram_config.py` seam. `telegram_ai_bot.py` was not changed.
- Added checked-in Python 3.10/3.11/3.12 CI execution for `tests/telegram`.

## Contract decisions

- Fake transport, process-local dedupe, process-local rate limiting, and process-local audit are deterministic defaults for unit/CI. Durable cross-process dedupe/audit and scheduler ownership are intentionally deferred to plan Task 24 and must be injected before scheduled operational publication.
- Dry-run validates and serializes an exact publication envelope but does not claim a dedupe key, consume rate capacity, or call a transport.
- Dedupe claim is synchronous and atomic at the protocol boundary. A send attempt records `ATTEMPTED` before transport invocation; a concurrent or completed duplicate records `DUPLICATE` and does not call the transport.
- A definitive Bot API negative response releases the claim for an intentional retry. An ambiguous transport outcome moves the key to UNKNOWN and raises `PublicationOutcomeUnknown`; automatic retry is refused to avoid double-send.
- The real transport is never selected by environment name. It must be explicitly constructed and injected from a complete enabled target config. No credential value appears in config/transport/audit representations or sanitized errors.
- Report and smoke rate limits use separate buckets for the same allowlisted chat. Numeric policy and durable operational configuration remain Task 24 wiring concerns.

## Verification

- Strict RED→GREEN evidence was observed for default config, env loading/redaction, warned fallback, chat+user auth, dry-run no transport, duplicate suppression, bounded smoke, rate limiting, real transport request contract, sanitized errors, known rejection versus unknown outcome, malformed response handling, concurrent duplicate suppression, and HTTPError JSON parsing.
- `python -m pytest tests/telegram -q` — 32 passed.
- Exact checked-in local CI pytest groups — 1,080 passed, 1 intentionally deselected.
- `python -m compileall -q prism_core prism_app stock_analysis_orchestrator.py prism-us/us_stock_analysis_orchestrator.py tools/audit_broker_boundaries.py telegram_config.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed, 0 violations; legacy inventory unchanged.
- `python -m pip check` — no broken requirements.
- `git diff --check` — passed.

## Independent review

- Read-only Claude review found no CRITICAL/HIGH defects and initially identified three material completeness concerns: definitive Bot API rejection was conflated with UNKNOWN, report/smoke shared one rate bucket, and stdlib HTTP errors did not parse Telegram's `ok:false` body.
- GPT accepted and remediated all three with focused RED→GREEN tests: definitive rejection is retryable while ambiguous outcomes remain blocked; report/smoke buckets are separate; and HTTPError JSON bodies preserve definitive Bot API negatives while malformed/network errors remain UNKNOWN.
- GPT also accepted low-risk hardening: both allowlist comparisons are always evaluated and the dedupe protocol now states its atomic-claim requirement.
- Two targeted follow-up Claude reviews found no unresolved CRITICAL/HIGH/MEDIUM defect and recommended shipping Task 22. Durable cross-process stores and terminal audit recovery remain explicitly deferred to Task 24.

## State separation

- Foundation: complete — target config/auth/publisher protocols, fake transport, opt-in Bot API transport, tests, and CI gate exist.
- CI enforcement: complete — `tests/telegram` is part of the checked-in Python 3.10/3.11/3.12 matrix.
- Runtime wiring: not added — no `prism_app.telegram_bot`, report job composition root, scheduler, or legacy bot replacement consumes these contracts yet.
- Operational behavior: exercised only with fake/injected transports and temporary in-memory stores; no Bot API network call or external message occurred.
- Operated readiness: not claimed — local token/allowlists, durable ops-backed dedupe/audit, scheduler ownership, monitoring, and bounded live smoke remain unverified.

## Side effects and safety

- No Telegram live message, provider/model/AgentNews fetch, broker/account/order/OrderIntent/KIS demo/internal-paper/live call, credential access/change, user/legacy database mutation, deployment, or runtime activation occurred.
- Read-only network effects were limited to Git fetch and Claude Code review. Local commits exist; no push, PR, or merge has occurred at this checkpoint.

## Merge state and next task

- Merge state: local Task 22 implementation and review gates are green and the intended diff is committed. Push the task branch, open the PR, verify exact-feature-head Python 3.10/3.11/3.12 CI, guarded squash-merge, then verify post-merge CI and `origin/main` ancestry.
- Next approved task after verified merge only: Phase 1D Task 23 — read-only Telegram conversational app (`prism_app/telegram_bot.py` and `prism_core/telegram/commands.py`) over stored `QueryService` data. Do not expand `telegram_ai_bot.py` or add mutating/broker/account commands.
- Stop conditions remain: block on merge conflict, unresolved high/medium auth/external-effect review, compatibility break, credential exposure, live message outside bounded allowlisted smoke controls, destructive/user-data mutation, broker/live/risk scope, repeated gate failure, or unverifiable exact-head CI/merge state.
