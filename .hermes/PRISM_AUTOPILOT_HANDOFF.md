# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1B Task 12 — deterministic quant feature service
- Branch: `prism-insight/t_f965e25f-prism-phase-1b-task-12-quant-feature-ser`
- Base: current `origin/main` at `b93b5f00064d45cbbd1626b2089ed1f40f994a54`
- Implementation state: feature service, pure calculators, tests, CI enforcement, independent review remediation, and final local gates are complete; delivery remains
- Runtime state: dormant foundation only; no application caller imports `prism_core.features`, and no proposal, policy, portfolio, paper, provider, messaging, database, or broker behavior changed

## Implemented scope

- Added immutable PIT inputs and a `QuantFeatureService` that returns the existing Task 11 `FeatureSnapshot` contract rather than redefining it.
- Added pure deterministic technical, fundamental, liquidity, and regime calculators with Decimal-only output and explicit raw/adjusted input basis.
- Preserved data snapshot, strategy ID/version, market/security, timezone-aware as-of, feature version, data-quality status, and quality disposition in feature identity/output.
- Added UUIDv5 feature identity and canonical ASCII JSON normalization with sorted names/keys and 12-decimal quantization.
- Kept `swing_v1.*` and `trend_v1.*` ownership separate and reject strategy-contract drift when required features are not computed.
- Re-evaluated the full Task 10 field-level quality gate and reject mismatched, missing-core, stale, partial, conflicting, or unavailable core input before producing a snapshot. `REPORT_ONLY` remains visibly non-proposal-eligible.
- Added explicit feature-suite execution to every Python 3.10/3.11/3.12 CI matrix job.

## Contract decisions

- Feature computation accepts only immutable tuples of typed PIT values and rejects any `available_at > as_of` input.
- Price and benchmark series must be ordered and equal-length; session pairing remains positional because the bounded `BenchmarkPoint` input has no separate observed-session field.
- All feature arithmetic and quantization run inside `Context(prec=50, rounding=ROUND_HALF_EVEN)`, independent of ambient process Decimal settings.
- `PriceBasis` is explicit input provenance and participates in feature UUID identity. It is not added to the Task 11 `FeatureSnapshot` in this slice because Task 12 must reuse that existing contract.
- Provider-to-input provenance linkage and runtime application composition remain deferred; this foundation does not claim operated readiness.

## TDD and verification

- Initial RED for the absent feature package and initial 10-test GREEN were observed before this recovered continuation.
- Review-remediation RED was observed for ambient Decimal divergence plus unordered/misaligned benchmark acceptance: three expected failures, then GREEN.
- Contract-drift RED was observed (`DID NOT RAISE`) before adding required-feature coverage rejection.
- Focused/current dependent GREEN: `python -m pytest tests/features tests/strategies tests/data -q` — 330 passed.
- Final exact local CI groups: 802 passed, 1 intentionally deselected — feature 14; strategy 26; runtime/safety 72; storage 44; AgentNews 22; all data 290; regime policy 42; LLM 111; remaining exact CI groups 181 with the deselection.
- `python -m compileall -q prism_core tools/audit_broker_boundaries.py` passed; broker audit passed with 0 violations and unchanged legacy inventory 22; `python -m pip check` found no broken requirements.
- `git diff --check` passed; private-artifact scan found none; static search found no broker, messaging, network, or runtime caller in the feature package.

## Independent review

- Initial verified read-only Claude review returned exit 0 with empty stderr. It found one HIGH ambient-Decimal-context determinism defect, plus MEDIUM benchmark alignment and strategy-contract-drift risks.
- GPT accepted all three and added focused RED→GREEN regressions, explicit Decimal context/rounding, ordered/equal-length benchmark validation, and required-feature coverage rejection.
- GPT deferred changing Task 11 `FeatureSnapshot` to carry `PriceBasis`: basis remains explicit on the computation input and UUID identity, while this bounded task requires reuse rather than public-contract expansion.
- Verified follow-up Claude review returned exit 0 with empty stderr, confirmed the HIGH fully resolved and no new CRITICAL/HIGH.
- Residual LOWs: signed-zero canonicalization and a TREND-specific ambient-context test. Session-date provenance and provider linkage remain later adapter/runtime responsibilities.

## Side effects and safety

- Git/GitHub and the read-only Claude review are the only network activity through this checkpoint.
- No provider request, external message, credential read/change, account/broker/order call, user database access, migration, deployment, or live-trading effect occurred.
- The bounded local delivery commit is the next closeout action. Push, PR, hosted CI, and merge remain pending; authoritative later delivery state belongs in Git, the PR, and the Kanban completion record.

## Remaining closeout

1. Commit the already frozen bounded diff.
2. Push/open a PR and verify the exact head on Python 3.10/3.11/3.12, including the explicit feature step in every job; verify branch protection separately.
3. Squash merge only when every gate is provable, then verify post-merge CI, merge SHA, expected files, and remote branch deletion.
4. Final reporting must separate foundation, development enforcement, runtime wiring, and operational behavior.

Stop conditions remain: block on compatibility change, destructive migration, provider/credential/account/broker/live/risk scope, conflict, unresolved HIGH/CRITICAL review, or an unverifiable required gate.