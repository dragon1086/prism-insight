# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1B Task 13 — strict `TradePlanProposal` schema and fail-closed proposal parsing service
- Branch: `prism-insight/t_eb652e57-prism-phase-1b-task-13-strict-tradeplanp`
- Base: current `origin/main` at `cb6a51da85a71e3604a36dc71175b3fd9b55799f`
- Implementation state: schema, parser, tests, explicit CI discovery, independent read-only review, and definitive local gates are complete; delivery remains
- Runtime state: dormant foundation only; no application caller, model/provider transport, prompt migration, persistence, validator, policy, risk, sizing, messaging, account, order, or broker path is wired

## Implemented scope

- Added a frozen Pydantic `TradePlanProposal` contract that reuses Task 11 `StrategyId`, `StrategyVersion`, `Market`, and `SecurityId` and carries the full bindable Task 12 feature-snapshot identity/quality envelope.
- Added strict fixed-field regime probabilities, bounded score/probability/sampling values, an allowlisted score breakdown and predicate operators, explicit entry price basis/session/validity, stop/target candidates, a non-increasing risk-multiplier candidate, evidence/counter-evidence, falsifiers, missing/stale declarations, uncertainty, and model/prompt/sampling versions.
- Every JSON-schema object requires all declared fields and forbids extras. Execution approval, final quantity, `OrderIntent`, policy override, stop widening, exposure increase, and averaging-down authorization are absent and rejected as unknown fields; pyramiding requires `Literal[True]` profitable-position precondition.
- Added `ProposalService.parse()` that retains the exact raw response on success and failure, rejects malformed/fenced/unknown structured output, binds every strategy/security/snapshot/version/as-of/quality field to the supplied immutable `FeatureSnapshot`, rejects unsupported predicate features, and checks every evidence reference against a fail-closed supplied allowlist.
- Added an explicit `tests/llm` step to every Python 3.10/3.11/3.12 matrix job without weakening any existing workflow step.

## Contract decisions and deferred seams

- Regime probabilities use fixed labels and must sum exactly to `Decimal("1")`; the plan explicitly permits normalization or rejection, and this foundation chooses deterministic rejection rather than silently changing model output.
- Any declared missing/stale/conflicting data, or any feature quality other than `FRESH + ACCEPT`, permits only `NO_ENTRY` or `REPORT_ONLY`; the parser binds those self-declared values to the actual feature snapshot.
- `ENTRY_CANDIDATE` requires entry, stop, and target candidates. All entry/stop/target price candidates use one explicit `PriceBasis`; actual reference-price and stop/target sanity remain Task 15 deterministic-validator responsibilities.
- Task 12 `FeatureSnapshot` does not expose its computation `PriceBasis`, so binding the proposal basis back to underlying price-series provenance cannot be added without changing the prior public contract. This remains an explicit Task 15/runtime seam, not operated readiness.
- Evidence IDs are checked against the caller-supplied bounded set. Evidence-to-snapshot existence/freshness validation remains a Task 15 deterministic-validator and later composition-root responsibility; no global evidence store or runtime caller exists in this slice.
- Live LLM structured-output transport, timeout/rate-limit behavior, and secret-redaction smoke evidence remain absent. The result is fixture-tested contract foundation only, not a verified provider integration.

## TDD and verification checkpoint

- Observed RED→GREEN cycles included the absent schema/service modules, fixed-field structured regime probabilities, mandatory entry/stop/target candidates, single price-basis enforcement, and score-component allowlisting.
- `python -m pytest tests/llm -q` — 35 passed.
- Pre-closeout dependent run: `python -m pytest tests/llm tests/features tests/strategies tests/data -q` — 365 passed before the final report-only test addition.
- Definitive exact local CI groups after the frozen implementation: 837 passed, 1 intentionally deselected — runtime/safety 72; storage 44; AgentNews 22; all data 290; regime policy 42; strategy 26; features 14; Task 13 LLM 35; legacy LLM 111; remaining exact groups 181 with the deselection.
- `python -m compileall -q prism_core tools/audit_broker_boundaries.py` passed.
- `python tools/audit_broker_boundaries.py` passed with 0 violations and unchanged legacy inventory 22.
- `python -m pip check` reported no broken requirements.
- Workflow YAML parsing confirmed the exact `python -m pytest tests/llm -q` step exists once in the matrix job and uses fail-closed shell defaults; local collection found both Task 13 test files and 35 cases.
- Final staged `git diff --check` passed; staged-file inspection found only the seven intended code/test/workflow/handoff paths and the corrected boundary-aware private-artifact/secret scan found no hits.

## Independent review

- Verified Claude Opus 4.8 read-only review returned exit 0, empty stderr, a complete source-grounded verdict, and no CRITICAL/HIGH findings. It found the strict schema, raw retention, provenance/quality binding, evidence default-rejection, predicate shape, and proposal-only authority mergeable as a foundation slice.
- GPT accepted exact probability rejection as an intentional plan-compliant policy and recorded the price-basis and evidence-freshness seams above for Task 15 rather than expanding this bounded schema into validator/runtime work.
- GPT rejected the reviewer's LOW protected-namespace warning: under the required Pydantic `>=2.10` environment (verified 2.13.4), a fresh `python -W error` import emits no warning; `ModelIdentity.model_config` has no conflicting namespace override requirement.
- Residual safe strictness: `WATCH` is rejected under degraded data; non-entry decisions may still carry inert candidates, but no candidate is executable and later policy must key eligibility on validated disposition.

## Side effects and safety

- Git/GitHub fetch and the read-only Claude review are the only network activity through this checkpoint.
- No model/provider request, external message, credential read/change, account/broker/order call, user database access, migration, deployment, runtime activation, or live-trading effect occurred.
- No commit, push, PR, or merge has occurred yet for Task 13.

## Remaining closeout

1. Commit the bounded diff, push/open a PR, and verify exact-head Python 3.10/3.11/3.12 CI including the explicit Task 13 step in every job; inspect branch protection/rulesets separately.
2. Confirm the PR head exactly matches the verified local commit and no later handoff-only commit invalidates hosted evidence.
3. Squash merge only when every gate is provable, then verify post-merge CI, merge SHA, expected files, and remote branch deletion.
4. Final reporting must separate foundation, development enforcement, runtime wiring, and operational behavior.

Stop conditions remain: block on compatibility change, destructive migration, provider/credential/account/broker/live/risk scope, conflict, unresolved HIGH/CRITICAL review, or an unverifiable required gate.