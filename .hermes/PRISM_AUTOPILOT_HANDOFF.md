# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1D Task 23 — read-only Telegram conversational app
- Branch: `prism-insight/t_daff17b3-prism-phase-1d-task-23-read-only-telegra`
- Base: `origin/main` at `093a5a425b03a3d8b325f2749b2a8dcb364236d8` (Task 22 Telegram contracts merge)
- Implementation state: locally complete, fully verified, and independently reviewed; commit, push, PR, exact-head hosted CI, guarded squash merge, and post-merge verification remain
- Runtime state: an explicitly composable long-polling read-only app and Bot API update source exist, but no startup entrypoint, scheduler, launchd job, durable ops-backed offset/audit/dedupe store, or user runtime configuration is wired

## Implemented scope

- Added `prism_core.telegram.commands` with a closed read-only command enum and strict argument validation for `/help`, `/status`, `/daily`, `/weekly`, `/symbol`, `/portfolio`, `/paper`, and `/health`.
- Added deterministic natural-language classification that resolves only to the same fixed read commands or bounded stored-report search. Mutation, order, broker/account, live/demo, risk, kill-switch, credential, prompt, policy, and configuration intents fail closed before any query.
- Added `prism_app.telegram_bot.TelegramConversationApp`, which authorizes both Telegram chat ID and user ID before parsing, querying, or replying and delegates all data reads to `QueryService`.
- Added an opt-in async `TelegramBotApiUpdateSource` for `getUpdates`, validated update batches, skip-and-drain handling for non-text/fromless/blank events, monotonic offsets, bounded transient polling backoff, and publisher-backed response dedupe/rate/audit behavior.
- Extended `QueryService` with point-in-time `latest_leadership(market)` and bounded parameterized `search_reports(query)` reads over persisted research data.
- Reused Task 22 `TelegramAuthorizer`, `TelegramPublisher`, fake transport, dedupe, rate-limit, audit, sanitized Bot API requester, and disabled-by-default configuration contracts. `telegram_ai_bot.py` was not changed.
- Added deterministic tests in `tests/telegram/test_commands.py` and `tests/telegram/test_prompt_injection_boundary.py` for the full allowlist, argument validation, dual authorization, mutation denial, prompt-injection inertness, query delegation, source parsing, offset draining, backoff, duplicate response suppression, unavailable reads, and truthful rate-limit audit state.

## Contract decisions

- Slash commands are a closed enum. Unknown slash commands are rejected; natural language cannot name or select a tool and can only become a fixed read command or `QueryService.search_reports` request.
- Unauthorized updates are silent and record only sanitized inbound metadata (`update_id`, decision status, and fixed command name where available). Chat ID, user ID, raw text, token, credentials, and stored report contents are excluded from representations and audit records.
- Stored report/evidence text is rendered only as inert plain-text response data and is never reparsed as an instruction.
- `/portfolio` and `/paper` honestly report the deferred internal-paper read contract without accessing broker/account surfaces. `/health` honestly reports Task 24 deferral. `/weekly` exposes persisted readiness rather than fabricating a report.
- A poll batch carries processable updates plus the max-seen next offset. Normal unprocessable events are skipped and drained; a publication failure interrupts before the batch offset is committed so unprocessed updates are not silently skipped.
- Telegram polling retries only sanitized `TelegramPollingError` failures with bounded in-process exponential backoff. Durable process supervision, offsets, monitoring, dedupe/audit persistence, and recovery remain Task 24.
- Interactive responses use the Task 22 publisher. App audit distinguishes sent, duplicate, rate-limited, denied, invalid, unauthorized, and unavailable outcomes.

## Verification

- Strict RED→GREEN evidence was observed for missing parser/app modules; closed command parsing; mutation and prompt-injection denial; latest/search QueryService reads; Bot API polling and offsets; fromless/blank event draining; transient polling recovery; duplicate-response suppression; broad read failures; and rate-limit audit fidelity.
- `python -m pytest tests/telegram -q` — 98 passed.
- `python -m pytest tests/app -q` — 19 passed.
- Exact checked-in local CI pytest groups — 1,146 passed, 1 intentionally deselected.
- `python -m compileall -q prism_core prism_app stock_analysis_orchestrator.py prism-us/us_stock_analysis_orchestrator.py tools/audit_broker_boundaries.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed, 0 violations; legacy inventory unchanged.
- `python -m pip check` — no broken requirements.
- `git diff --check` — passed.

## Independent review

- Initial read-only Claude Opus review found no CRITICAL confidentiality/integrity defect and confirmed authorization occurs before parsing/querying, natural-language text cannot choose mutating tools, stored prompt injection is inert, SQL is parameterized, and credentials are redacted.
- GPT accepted and remediated four availability/audit findings with focused RED→GREEN tests: fromless events no longer poison a poll batch; unexpected read errors return sanitized unavailable responses; transient polling failures back off and resume; and rate-limited replies are no longer audited as answered.
- A targeted follow-up found those four resolved and one remaining MEDIUM blank-text poison event. GPT reproduced it RED, added skip-and-drain handling, returned the focused and broad suites to GREEN, and added duplicate-audit coverage.
- Final targeted read-only Claude Opus review found the blank-text issue resolved, no unresolved or new CRITICAL/HIGH/MEDIUM defects, and recommended shipping. Residual LOW items are malformed impossible-under-contract update envelopes, UTF-16 response-length edge handling, and durable publication recovery/audit continuity deferred to Task 24.

## State separation

- Foundation: complete — closed parser, dual-authorized conversational app, polling source, QueryService read extensions, fake/injected tests, and response publication contracts exist.
- CI enforcement: complete — `tests/telegram` and `tests/app` are already executed by the checked-in Python 3.10/3.11/3.12 matrix.
- Runtime/application wiring: partial by design — the app consumes real `QueryService` and Task 22 contracts when explicitly composed, but no production startup root, scheduler, launchd job, or legacy bot replacement invokes it.
- Operational behavior: exercised only with fake/injected Bot API and publication transports plus temporary SQLite stores; no external message or Bot API request occurred.
- Operated readiness: not claimed — credentials/allowlists, durable ops stores, process supervision, monitoring, recovery, and bounded live smoke remain unverified and Task 24-owned.

## Side effects and safety

- No Telegram live message, Bot API request, provider/model/AgentNews fetch, broker/account/order/OrderIntent/KIS demo/internal-paper/live call, credential access/change, user/legacy database mutation, deployment, or runtime activation occurred.
- Read-only network effects were limited to Git fetch and three Claude Code review calls. No commit, push, PR, or merge has occurred at this checkpoint.

## Merge state and next task

- Merge state: local Task 23 implementation and review gates are green. Commit the frozen intended diff, push the task branch, open the PR, verify exact-feature-head Python 3.10/3.11/3.12 CI, guarded squash-merge, then verify post-merge CI and `origin/main` ancestry.
- Next approved task after verified merge only: Phase 1D Task 24 — durable job operations and scheduler/lease/watchdog/runtime wiring. Do not implement Task 24 in this branch.
- Stop conditions remain: block on merge conflict, unresolved high/medium authorization/external-effect review, compatibility break, credential exposure, live message outside bounded allowlisted smoke controls, destructive/user-data mutation, broker/live/risk scope, repeated gate failure, or unverifiable exact-head CI/merge state.
