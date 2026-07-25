# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1B Task 14 — strategy-specific `TradePlanProposal` prompt contracts
- Branch: `prism-insight/t_3bb97373-prism-phase-1b-task-14-strategy-specific`
- Base: current `origin/main` at `d86a252321029ff845d4b7e27efce42cd42aab37`
- Implementation state: prompt contracts, bounded KR/US seams, tests, explicit CI enforcement, independent read-only review, and preliminary exact local gates are complete; definitive frozen-tree gates and delivery remain
- Runtime state: dormant prompt/factory foundation only; no application caller, immutable input-envelope assembly, model/provider transport, persistence, validator, policy, risk, sizing, messaging, account, order, broker path, or operational behavior is wired

## Implemented scope

- Added separate `SWING_V1` and `TREND_V1` prompt identities, mandates, outcome-evaluation horizons, and schema-fingerprinted prompt versions while reusing the Task 13 `TradePlanProposal.model_json_schema()` contract rather than redefining it.
- Added a strict PIT input boundary: only supplied snapshot/features/evidence within the declared as-of boundary may be used; user/news/report/evidence text is untrusted data and embedded instructions are ignored.
- Required explicit uncertainty, known unknowns, falsifiers, bull/bear evidence references, missing/stale/conflict declarations, and supplied feature/evidence references for every numeric claim.
- Explicitly denied final quantity, portfolio slots, execution/order approval, `OrderIntent`, broker/account actions, policy/risk overrides, stop widening, exposure increases, averaging down, and assumptions of execution. The embedded strict schema also rejects undeclared fields.
- Added tool-free, dormant KR and US agent factories with `server_names=[]`. The existing giant KR/US scenario factories and their legacy caller behavior remain unchanged behind the existing boundary.
- Put the canonical contract in `prism_core/llm/trade_plan_prompts.py` and retained the requested `cores/agents/trade_plan_prompts.py` compatibility import. This avoids the repository's root-`cores` versus `prism-us/cores` package-shadowing trap while keeping one implementation.
- Added explicit Python 3.10/3.11/3.12 CI discovery for `tests/agents` without weakening existing jobs.

## Contract decisions and deferred seams

- Prompt versions use authored strategy revisions plus the first 12 hexadecimal characters of SHA-256 over the exact canonical JSON Schema text embedded in the prompt. A schema change therefore changes the prompt identity automatically; authored prose changes still require an intentional prompt-revision bump.
- The prompt contains no supplied market/evidence payload and performs no concatenation itself. A later application/runtime task must assemble and delimit the immutable input envelope; actual injection resistance at that composition boundary remains unverified.
- The agent factories configure no MCP tools, but they are not called by any current entrypoint. Legacy runtime behavior is intentionally unchanged until a later bounded wrapper/SHADOW task.
- Task 15 remains responsible for deterministic proposal validation and field dispositions, including prompt-version binding, evidence freshness/existence, and price/stop/target sanity. Task 16 remains responsible for deterministic sizing and consolidated exposure.
- Live LLM structured-output transport, timeout/rate-limit behavior, provider/model identity smoke, secret redaction, persistence, and operated readiness remain absent. Fixture/contract tests do not prove a live model integration.

## TDD and verification checkpoint

- Observed RED→GREEN cycles covered: missing prompt module and strategy separation; PIT/untrusted-data confinement; strict schema/authority rules; language validation; tool-free KR/US factories; explicit CI enforcement; US package-shadow-safe imports; and schema-bound prompt-version identity.
- `python -m pytest tests/agents tests/llm tests/strategies -q` — 69 passed after review remediation.
- Preliminary exact local CI groups — 845 passed, 1 intentionally deselected: runtime/safety 72; storage 44; AgentNews 22; all data 290; regime policy 42; strategy 26; features 14; Task 13 LLM 35; Task 14 agents 8; legacy LLM 111; remaining exact groups 181 with the deselection.
- `python -m compileall -q prism_core tools/audit_broker_boundaries.py cores/agents/trade_plan_prompts.py cores/agents/trading_agents.py prism-us/cores/agents/trading_agents.py` passed.
- `python tools/audit_broker_boundaries.py` passed with 0 violations and unchanged legacy inventory 22.
- `python -m pip check` reported no broken requirements.
- Workflow YAML parsed successfully with one matrix job and an explicit `python -m pytest tests/agents -q` step.

## Independent review

- Verified Claude Opus 4.8 read-only review returned exit 0, empty stderr, resolved `modelUsage` `claude-opus-4-8`, a complete MERGEABLE verdict, and no CRITICAL/HIGH/MEDIUM findings.
- GPT accepted LOW-1 (schema could change without changing a hard-coded prompt version), added a focused failing regression test, then bound both strategy prompt versions to the exact embedded schema fingerprint and returned the focused suite to green.
- A verified targeted Claude Opus 4.8 follow-up returned exit 0, empty stderr, resolved model identity, MERGEABLE, LOW-1 RESOLVED, and no new CRITICAL/HIGH/MEDIUM finding.
- Remaining non-blocking observations: future unmapped `StrategyId` values would currently raise `KeyError`; prompt prose revisions rely on the explicit authored `.v1` bump rather than the schema fingerprint; and real evidence-envelope injection resistance is deferred to runtime composition. None grants authority or changes current runtime behavior.

## Side effects and safety

- Git/GitHub fetch and the two read-only Claude reviews are the only network activity through this checkpoint.
- No live LLM/model-provider request by PRISM, external message, credential read/change, account/broker/order call, user database access, migration, deployment, runtime activation, or live-trading effect occurred.
- No commit, push, PR, or merge has occurred yet for Task 14.

## Remaining closeout

1. Freeze and inspect the intended file set, rerun definitive exact local CI/compile/broker/pip/diff/private-artifact gates, then commit the bounded diff.
2. Push/open a PR and verify exact-head Python 3.10/3.11/3.12 CI includes the explicit Task 14 step in every job.
3. Confirm the PR head exactly matches the verified commit, squash merge only when every gate is provable, then verify post-merge CI, merge SHA, expected files, and remote branch deletion.
4. Final reporting must separate contract foundation, development CI enforcement, runtime wiring, live model transport, and operational behavior.

Stop conditions remain: block on compatibility change, destructive migration, provider/credential/account/broker/live/risk scope, conflict, unresolved HIGH/CRITICAL review, or an unverifiable required gate.