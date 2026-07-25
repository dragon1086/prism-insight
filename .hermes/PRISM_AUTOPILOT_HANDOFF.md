# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1A Task 9B.3 — macro official point-in-time evidence adapter foundation
- Branch: `prism-insight/t_4851c208-prism-phase-1a-task-9b.3-macro-official`
- Implementation state: fixture contract and approval-gated live-smoke scaffold implemented; final canonical gates and delivery remain
- Runtime state: dormant contracts only; no concrete FRED/ALFRED/ECOS HTTP transport, application caller, scheduler, strategy, LLM, account, broker, or order path is wired

## Implemented scope

- Added `prism_core/data/providers/macro_official.py` with source-separated FRED current-series, ALFRED vintage, and ECOS statistic-search request/evidence contracts.
- Preserved exact source, capability, endpoint identity, series, observation period, observed/available/ingested/as-of times, release, revision, vintage date, raw payload hash, terms/license, quality, and sanitized correlation/fact evidence.
- Added explicit source approval metadata for exact source/capability/endpoint, credential scope, terms/license, cost, rate, call, and validity bounds; live transports fail before I/O without matching durable approval.
- Added fail-closed collection semantics: missing evidence becomes a sanitized `UNAVAILABLE` event; `STALE`, `PARTIAL`, `UNAVAILABLE`, or `CONFLICT` core evidence is never usable; conflicting facts retain every source envelope and emit a conflict event rather than merging.
- Added deterministic fixture tests and a separately marked opt-in live scaffold that cannot make a network request because this task supplies no concrete transport.
- Exported the macro contracts through `prism_core.data.providers` and `prism_core.data`; registered the `live_macro_official` pytest marker.

## Contract decisions

- `available_at` remains the authoritative knowability instant. Observation period, release date, event date, or vintage never substitutes for it.
- `vintage_date` is separately retained and cannot exceed the evaluation `as_of_date`; ALFRED requests require an exact vintage and returned evidence must match it.
- FRED, ALFRED, and ECOS payloads are never normalized into one silent value in this foundation. Optional `fact_key`/`fact_hash` comparison records conflicts while preserving all envelopes.
- Exact endpoint identities omit query strings and credentials. A future concrete transport owns safe request construction only after durable source approval.
- Approval attempts are counted before I/O so failed requests cannot create an unbounded retry loop; authorization-bound failures remain hard fail-closed errors.

## Verification

- RED: initial fixture test failed because `macro_official` did not exist; GREEN: 1 passed.
- RED: provider/public-contract slice failed on missing contracts/exports; GREEN: focused suite passed.
- RED: future vintage regression failed because no guard existed; GREEN: targeted test passed after adding `vintage_date <= as_of_date.date()`.
- Focused result: `python -m pytest tests/data/providers/test_macro_official_provider.py tests/integration/test_macro_official_live.py -q` — 16 passed, 1 intentionally skipped.
- Final canonical local groups — 678 passed, 1 intentionally deselected: runtime/safety 72; storage 44; data 270; LLM 111; remaining exact CI groups 181 with the deselection.
- `python -m compileall -q prism_core tools/audit_broker_boundaries.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed with 0 violations; legacy inventory unchanged.
- `python -m pip check` — no broken requirements.
- `git diff --check` — passed before this final handoff refresh and is rerun against the frozen tree before delivery.

## Independent review

- Initial verified read-only Claude review returned exit 0 with empty stderr and found no CRITICAL/HIGH issue. It raised one MEDIUM PIT inconsistency: a future vintage could contradict the as-of boundary, plus LOW coverage/documentation gaps for integrity/live gates and pre-I/O accounting.
- GPT accepted the vintage finding, observed RED then GREEN, added transport identity/as-of/ALFRED-vintage and live clock/expiry tests, and documented pre-I/O attempt accounting.
- Follow-up verified read-only Claude review returned exit 0 with empty stderr, marked prior findings resolved, found no remaining CRITICAL/HIGH/MEDIUM defect, and recommended approval. Informational residuals: date-level timezone semantics would need revisiting if ECOS adds request-level vintage semantics; authorization-bound failures intentionally hard-abort while upstream fetch failures become quality events.

## Side effects and safety

- No FRED, ALFRED, ECOS, or other provider request occurred.
- No credential was read, printed, changed, or created.
- No external message, account/broker/order call, user database access, migration, deployment, runtime activation, live trading effect, commit, push, PR, or merge has occurred at this checkpoint.

## Remaining closeout

1. Run final frozen-tree diff/private-artifact/status inspection.
2. Commit, push, open a PR, verify exact-head Python 3.10/3.11/3.12 CI, squash merge, and verify post-merge CI and remote branch deletion.
3. Final reporting must keep these evidence states separate: fixture foundation exists; concrete live transport/live integration/runtime wiring/operated readiness are all absent.

Stop conditions remain: block on source-approval/credential/network scope, compatibility change, destructive migration, conflict, unresolved HIGH/CRITICAL review, broker/account/live/risk scope, or an unverifiable required gate.
