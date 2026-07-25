# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1B Task 16 — position policy, deterministic sizing, and consolidated exposure
- Branch: `prism-insight/t_d008e763-prism-phase-1b-task-16-position-policy-s`
- Base: current `origin/main` at `a47b4403850b4ddc115fb9112def8e92197300ce`
- Implementation state: focused foundation, explicit CI discovery, TDD remediation, independent read-only review, and definitive local gates are complete; delivery remains
- Runtime state: dormant policy/risk foundation only; no application caller, persistence, paper ledger, `OrderIntent`, model/provider transport, messaging, account, broker, or operational behavior is wired

## Implemented scope

- Added immutable actual/virtual strategy-book position and open-order exposure contracts with explicit native/base currency and injected FX conversion.
- Added monotonic long-only stop policy, loss-position addition prohibition, and pyramiding approval requiring a profitable position plus an accepted validator disposition bound to the same strategy, security, and market.
- Extended accepted proposal validation with explicit pyramiding-candidate dispositions; raw proposal values remain non-authoritative.
- Added stop-distance sizing that consumes only resolved stop/multiplier dispositions, floors quantity and lot size deterministically, reasserts an injected multiplier maximum, converts native risk/notional to base currency, and caps cash, liquidity, symbol, sector, market, currency, gross, and open-order headroom.
- Added one consolidated policy that preserves book/strategy breakdowns while aggregating symbol, sector, market, currency, gross, and open-order base-currency exposure across SWING_V1/TREND_V1 and actual/virtual books.
- Fail closed on conflicting security metadata, symbol identity, base currency, or per-currency FX rates.
- Added explicit Python 3.10/3.11/3.12 CI discovery for `tests/policy tests/portfolio` without weakening existing jobs.

## Contract decisions and deferred seams

- Every numeric policy limit, risk budget, FX rate, cash amount, liquidity cap, and consolidated headroom is caller-injected research/SHADOW configuration; there are no production defaults.
- Sizing requires one accepted validator result and unique usable `resolved_value` dispositions. The immutable raw proposal is used only to bind a validated pyramiding candidate to position identity, never for quantity or risk values.
- Consolidated amounts are base-currency values. Native position/order values remain explicit and all items in one snapshot must use one requested base currency and one rate per currency.
- A position that gaps below its stored stop remains representable; stop updates cannot widen risk, and additions remain prohibited while the position is not profitable.
- Persistence, runtime composition, proposal-to-position state, as-of/FX provenance storage, internal paper, and application wiring remain deferred to later tasks.

## TDD and verification checkpoint

- Observed vertical RED→GREEN for position policy, resolved-field sizing, consolidated exposure, validator pyramiding disposition, package exports/CI discovery, base-currency aggregation, consolidated headroom caps, configured multiplier reassertion, position identity binding, FX consistency, cross-currency sizing, and gapped-position representation.
- Current focused suite: `python -m pytest tests/policy tests/portfolio -q` — 45 passed.
- `git fetch --prune origin` confirmed the branch equals current `origin/main` before the closeout freeze.
- Definitive local gates passed against the handoff-inclusive tree: compileall; broker audit (22 inventoried legacy findings, 0 violations); runtime/safety 72; storage 44; AgentNews 22; data 290; regime 42; strategies 26; features 14; LLM 35; policy/portfolio 45; agents 8; core LLM 111; execution 14 plus 1 deselected; intent/positions 97; reports 10; pyramiding/stale 14; concurrency 8; KR fill-chaser 22; US fill-chaser 16; `pip check`; and staged diff/private-artifact checks.

## Independent review

- The first repository-exploration review timed out with empty streams; the first frozen-bundle fallback ended `error_max_turns` and was rejected as unusable.
- Verified Claude Code read-only compact review used only `Read`, returned exit 0, empty stderr, resolved model `claude-opus-4-8`, and `MERGEABLE` with no CRITICAL/HIGH defect.
- Accepted findings added configured multiplier reassertion, every headroom binding regression, stop namespace validation, position/candidate identity binding, and same-currency FX consistency. Selected malformed-input coverage was added; low-level constructor coverage remains a non-blocking residual.
- Verified targeted follow-up review of the remediated contracts/tests returned exit 0, empty stderr, the same resolved model, `MERGEABLE`, and no new CRITICAL/HIGH/MEDIUM defect. It independently accepted cross-currency sizing and gapped-position representation.
- Non-blocking residuals: `SizingResult.risk_per_unit` unit is documented only by surrounding contract, currency syntax validation is basic, co-binding limits report one deterministic label, and runtime/as-of/FX provenance wiring remains deferred.

## Side effects and safety

- Git fetch and read-only Claude reviews are the only network activity through this checkpoint.
- No live PRISM model/provider request, external message, credential read/change, account/broker/order call, `OrderIntent`, user database access, migration, deployment, runtime activation, or live-trading effect occurred.
- No commit, push, PR, or merge has occurred yet for Task 16.

## Remaining closeout

1. Commit, push, open a PR, and verify exact-head Python 3.10/3.11/3.12 CI includes the explicit policy/portfolio step in every job.
2. Squash merge only when every required gate is provable; verify post-merge CI, merge SHA, expected files, and remote branch deletion.
3. Create the bounded Task 17 successor only after merge verification, then complete the Kanban task with the returned successor ID.

Stop conditions remain: block on compatibility change, destructive migration, provider/credential/account/broker/live/risk scope, conflict, unresolved HIGH/CRITICAL review, or an unverifiable required gate.