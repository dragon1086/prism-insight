# PRISM Phase 1 Product Vertical-Slice Handoff

## 2026-07-29 KR legacy-selector adapter verified checkpoint

- Branch/worktree: `wt/t_e04c57b6` / `.worktrees/t_e04c57b6`, reconciled at exact `origin/main` `0b690f6` before closeout (`HEAD...origin/main = 0/0`). This is authoritative plan Task 5 only. StockEasy remains the mandatory later Task 7 supplement and final KR daily-product/UAT is not claimed here.
- Added the thin read-only `prism_app/kr_candidate_source.py` adapter. One exact immutable `KRMarketContext` is translated into the legacy selector context with explicit context identity, session/regime fields, authoritative `GroupLeadership.group_id`/`concentration_pct`, and a separately injected typed sector map. `UNKNOWN` becomes literal `unknown`, skips the legacy final-score parity path, and can never fall through to legacy SIDEWAYS slots or score weights.
- The adapter exports every discovered source assertion without a local candidate cap, derives stable KR security identity from normalized symbol rather than display name, preserves trigger IDs, original trigger-local score keys/values, RS/extension observations, discovery source/snapshot/evidence, and PIT clocks, then uses the existing uncapped reconciliation contract. Screening failures become per-identity `REPORT_ONLY`; malformed source rows become sanitized `INVALID_CANDIDATE` exclusions without aborting valid siblings. Legacy final selection remains capped only for parity comparison, and both discrepancy directions plus the UNKNOWN skip are explicit.
- `trigger_batch.discover_read_only_candidates` calls only the characterized market snapshot and candidate trigger functions; it does not enter the legacy tracking/reporting/messaging path. KRX remains first candidate-discovery source with Naver as explicit emergency fallback. KRX daily discovery clocks now conservatively anchor observation to `min(15:30 KST session close, receipt)` while availability/ingestion remain post-fetch receipt; Naver exports provider observation, receipt, stable snapshot identity, and evidence. The wrapper characterization gate covers both discovery and known-regime `select_final_tickers` for direct forbidden account/broker/order/message/tracking calls.
- Current-tree actual read-only UAT ran from `2026-07-29T13:30:40.808307+09:00` through `2026-07-29T13:30:47.900985+09:00` with `fixture_only=false` and `cached_only=false`. The production context seam observed KIS volume-rank HTTP `200` as PRIMARY plus AgentNews KR as SUPPLEMENTAL; KIS was `PARTIAL`, AgentNews `FRESH`, aggregate context `UNAVAILABLE`, regime `UNKNOWN`, disposition `ANALYSIS_INCOMPLETE`, and `action_eligible=false`. KRX discovery was unavailable in this environment, so the characterized Naver fallback returned 10 trigger assertions across three afternoon triggers, reconciled to 6 unique/included identities with 0 excluded, 0 invalid, and 0 truncated. All 10 assertions remained `REPORT_ONLY`; parity recorded `LEGACY_SELECTION_SKIPPED_UNKNOWN_REGIME`. Private sanitized readback `.hermes/uat/kr-candidate-adapter-live-uat.json` is mode `0600` and git-ignored; it retains no raw provider rows or credential values.
- This live run proves the current intraday fail-closed KIS+AgentNews context → legacy discovery → candidate adapter → reconciliation seam. It does **not** prove a known-regime completed-session parity run, KRX live discovery, downstream SWING/TREND analysis, report/dashboard integration, explicit user UAT, StockEasy supplementation, scheduling, or operated readiness. Those claims remain pending their named later milestones; fixture evidence is not substituted for them.
- Strict TDD/review remediation retained decisive RED→GREEN evidence for KRX source-clock causality (`1 failed` then pass) and malformed-row sibling isolation (`1 failed` then pass). The prior focused adapter/wrapper suite passed `20`; current focused suite passes `21`. Independent Claude read-only review first returned `PASS_WITH_RECOMMENDATIONS` with no CRITICAL/HIGH, then GPT accepted and fixed its KRX-clock and malformed-row findings and expanded known-regime call-graph characterization. The regenerated current-tree follow-up returned `PASS`, confirmed all material findings resolved, found no new CRITICAL/HIGH/MEDIUM defect, and did not block delivery. Remaining LOW disclosures are Naver naive-timestamp error typing and the intentionally shallow/non-transitive AST characterization.
- Definitive current-tree local gates passed all 31 focused and hosted-CI-equivalent pytest command groups (`1,442` passing test invocations plus one intentional deselection). `compileall` passed for the changed/runtime trees; the broker-boundary audit reported `violations: 0`; `pip check`, CI YAML parsing, and `git diff --check` passed. Added-line scans found zero hardcoded-secret, private-key, shell-injection, eval/exec, pickle, or breakpoint patterns, and Git status contains no private/UAT/database/credential path. Commit/push/PR, exact-head hosted CI, squash merge, and post-merge verification remain closeout actions.
- External effects through UAT/review: bounded official KIS market-data-only reads, public AgentNews KR reads, public Naver candidate-discovery reads, GitHub fetch/prune, and read-only Claude review. No account, balance, holdings, broker, order, cancel/replace, external message, schedule, credential mutation/output, destructive migration, or raw private provider payload retention occurred.

## 2026-07-29 KR shared market-context verified delivery

- Branch/worktree: `feat/kr-shared-market-context` / `.worktrees/t_b7209359`.
- Added immutable `KRMarketContext` contracts under `prism_core/market/`: explicit KR session/as-of/ingestion clocks, required PRIMARY/SUPPLEMENTAL source roles, deterministic metric and group-leadership slots, five-state deterministic regime output plus `UNKNOWN`, fully bound evidence IDs, conflict/missing-field ledgers, canonical serialization/content identity, and fail-closed `ANALYSIS_INCOMPLETE`/`action_eligible=False` semantics. Required regime inputs that are absent remain `UNKNOWN`; there is no silent fallback to `SIDEWAYS`, and every known regime is re-derived from the governed `kr_regime_v1` features/version/reason.
- Added the smallest KIS-primary + public AgentNews composition seam. KIS is the only deterministic primary input and the port exposes only `fetch_volume_rank`; AgentNews remains `UNTRUSTED`, `RESEARCH_PRIORITY_CONTEXT`, and non-executable. Returned KIS volume-ranking rows produce explicitly scoped `volume_rank_*` breadth counts, not market-wide breadth. Missing index state, investor flows, macro indicators, group leadership, and regime features remain visible and force `UNAVAILABLE` rather than fabricated values.
- PIT/session remediation distinguishes pre-open mutable snapshots from intraday snapshots using the official KRX exchange calendar plus KST observation time, preserves the provider's latest completed session date, returns `UNKNOWN` when calendar capability is unavailable, rejects future provider/attempt/fallback/freshness clocks, and represents KIS quality conflicts with an explicit conflict reason. The final adjudication additionally cross-checks every provider `COMPLETE_CURRENT_SESSION` label against `latest_completed_session(ExchangeMarket.KRX, observed_at)`, so an intraday or mismatched completion claim now fails closed as `UNKNOWN`. Parent quality is re-derived from explicitly PRIMARY source clocks; supplemental AgentNews remains visible but cannot redefine deterministic KIS market-data quality.
- Genuine bounded read-only composition ran from `2026-07-29T11:09:08.520627+09:00` through `2026-07-29T11:09:10.903675+09:00` using the production `KRMarketContextComposer`, actual KIS market-data-only volume rank, and public AgentNews KR (`fixture_only=false`, `cached_only=false`). The sanitized readback recorded KIS `PARTIAL`, AgentNews `FRESH`, session `INTRADAY` anchored to `2026-07-28`, aggregate `UNAVAILABLE`, regime `UNKNOWN`, disposition `ANALYSIS_INCOMPLETE`, `action_eligible=false`, Pydantic readback `PASS`, and canonical equality. Private evidence is `.hermes/uat/kr-market-context-live-uat.json`, mode `0600`, git-ignored, and explicitly records no retained raw market rows/news body, account/balance/holdings call, broker order/cancel/replace call, or credential mutation. This live run preceded the final calendar-label hardening; the changed branch is exercised by deterministic exchange-calendar regressions, and the live mutable `INTRADAY` path is unchanged.
- Strict TDD retained RED→GREEN proof for all five predecessor review blockers: asserted FRESH quality over a STALE primary, arbitrary known/SIDEWAYS regime construction, duplicate canonical collection keys in both orders, weekend/holiday false INTRADAY labels, and AgentNews attempt/fallback/freshness clocks after `as_of`. Post-review RED→GREEN hardening also requires explicit source roles, binds every nested evidence ID, and canonicalizes leadership evidence IDs.
- Final independent Claude Opus read-only review (`tasks/reviews/t_9ce70cab-claude-safety-review.md`, private/git-ignored) returned `PASS_WITH_RECOMMENDATIONS`, with zero CRITICAL/HIGH, three MEDIUM, and three LOW findings. Hermes explicitly adjudicated all six: M1 accepted and fixed by authoritative official-close/session cross-check; M2 accepted with direct non-KIS and future-KIS-clock rejection tests; M3 accepted with calendar-unavailable, completed-date mismatch, and unrecognized-state tests; L1 accepted with a valid last-known-good AgentNews fallback test proving stale supplemental/non-executable behavior; L2 accepted with empty, non-finite, and malformed breadth tests; L3 accepted by documenting and testing deterministic `STALE` over `PARTIAL` precedence. No finding was rejected or deferred. The meaningful RED for M1 was `1 failed, 23 passed`; after the one in-scope implementation iteration the same focused command was GREEN at `24 passed`. A current-tree Claude follow-up exited `0` with empty stderr and `PASS`, finding no CRITICAL/HIGH/MEDIUM defect; Hermes accepted its sole new LOW coverage note and parametrized calendar-unavailable coverage across both mutable and provider-COMPLETE branches.
- Final local verification after the follow-up coverage edit: exact two-file focused command `25 passed`; exact `tests/market` gate `38 passed`; neighboring candidate/KIS/AgentNews/calendar suite `102 passed`; all 30 hosted-CI-equivalent pytest command groups passed (`1,415` passing test invocations total, one intentional deselection); `python -m compileall -q prism_core prism_app stock_analysis_orchestrator.py prism-us/us_stock_analysis_orchestrator.py tools/audit_broker_boundaries.py` passed; broker-boundary audit reported `violations: 0`; `python -m pip check`, CI YAML parse, and `git diff --check` passed. Added-line scans found zero hardcoded-secret, private-key, shell-injection, eval/exec, or pickle patterns. Tracked private-path scan found no `.hermes/uat`, review artifact, SQLite/database, key, or credential file; the only pre-delivery changes are the two market modules, two market tests, and this handoff.
- Delivery PR #47 (`https://github.com/mienne/prism-insight/pull/47`) contains only those five intended paths after reconciling the already-squash-merged #46 baseline. Hosted run `30416998080` passed the complete fail-closed workflow on exact code head `e008784129f6e62b6f7f39b334896289992803d3` for Python 3.10, 3.11, and 3.12; every required step, including the dedicated market gate and broker audit, completed successfully in all three matrix jobs. The repository has no branch protection/ruleset, so these are active verified PR checks rather than administratively required checks. The final handoff-only head must still be exact-head green before squash merge; there is no code, evidence, or review blocker.
- CI now has an explicit fail-closed `python -m pytest tests/market -q` step. This milestone is contract foundation + actual KIS/AgentNews composition only. Candidate/scenario runtime sharing is intentionally not claimed until the Task 5 legacy selector adapter and scenario wiring consume one composed instance; report/dashboard integration and user UAT also remain pending.
- External effects through this checkpoint: GitHub fetch/prune, task-scoped feature-branch commits/pushes, PR #47, hosted CI, bounded official KIS market-data-only reads, public AgentNews KR reads, and read-only Claude review. No direct/force push to main, account, balance, holdings, broker, order, cancel/replace, Telegram message, schedule, credential change, raw provider-value retention, or private artifact staging occurred. Squash merge and post-merge origin/main verification remain pending the final exact-head CI gate.

## 2026-07-29 KR unbounded candidate-contract and real-data checkpoint

- Branch/worktree: `feat/kr-unbounded-candidate-contracts` / `.worktrees/t_2811adfe`.
- Added strict immutable `CandidateSnapshot` contracts and deterministic reconciliation under `prism_core/candidates/`. Reconciliation has no local post-discovery cap, merges by `(market, security_id)`, preserves full provider/channel/source/trigger/raw-score/evidence assertions, never compares trigger-local raw scores globally, orders core before supplemental candidates, deduplicates exact complete source assertions, fails closed on contradictory assertions, isolates malformed records, and emits explicit invalid/data-unavailable exclusions. `truncated_candidate_count` is structurally fixed to `0`, and the count validator accounts for every input record.
- Added the smallest KIS-primary read-only discovery seam needed to clear this milestone's real-data gate. `KISHTTPTransport.fetch_volume_rank()` can call only the official quotation endpoint `/uapi/domestic-stock/v1/quotations/volume-rank`; it exposes no account/order method. `KISVolumeCandidateSource` maps every provider-returned row to the existing contract without a local cap and sends malformed rows through reconciliation as explicit invalid exclusions.
- Session semantics are conservative: the mutable KIS rank is `FRESH` only after a same-date completed KRX session. Pre-open, intraday, weekend, and holiday snapshots are `PARTIAL`; their candidates remain visible as `REPORT_ONLY` with `KIS_VOLUME_RANK_SESSION_UNVERIFIED` and cannot be represented as an eligible completed-session signal.
- Genuine KIS market-data-only UAT at `2026-07-29T08:25:43+09:00` returned HTTP `200` and 30 provider rows. All 30 stable identities traversed reconciliation (`input_unique=30`, `reconciled_unique=30`, `included=30`, `invalid=0`, `truncated=0`) and were correctly labeled `REPORT_ONLY` because the run was pre-open. Sanitized private evidence is `.hermes/uat/2026-07-29-kis-candidate-discovery.txt` and remains ignored. This proves the actual KIS transport → candidate contract → reconciliation plumbing only; it does not prove an uncapped provider universe, a completed-session ranking signal, Task 5 selector integration, or operated readiness. The independent synthetic contract test proves 500 unique identities reconcile without local truncation.
- TDD evidence includes retained RED→GREEN cycles for stable mapping, discovery-only transport construction, pre-open PARTIAL/session anchoring, non-FRESH REPORT_ONLY propagation, malformed-row isolation, and the structural zero-truncation ledger. Earlier retry evidence for provider-complete source identity and permutation-stable conflict serialization remains valid.
- Independent Claude read-only review initially blocked on pre-open FRESH labeling and identified malformed-row/evidence-framing concerns. Hermes remediated all three with focused failing regressions. A follow-up read-only review marked H1/M1/M2 resolved, returned `PASS`, and found no unresolved HIGH/CRITICAL item. LOW deferred Task 5 concerns are the future multi-channel quality-policy merge and shared KR security-ID helper.
- Current deterministic proof: focused candidate/KIS transport suite `54 passed`; selected broad data/LLM/storage/policy/portfolio/candidate suite `470 passed`; actual KIS smoke `1 passed`; `python -m compileall -q prism_core`, `python tools/audit_broker_boundaries.py`, and `git diff --check` pass with broker-boundary `violations: 0`. The Python matrix contains an explicit fail-closed `tests/candidates` step. Hosted exact-head CI remains pending until delivery.
- External effects: GitHub fetch/prune, bounded official KIS market-data-only reads, and two read-only Claude review rounds. No account, balance, holdings, broker, order, cancel/replace, message, schedule, credential change, or private artifact staging occurred. Commit/push/PR/merge are pending closeout. Task 3 remains pre-created and dependency-blocked; do not duplicate it.

## 2026-07-28 acceptance correction — product scenario completion first

The previous network-backed KR/US run proved transport, OAuth, persistence, and rendering paths only. It did **not** prove a completed product scenario. All four KR/US × SWING_V1/TREND_V1 model outputs declared critical missing/conflicting inputs while choosing `WATCH`; the strict `TradePlanProposal` root validator correctly rejected them. The persisted rows therefore had `parse_status=REJECTED`, `validation_status=REJECTED`, `proposal_id=NULL`, `proposed_decision=NULL`, and `normalized_proposal_json=NULL`. `dashboard_export.py` then incorrectly projected every rejected record as `NO_ENTRY` and an empty scenario.

The replacement acceptance contract is now authoritative for this workstream:

1. Keep `DATA_UNAVAILABLE`, `ANALYSIS_INCOMPLETE`, `INVALID_PROPOSAL`, `POLICY_REJECTED`, `REPORT_ONLY`, `WATCH`, `NO_ENTRY`, and `ENTRY_CANDIDATE` distinct. Never convert parse/schema failure or missing scenario inputs into an investment `NO_ENTRY` decision.
2. A scenario is complete only when strict parsing succeeds, normalized proposal content is preserved, identity/evidence binding succeeds, deterministic field dispositions are persisted, and the user surface renders non-empty evidence-backed regime plus bull/base/bear paths, current action, machine-checkable triggers, falsifiers, uncertainty, and next-review event.
3. `NO_ENTRY` counts as complete only when required data is present and explicit observed values fail explicit strategy thresholds. `WATCH` counts only with machine-evaluable triggers, validity, and failure transition. `ENTRY_CANDIDATE` additionally requires consistent price basis plus entry, structure/ATR stop, target, invalidation, and reward/risk candidates. Missing core data is `ANALYSIS_INCOMPLETE`/`REPORT_ONLY`, not `NO_ENTRY`.
4. Complete the bounded KR/US × SWING_V1/TREND_V1 product scenario kernel with actual approved provider/OAuth evidence before attaching it to legacy candidate-selection/report/dashboard callers. Fixtures remain CI evidence only. Fixed-symbol provider smoke is not existing-product completion.
5. After scenario-kernel completion, connect the smallest thin adapters to existing candidate selection and current report/dashboard surfaces; do not build a parallel product.
6. Final proof requires genuine KR and US network-backed cycles, SQLite readback, lossless report/dashboard rendering, explicit error-state UAT, read-only Claude red-team, broad gates, exact-head PR checks, and explicit user acceptance. Until then the verdict is `product scenario incomplete`.

Hard boundaries remain unchanged: SHADOW/read-only only; no account, balance, holdings, broker, order, cancel/replace, message, schedule, launchd/cron, live, risk-limit, credential, destructive migration, or compatibility-expansion effects. Existing uncommitted work must be preserved and reconciled by exactly one sequential writer.

## Fresh-session reconstruction

Read this file first, then:

1. `.hermes/plans/2026-07-26_173423-phase1-product-vertical-integration.md`
2. `git status --short --branch` and the complete tracked + untracked diff
3. `prism_app/daily_pipeline.py`
4. `prism_app/strategy_evaluator.py`
5. `prism_core/features/market_inputs.py`
6. `prism_core/strategies/quant_score.py`
7. `prism_core/data/providers/kis.py` and `kis_http.py`
8. the new tests listed below

Treat tests/runtime/source as authoritative over conversation summaries.

## Workspace

- Repository: `/Users/enne/Documents/Codex/prism-insight`
- Active worktree: `/Users/enne/Documents/Codex/prism-insight/.worktrees/product-vertical-slice`
- Branch: `feat/phase1-product-vertical-slice`
- Base: `origin/main` at worktree creation time; refresh/fetch before closeout
- Working tree: intentionally uncommitted; do not overwrite or reset it
- No active server or durable background process

## User completion contract

Phase 1 is complete only when one real product works end-to-end and is user-testable:

```text
live read-only KIS/FMP data
→ PIT/quality
→ SWING_V1 and TREND_V1 features
→ deterministic quant scores
→ existing PRISM ChatGPT OAuth LLM path
→ strict TradePlanProposal parse
→ deterministic ProposalValidator
→ SQLite persistence/readback
→ SHADOW report/dashboard
→ one CLI/UAT surface
```

Fixture/unit/E2E tests alone are not product completion. Keep these states separate: module implementation, fixture proof, live provider proof, runtime wiring, user UAT, operated readiness.

## Hard safety boundaries

- No account, holdings, balance, broker-order, cancel/replace, or live-trading effects.
- LLM is tool-free proposal generation only; validator/policy remains authoritative.
- Stale/partial/conflict/unavailable core input must fail closed before the LLM and
  remain `DATA_UNAVAILABLE`, `ANALYSIS_INCOMPLETE`, or `REPORT_ONLY`; it must not be
  projected as an intentional investment `NO_ENTRY`.
- Never print/store credential values or provider error details.
- Telegram remains dry-run or `[TEST]` allowlist only; no schedule activation before UAT approval.
- Claude is read-only review only; Hermes/GPT owns edits/tests/final decisions.

## Authentication correction — important

Do **not** require a separate OpenAI API key for the new Phase 1 path. Existing PRISM supports ChatGPT OAuth:

```text
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth
→ cores.chatgpt_proxy.inject_env()
→ cores.chatgpt_proxy.start_proxy()
→ local OpenAI-compatible endpoint on 127.0.0.1:18741
→ placeholder process key injected internally
```

Relevant existing wiring is in `stock_analysis_orchestrator.py` and `prism-us/us_stock_analysis_orchestrator.py`. OAuth token storage is `~/.config/prism-insight/chatgpt_auth.json`. The isolated worktree shell lacked bootstrap env/token visibility; that is a composition/lifecycle issue, not proof an API key is required. Reuse the existing OAuth lifecycle in the new CLI/composition root. Never ask the user to paste a secret.

## Implemented so far

### Structured LLM evaluator

New `prism_app/strategy_evaluator.py`:

- strategy-specific prompt contract
- one tool-free `LLMBackend` call
- strict `ProposalService` parsing
- deterministic `ProposalValidator`
- fail-closed `NO_ENTRY`/rejection behavior
- sanitized backend error type only
- proposal/decision/feedback persistence
- typed feature + quant + evidence input

### Typed daily-pipeline input

`prism_app/daily_pipeline.py` now carries `StrategyEvaluationInput` containing exact `FeatureSnapshot`, `QuantScoreBreakdown`, evidence identities/payload, PIT timing, and hard vetoes. It validates strategy/data-snapshot identity. Review remediation also added explicit evaluation market binding.

### Deterministic quant score

New `prism_core/strategies/quant_score.py` computes versioned deterministic Decimal scores and components from exact feature snapshots; non-fresh/non-ACCEPT inputs reject.

### Market snapshot → feature input

New `prism_core/features/market_inputs.py`:

- exact stock/benchmark session alignment
- duplicate-session rejection
- raw/adjusted basis handling
- no quality upgrade over provider snapshot
- no fabricated catalyst/regime observations
- PIT availability now comes from `bar.timing`, not historical session close

### KIS bounded historical read

`KISHTTPTransport` has optional `lookback_calendar_days` in `[0, 400]`, default `0` for compatibility. Real read-only KIS smoke once returned 41 bars each for `005930` and `069500` over 60 calendar days. A later separate CLI process received a sanitized `KISMarketDataTransportError`, likely requiring auth/token lifecycle investigation; do not call this operated readiness.

Historical KIS rows must use the retrieval/envelope `observed_at` and `available_at` because KIS does not provide vintaged proof that a later-returned historical value existed at session close. Do not backdate availability.

### User CLI foundation

New:

- `prism_app/__main__.py`
- `prism_app/cli.py`
- `prism_app/live_data_uat.py`

Command:

```bash
python -m prism_app live-data --symbol 005930 --benchmark 069500 --lookback-days 60 --output <path>
```

It is explicitly a read-only live-data UAT, not the finished product. It catches provider and normalization errors, redacts details, returns `NO_ENTRY`, never calls LLM/broker, and now returns nonzero when UAT is rejected. `.hermes/uat/2026-07-26-kr-live-data.json` is a generated local artifact and should stay out of the commit unless explicitly approved.

## Verification evidence before latest review remediation

Broad selected suite:

```text
241 passed in 2.85s
```

Command covered `tests/app tests/e2e tests/features tests/strategies`, KIS provider/transport, LLM, policy, and feedback tests.

Also passed:

- `python tools/audit_broker_boundaries.py` → `violations: 0`
- `python -m compileall -q prism_app prism_core cores/llm`
- `git diff --check`
- added-line static scan: no hardcoded secret, shell injection, eval/exec, or pickle findings

**Do not cite this as current green proof.** Several files changed afterward during independent-review remediation and the broad suite has not yet been rerun.

## Independent review and adjudication

### 2026-07-26 idempotency remediation checkpoint

Finding #1 now has a sequential-retry implementation and product-pipeline proof:

- `StructuredLLMStrategyEvaluator` computes the complete invocation identity before the backend call and probes `research.sqlite` by the existing unique proposal natural key.
- A stored exact invocation is provenance-checked and reconstructed without another LLM call.
- Replay output is deterministic for accepted, malformed, parsed-but-rejected, and backend-failure results. Provider exception classes were replaced by the stable redacted `LLM_BACKEND_FAILURE` token so retries can reproduce the same analysis.
- Accepted proposals persist only sampling fields the backend really applies: explicit temperature plus required nullable `top_p`/`seed`; unsupported configured keys still reject.
- New DailyPipeline failure-injection tests cover (a) first strategy persisted then second strategy raises and (b) both proposals persisted then ops save raises. Both retries converge with two total backend calls, two proposal rows, one leadership report, and one ops job row.

Current verification after these changes:

```text
253 passed in 3.10s
compileall: PASS
broker boundary audit: violations 0
git diff --check: PASS
```

The selected suite covers `tests/app`, `tests/e2e`, `tests/features`, `tests/strategies`, `tests/feedback`, `tests/llm`, `tests/policy`, and focused KIS provider/HTTP tests.

Claude read-only follow-up initially exhausted 12 turns. A successful frozen-bundle retry returned a conditional pass: sequential same-invocation recovery is resolved and no HIGH/CRITICAL issue blocks OAuth/product wiring. Residual hardening remains explicit: concurrent duplicate runners need the existing ops lease/single-runner gate, and provider/evaluation/source identities must stay stable for a retry to count as the same full invocation. Snapshot drift is a new invocation, not a replay, and must not be silently called idempotent.

Architecture review output exists at `/tmp/prism_claude_stdout`; initial Claude process exited successfully. A later focused Claude run hit max turns and produced no usable verdict.

A separate read-only independent reviewer produced seven blocking logic findings. Full artifact:

`/Users/enne/.hermes/cache/delegation/subagent-summary-0-20260726_181640_736275.txt`

Status:

1. **Cross-database partial persistence/idempotency — sequential recovery implemented and verified.** Exact invocation replay now reads persisted research proposals before any backend call. DailyPipeline tests cover second-strategy and ops-save failure recovery. Remaining non-blocking hardening: concurrent runners require lease/single-runner enforcement; changed snapshot/evaluation/source provenance is intentionally a new invocation and must fail/report distinctly at the product layer.
2. **Proposal identity provenance — implemented and verified.** Identity hashes strategy/version, feature/score IDs and versions, prompt/model/sampling/policy/config/code/schema versions, evaluation boundary, and source-payload hash; accepted and rejected replay tests pass.
3. **Historical KIS PIT backdating — fixed and selected suites pass.** Restored envelope/retrieval timing and changed feature adapter to use `bar.timing.observed_at`.
4. **Cross-market feature provenance — fixed and selected suites pass.** `StrategyEvaluationRequest` has explicit market and rejects mismatched feature market.
5. **Empty unsupported LLM output escaped persistence — fixed and verified.** `_raw_response` is inside the fail-closed catch and persists/replays `[NO_MODEL_OUTPUT]` with the stable redacted error token.
6. **Sampling provenance claimed unsupported fields — fixed and verified.** Backend config requires explicit temperature, rejects unsupported keys and invalid values, and accepted-proposal persistence/replay passes with nullable undeclared controls.
7. **UAT normalization errors/exit status — fixed and selected suites pass.** Normalization/adaptation failures are caught and rejected UAT returns process code 2.

The requested accepted-proposal evaluator test now verifies exact model/sampling provenance and idempotent replay without a second backend call.

## 2026-07-26 product-composition checkpoint

Implemented and focused-green:

- `ChatGPTOAuthRuntime` owns the existing in-process OAuth proxy lifecycle, requires `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth`, uses the repository token location, restores prior process environment, exposes no MCP tools, and never requests a separate API key.
- `python -m prism_app oauth-smoke` performs one bounded tool-free structured backend call and emits only sanitized evidence.
- `run_kr_shadow_product` composes KIS market-data-only acquisition, PIT evidence, separate SWING/TREND features and quant scores, structured evaluator, deterministic validator, research/ops SQLite, lease/single-runner enforcement, persisted readback, and an atomically appended SHADOW Markdown section.
- `python -m prism_app shadow-readback` reads the exact ops run through SQLite read-only mode and reuses the existing Markdown report as the base shell.
- App-level invocation identity now includes snapshot/source payload, evaluation time, model/provider/sampling, prompt versions, validator/policy/config/code/schema versions, and deterministic policy limits. Changed provenance is a new invocation; exact replay remains idempotent.
- Product UAT success now rejects quality skips, missing strategy families, any stable `LLM_BACKEND_FAILURE`, and idempotent replay. Therefore `PERSISTED_READBACK_VERIFIED` means a fresh ACCEPT cycle returned model output for both strategy families; rejected proof returns sanitized exit 2.
- Non-fresh provider quality no longer enters feature computation or the LLM. It persists as a fail-closed no-strategy/NO_ENTRY analysis, and leadership quality is not upgraded over provider quality.
- A stale timed-out Kanban worker (PID 98921) was found still editing this worktree during retry run 78. It was terminated, recorded in card comment 103, and run 78 became the sole finalization owner before further edits.

Focused proof in retry run 78:

```text
21 passed in 1.08s                       # OAuth/composer/readback/lease/UAT slice
275 passed in 3.98s                      # selected broad suite before final review remediation
16 passed in 1.12s                       # product/report focused reconciliation
1 passed                                # H1 runtime-proof RED→GREEN
2 passed                                # runtime-proof + composition replay focused GREEN
1 passed                                # stale quality fail-closed RED→GREEN
353 passed in 5.05s                     # definitive selected broad + reporting suite
compileall: PASS
broker boundary audit: violations 0
git diff --check: PASS
```

Independent Claude read-only review:

- First 338 KB bundle exhausted 8 turns and was unusable.
- Compact 180 KB bundle completed successfully. It identified H1 (backend failure could look like UAT success) and H2 (app job identity omitted evaluator/policy provenance) as HIGH.
- H2 had already been remediated TDD-first by the stale prior worker before it was stopped; retry run 78 verified the source/test and retained it.
- Retry run 78 remediated H1 TDD-first and also rejected quality-skip/replay proof. A focused follow-up review found no new HIGH/CRITICAL; H2 and empty-bar timing were resolved, H1 was materially closed at the product boundary, and residual observations were non-blocking.
- GPT adjudication: `StructuredLLMStrategyEvaluator` emits the stable redacted `LLM_BACKEND_FAILURE` token on all backend exceptions and tests persist/replay it, so the product guard is grounded. `transaction()` uses `BEGIN IMMEDIATE`, resolving lease atomicity. Binary PIT evidence completeness/timing validation makes accepted evidence/regime/fundamental fields fresh by construction; provider price/leadership quality now remains explicit. Pipeline persistence of a quality skip is intentional audit evidence, while the product/UAT command correctly refuses to call it runtime proof.

## Live capability evidence and R3 blocker

- Actual KIS market-data-only retry succeeded with 100 bars for each of `005930` and `069500`; because the run occurred on a non-trading day, provider quality was `STALE`, the gate returned `REJECT`, and no LLM was called. Sanitized artifact: `.hermes/uat/2026-07-26-kr-live-data-retry.json` (local/private; do not commit by default).
- Actual OAuth smoke was attempted twice through the new command. The repository OAuth token file was absent and `PRISM_OPENAI_AUTH_MODE` was not configured in the worker environment; both attempts failed closed as `OAUTH_LLM_UNAVAILABLE` without a network/model call. Sanitized artifact: `.hermes/uat/2026-07-26-oauth-llm-smoke-retry.json` (local/private; do not commit by default).
- No actual OAuth-backed complete product cycle has run. No UAT command may be presented as ready, and user acceptance cannot start, until OAuth is configured locally and a fresh-market PIT evidence bundle is available.
- Unblock locally without sharing secrets: run `python -m cores.chatgpt_proxy.oauth_login` in the repository and set `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth` in the UAT shell. Do not paste token contents into chat or the task thread.

### 2026-07-26 OAuth-unblock and closeout evidence

- The user completed the existing local ChatGPT OAuth login. Token-file presence was verified without reading or printing it.
- `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python -m prism_app oauth-smoke ...` completed against the actual OAuth proxy with exit `0`, structured status `OK`, `tool_count: 0`, and `broker_called: false`.
- The first successful smoke exposed an OpenAI Agents SDK tracing-export `401`: the SDK used the proxy placeholder key against its separate public tracing endpoint. TDD remediation in `cores/llm/backends/openai_agents_backend.py` now disables SDK tracing before binding the OAuth proxy client. Focused test: `1 passed`; repeated actual smoke: exit `0`, identical sanitized success payload, and empty stderr.
- A narrow Claude Opus read-only security/global-state follow-up exited `0` with empty stderr and verdict `PASS`: no HIGH/CRITICAL blocker. Its LOW residual is intentional process-global tracing disablement; this CLI runtime is bounded and the change reduces metadata egress. The existing repository verification utility already uses the same global-disable posture.
- A fresh actual KIS market-data-only boundary retry returned 41 aligned sessions for both `005930` and `069500`. Weekend data remained `STALE`; the command exited `2` with `REJECT`/`NO_ENTRY`, `llm_called: false`, and `broker_called: false`. This is correct fail-closed live-boundary evidence, not product/UAT completion.
- No complete OAuth-backed product cycle was attempted with invented PIT evidence. The current weekend snapshot cannot satisfy the FRESH/ACCEPT gate, and no actual user-curated catalyst/regime/fundamental PIT bundle was available. A real complete cycle therefore remains blocked until both fresh trading-session data and genuine current PIT evidence are available.
- Sanitized/private runtime artifacts remain under `.hermes/uat/` and are intentionally excluded from delivery.

Final local verification on the post-tracing tree:

```text
289 passed in 4.27s                     # selected Phase 1 product/provider/report suite
compileall prism_core prism_app cores/llm: PASS
broker boundary audit: violations 0
git diff --check: PASS
CLI help: PASS
40 non-UAT changed/untracked files scanned: 0 secret/private-key patterns
```

An unscoped repository-root `python -m pytest -q` was also attempted and stopped during collection with 62 pre-existing environment/layout errors (missing optional legacy dependencies such as pandas/numpy/Telegram/MCP packages, absent private KIS config, and cross-tree `tests` package collisions). The task-scoped selected suite above is green; broad-root collection is not represented as passing.

### 2026-07-27 deferred structured-output validation checkpoint (`t_7274c9ab`)

The real OpenAI Agents boundary no longer runs `TradePlanProposal` Pydantic semantic/cross-field validators before the application policy seam:

- `cores.llm.ports.DeferredValidationSchema` is an SDK-independent marker for shape-only structured output.
- `OpenAIAgentsBackend` converts that marker into an `AgentOutputSchemaBase` implementation, preserves a strict object/type/required/items schema, removes value-level enum/range/format/length/pattern constraints, decodes fractional JSON numbers as `Decimal`, and returns the decoded mapping.
- `StructuredLLMStrategyEvaluator` uses the deferred schema and serializes decoded mappings safely. `ProposalService` performs strict Pydantic parsing and `ProposalValidator` turns parse/semantic failures into deterministic `REJECTED` dispositions instead of `LLM_BACKEND_FAILURE`.
- Non-finite model numbers, malformed JSON, wrong JSON types, extra fields, and prohibited execution keys still fail closed. No broker/account/order capability was added.

TDD evidence:

```text
RED  ImportError: DeferredValidationSchema missing
GREEN 1 passed                                  # backend deferred-schema contract
RED  semantic-invalid mapping classified LLM_BACKEND_FAILURE
GREEN 1 passed                                  # persisted REJECTED, backend_error_type=None
RED  2 failed                                   # numeric float + field-range deferral regressions
GREEN 2 passed                                  # Decimal-safe numeric path + shape-only range deferral
39 passed in 0.75s                              # backend + structured evaluator suites
260 passed in 1.74s                             # cores/llm, tests/llm, tests/policy, tests/app
compileall cores/llm prism_core prism_app: PASS
git diff --check: PASS
```

An unscoped root `python -m pytest -q` was attempted again and stopped during collection with 45 environment/layout errors: optional legacy packages were absent (`upstash_redis`, `Crypto`, `mcp_agent`, Telegram, matplotlib/scipy/PyPDF2), private KIS config was absent, and cross-tree `tests` package collisions prevented package imports. This is not represented as a green full suite; the task-scoped 260-test regression is green.

Independent Claude read-only review ran twice with exit `0` and empty stderr. The first review found a blocking real-SDK float→`canonical_json` mismatch plus missing full-schema and field-range-deferral coverage. GPT accepted and remediated those findings TDD-first. The follow-up confirmed the HIGH/MEDIUM findings resolved and found no new blocking defect. Its concern that `Literal[True]` could become typeless was rejected with current runtime evidence: the projected full `TradePlanProposal` schema retains `type: boolean` for `requires_profitable_position`, and the full-schema contract test passes. Residual: SDK-rejected gross shape failures cannot retain raw model bytes because the SDK raises before returning; they remain redacted `[NO_MODEL_OUTPUT]`/`LLM_BACKEND_FAILURE` and fail closed.

No live provider, product, Telegram, broker, account, or order call occurred in this checkpoint. The only networked external action was the bounded read-only Claude review. No credential was read, changed, or printed; no commit, push, or PR occurred.

### 2026-07-27 real provider/OAuth SQLite SHADOW checkpoint (`t_ee0d8188`)

One fresh bounded runtime cycle completed through the diagnostic `prism_app` composition at `2026-07-27T06:17:49Z`:

- Command scope: actual KIS read-only daily market data for `005930` and `069500`, FMP `stable/income-statement` fundamentals for `005930.KS`, live AgentNews KR Markdown, ChatGPT OAuth with requested/forwarded model `gpt-5.6-sol`, separate SWING/TREND feature and quant inputs, deterministic `ProposalValidator`, new private research/ops SQLite stores, and SHADOW Markdown.
- Sanitized command result: exit `0`, `PERSISTED_READBACK_VERIFIED`, quality `ACCEPT`, two strategy families, fresh non-replay invocation, `broker_called=false`, `schedule_activated=false`, and operated readiness/user acceptance both false.
- Temporal/source evidence: collection `2026-07-27T06:17:54.748330+00:00`; KIS latest completed session `2026-07-24` with `FRESH` quality; AgentNews source update `2026-07-27T00:40:00+00:00` with `FRESH` quality; FMP latest PIT-available statement accepted/as-of `2025-12-30T19:00:00+00:00` and disclosed as `PIT_AVAILABLE`.
- The actual OAuth model returned structurally valid mappings that the application rejected semantically because they referenced unknown evidence IDs. Both rows persisted as `parse_status=REJECTED`, `validation_status=REJECTED`, rendered `NO_ENTRY`, and recorded 15 deterministic rejection dispositions. This is the required fail-closed semantic boundary, not an LLM backend failure.
- `research.sqlite` contains two decision snapshots, two feedback runs, two `trade_plan_proposals`, 15 disposition events, and one market snapshot/report. Both generated raw model outputs were retained and read back from SQLite (11,772 and 11,724 bytes); their computed SHA-256 values exactly match `raw_output_ref`. Empty normalized proposal JSON is expected for these semantic parse rejections.
- `ops.sqlite` contains the `ANALYSIS_PERSISTED` analysis row and successful leased pipeline row. `shadow-readback` reproduced Markdown byte-for-byte identically to the original artifact.
- Configured KIS/FMP values and all string values from the local OAuth token document were mechanically scanned against both SQLite files and both Markdown files without printing them; no private value was present. The proxy model map preserved `gpt-5.6-sol` unchanged.

Private/uncommitted artifacts (all mode `0600`):

- `.hermes/uat/2026-07-27-research-milestone-b.sqlite`
- `.hermes/uat/2026-07-27-ops-milestone-b.sqlite`
- `.hermes/uat/2026-07-27-shadow-milestone-b.md`
- `.hermes/uat/2026-07-27-shadow-milestone-b-readback.md`

Verification on this checkpoint:

```text
39 passed                              # Milestone A backend/evaluator proof re-run first
288 passed in 4.94s                    # app + LLM/policy/feedback + KIS/AgentNews regression
compileall prism_core prism_app cores/llm: PASS
broker boundary audit: violations 0
git diff --check: PASS
```

Independent Claude Opus read-only review first exhausted its repository-exploration turn budget and produced no usable verdict. A successful compact frozen-packet retry verified the live-source, deterministic-rejection, raw-output persistence/readback, SHADOW, and no-broker/account legs. Its narrow BLOCK concerned omitted OAuth source, provider-served model identity, and OAuth-token artifact scanning. GPT adjudication: the omitted `oauth_llm.py` source was then inspected and shows localhost-only proxy lifecycle, empty MCP registry, cleanup, and fail-closed activation; the proxy forwards `gpt-5.6-sol` unchanged and the actual accepted response proves that requested model path (provider-internal served-model attestation is not exposed by this adapter); all OAuth document string values were scanned against the artifacts with no match. No unresolved CRITICAL/HIGH task-contract defect remains.

This proves the real diagnostic vertical runtime boundary only. It does not integrate the path into the existing candidate/report/PDF user surface, does not constitute user UAT, and does not establish scheduling, publication, recovery, or operated readiness. No Telegram message, broker/account/balance/holdings/order/cancel/replace effect, schedule activation, credential change, commit, push, or PR occurred.

### 2026-07-27 existing report/dashboard surface checkpoint (`t_d98bf3b9`)

Milestone C re-used the exact persisted Milestone-B run and attached it to the existing report and localhost dashboard surfaces without another provider/model call:

- New `prism_app.user_surface_uat` and `python -m prism_app user-surface` read the exact ops run in SQLite read-only mode, bind the persisted `leadership_report_id` to its `leadership_snapshot_id`, append the bounded SHADOW section to that existing `market_leadership_v1` report, and export the separate research/paper/ops dashboard contract. Mismatched report/snapshot identity fails closed.
- The existing `examples/dashboard/app/page.tsx` now consumes the authoritative `prism_dashboard_v1` contract directly instead of the obsolete real-account/simulator DTO. It shows source time/quality, leaders without fabrication, separate SWING/TREND proposal cards, scenario/evidence/falsifier state, SHADOW feedback, and ops job state. It contains no StockEasy menu name and renders no account, quantity, order, execution approval, or price level.
- Dashboard export now exposes validation status and normalizes every parse/validation rejection to `proposed_decision=NO_ENTRY`, including a non-null model `ENTRY_CANDIDATE`. The RED regression first observed `ENTRY_CANDIDATE != NO_ENTRY`, then passed after backend normalization; frontend normalization remains defense in depth.

Actual persisted user-surface evidence:

```text
python -m prism_app user-surface ...
-> EXISTING_USER_SURFACES_EXPORTED
-> report_id d55ad07a-0689-5140-80b0-e1c6d37438de
-> data_snapshot_id a5ca1080-ca7c-52b9-bbee-ff25341ce2c4
-> broker_called=false; schedule_activated=false
```

- Existing report artifact `.hermes/uat/2026-07-27-existing-report-milestone-c.md` preserves the 26-line leadership report and appends one SHADOW section. It visibly discloses KIS/FMP/AgentNews source/as-of, SWING/TREND `REJECTED`, and `NO_ENTRY` with no price levels.
- Dashboard artifact `examples/dashboard/public/dashboard_data.json` was generated from the same Milestone-B research/ops stores plus an empty versioned internal-paper store `.hermes/uat/2026-07-27-paper-milestone-c.sqlite`. It contains KR `FRESH`, two persisted ops jobs, separate rejected SWING/TREND rows normalized to `NO_ENTRY`, no fabricated security leaders, and no forbidden entry/stop/target/quantity/account/real-portfolio keys.
- A localhost-only production server was started on `127.0.0.1:4173`, checked by HTTP, inspected through the real browser, and stopped. The KR screen was visually coherent and showed timestamp, `FRESH`, both strategy cards, `REJECTED`/`NO_ENTRY`, and the read-only disclaimer. The US switch showed `UNAVAILABLE` and `NO_ENTRY` instead of fabricating absent US data.
- All three new private artifacts are mode `0600`. Seven configured credential/token-document string values were mechanically checked against them without printing values; artifact matches: `0`.

Current verification after review remediation:

```text
406 passed in 7.93s                    # app/dashboard/e2e/features/strategies/feedback/LLM/policy/report/provider selection
dashboard `npm run build`: PASS        # Next 16 production build and static route generation
compileall prism_core prism_app cores/llm: PASS
broker boundary audit: violations 0
git diff --check: PASS
```

`npx tsc --noEmit` remains red on pre-existing dormant legacy dashboard components and duplicate language-provider keys. The current Phase 1 page itself production-builds and does not import those components; deleting or fully repairing all dormant legacy UI is outside this narrow milestone. `npm ci --legacy-peer-deps` was required because the existing React 19 / `vaul@0.9.9` peer range conflicts, and npm reported three existing high-severity dependency advisories; no dependency or lockfile change was made in this card.

Independent Claude Opus read-only review completed with exit `0` and empty stderr. It found no CRITICAL/HIGH blocker. GPT accepted its MEDIUM finding that rejected JSON could retain a non-null `ENTRY_CANDIDATE`, remediated it TDD-first, and added a guard preventing the current page from re-importing dormant real-account components. A focused follow-up review confirmed the JSON invariant `REJECTED -> NO_ENTRY` is fully closed with no new HIGH/CRITICAL defect. Residual LOW observations: the dashboard is a rolling PIT read model rather than one-run-only view, result metadata names the strategy data snapshot while the report body correctly binds the leadership snapshot, and dormant legacy dashboard components still fail standalone full-project TypeScript checking.

No KIS/FMP/AgentNews/OAuth provider call occurred in Milestone C; it reused the exact real persisted Milestone-B evidence. Network activity was limited to npm package installation for the local build and bounded read-only Claude review. No Telegram message, broker/account/balance/holdings/order/cancel/replace effect, schedule/launchd/cron activation, credential change, commit, push, or PR occurred. The localhost server was stopped. Implementation is integrated and agent-observed UAT passed; explicit user acceptance and operated readiness remain pending.

### 2026-07-27 Milestone D closeout preflight (`t_9440fcc2`)

- Milestones A-C are complete and the exact persisted real run has been observed through the existing Markdown report and localhost dashboard. This is agent-observed product UAT; explicit user acceptance remains pending.
- `git fetch --prune origin` completed before closeout. Local `HEAD` and `origin/main` both resolve to `98f088274efc9467e9738ca1eff5d4dac5b81864` with divergence `0 0`; no base reconciliation or conflict resolution is required.
- The intended delivery set is every tracked modification and every untracked implementation/test/plan file listed by Git except `.hermes/uat/` and generated dashboard data. `.hermes/uat/` is now explicitly ignored so private SQLite, Markdown, JSON, WAL, and SHM UAT evidence cannot enter the delivery through broad staging.
- Final independent red-team, definitive task-scoped CI command groups, compileall, dashboard production build, broker-boundary audit, dependency/YAML checks, secret/private-artifact scan, commit/push/PR, and exact-head hosted CI are pending at this checkpoint. No final-green claim should cite earlier milestone gates after this handoff edit.
- Closeout must stop on unresolved CRITICAL/HIGH findings, conflict, secret/private artifact detection, broker/account/order/schedule capability, or an unverifiable required gate. The PR must remain unmerged pending explicit authorization.
- The previously blocked final review gate is now resolved. GPT independently adjudicated the earlier Claude findings, added regression tests first, observed the expected focused RED result (`10 failed, 53 passed`), and remediated the substantiated defects: KIS raw/unadjusted request semantics and as-of guard; live evidence age/field-quality propagation; feature/score provenance in product identity; base daily-run lease serialization; nested dashboard JSON allowlists; and distinct backend-failure versus invalid-model-output persistence/UAT handling. The focused post-remediation suite passed `68` tests before handoff freeze, and a separate KIS future-availability guard test was then added for the definitive run.
- GPT rejected the claim that cross-store recovery depends on a deterministic LLM response. The persisted proposal key replays the exact stored proposal without another model call; a crash before proposal persistence can repeat only a read-only model call and cannot create a broker, publication, schedule, or other external effect in this SHADOW composition.
- A final compact-scope Claude Opus 4.8 read-only follow-up completed successfully with exit `0`, empty stderr, and a direct verdict. It reported no HIGH or MEDIUM finding and marked all seven adjudicated items resolved. GPT verified its material dependency claims against `DailyRunRequest.base_job_key`, `JobRunStore`, `FeedbackRepository`, the quality gate, and the current composition. Its three LOW observations are non-blocking: static user PIT override relies on the trusted-input age guard plus real price quality rather than source-specific field-quality metadata; success completion intentionally fails closed if lease ownership expires at the final boundary; and a future AgentNews timestamp is already rejected by the downstream PIT timing invariant.
- Definitive post-handoff gates passed on the frozen delivery tree: all exact local CI command groups passed (`1,297` tests total, with the workflow's one intentional deselection), `compileall` passed, broker-boundary audit reported `0` violations, dashboard `npm run build` completed its production/static build, `pip check` found no broken requirements, CI YAML parsed, and cached `git diff --check` passed. All `61` intended paths are staged; staged private-path matching found none, two configured credential values were checked against every staged text file without printing values and found `0` matches, and the added-line scan's three secret-shaped strings were independently verified as a redacted representation or explicit fixtures. `gitleaks` is not installed, so exact-value, path, and pattern scans are the available local secret evidence.
- Delivery commit `b301aad68ea6dc9460cd7f55c9cfca19dfc76058` was pushed and PR #42 was opened without merging. Its first hosted matrix reached the new `tests/app` step and failed collection on Python 3.10/3.11/3.12 because the workflow's intentionally minimal dependency list omitted `aiohttp`, now imported by the OAuth composition. This is a CI-environment contract defect, not a test regression. `aiohttp` was added to the existing fail-closed matrix install step, and a fresh minimal virtual environment using that exact dependency set passed all `75` app tests. The corrective commit and replacement exact-head hosted CI remain pending; the PR must remain unmerged for explicit review.

### 2026-07-28 Scenario A honest-completeness checkpoint (`t_03828c78`)

- Added one shared `ProductScenarioState`/`ScenarioAssessment` contract used by the
  structured evaluator, SQLite dashboard readback, daily report, SHADOW Markdown,
  and active localhost dashboard page. Parse/schema failure and policy rejection no
  longer become an invented `NO_ENTRY`.
- A complete accepted scenario now requires persisted identity binding (strategy,
  version, market, security, data snapshot, feature snapshot), accepted evidence
  dispositions, non-empty regime/bull/base/bear paths, observed machine-evaluable
  predicates, falsifiers, uncertainty, and a next-review time. Raw model output
  remains audit-only and is not selected or projected by user surfaces.
- `NO_ENTRY` is complete only when persisted observed values fail explicit
  thresholds. `WATCH` requires at least one pending predicate and a failure
  transition. `REPORT_ONLY` remains distinct and incomplete. Critical declared data
  gaps or any provenance quality other than exact `FRESH` + `ACCEPT` force an
  incomplete non-actionable state.
- The product-composition SQLite replay initially exposed tuple/list divergence in
  the new scenario reasons. The evaluator now emits JSON-stable lists; both KR and
  US persisted-readback regressions pass.
- Focused TDD evidence includes explicit RED→GREEN cases for satisfied WATCH
  predicates, critical-data `NO_ENTRY`, `REPORT_ONLY`, non-eligible feature quality,
  structured missing/stale dashboard projection, and SQLite replay equality. The
  frozen focused suite passed `71` tests before the final provenance hardening; its
  additional focused regression then passed independently.
- Independent Claude read-only review completed with exit `0` and empty stderr. GPT
  accepted and fixed its dashboard structured-data fidelity and `REPORT_ONLY`
  findings, found and fixed the stricter critical-data gap, and then closed Claude's
  final LOW provenance-quality edge. Two follow-ups returned direct `PASS` verdicts
  with no unresolved CRITICAL/HIGH/MEDIUM finding.
- Dashboard `npm run build` passes. Standalone full-project `npx tsc --noEmit`
  remains red on the documented dormant legacy components/types; the active page is
  production-built and does not import those components.
- Definitive broad Python/static/safety/dashboard gates are intentionally pending on
  this handoff-frozen tree. Record their exact results in the Kanban review comment;
  do not retroactively cite earlier milestone counts as final evidence.
- This checkpoint is SHADOW/read-only only. No provider/model smoke, external
  message, broker/account/order effect, credential change, schedule activation,
  push, PR creation/update, or merge is authorized by this card. Scenario B input
  pack changes remain preserved but outside this checkpoint's implementation scope.

### 2026-07-28 Scenario B deterministic input-pack checkpoint (`t_7c550b94`)

- Added one shared, immutable `ScenarioInputPack` contract for KR/US
  `SWING_V1` and `TREND_V1`. It is built only from normalized
  `MarketSnapshot` plus exact strategy `FeatureSnapshot` inputs and remains
  deliberately unwired from legacy candidate/report callers.
- The pack preserves stable security/provider-symbol identity; market and
  snapshot provenance; latest completed-session OHLC; explicit RAW versus
  SPLIT_ADJUSTED basis; corporate-action and adjustment-vintage provenance;
  PIT fundamentals; separate company and market-context evidence; earnings and
  next-review events; and explicit CORE/SUPPLEMENTAL missing, stale, and conflict
  issues with deterministic entry vetoes.
- Separate versioned SWING/TREND indicator sets derive MA/slope, ATR/ATR%,
  realized volatility, gap risk, support/resistance/breakout structure, relative
  strength, 52-week high/peak state, momentum, and volume/liquidity directly
  from aligned completed-session bars. No absent value is invented for an LLM.
- Missing or conflicting core identity, basis, timing, session structure,
  provider quality, strategy-feature identity, raw price discontinuity, or
  liquidity denominator yields `ANALYSIS_INCOMPLETE` + `REPORT_ONLY` with no
  strategy payload. Supplemental gaps preserve deterministic strategy analysis
  but remain visible and veto entry.
- Retry reconciliation found one focused failure left by the timed-out worker:
  RAW history crossing a split was not yet classified. The existing failing
  regression was observed, then fixed. Additional strict RED→GREEN regressions
  cover future-PIT evidence, stale supplemental evidence, stock-dividend/rights/
  spinoff discontinuities, benchmark splits that corrupt relative strength,
  zero-volume formula denominators, stale adjustment vintages, and mixed
  adjustment vintages. The focused suite is green at `24 passed`.
- Earlier neighboring strategy/features/provider verification passed `214`
  tests and the modified app/US diagnostic composition slice passed `22` tests.
  The definitive frozen-tree command then passed `304` app/features/strategies/
  provider tests, `compileall`, the broker-boundary audit with `violations: 0`,
  and `git diff --check`.
- Independent Claude read-only review initially BLOCKED on incomplete RAW
  corporate-action coverage. GPT accepted and remediated the finding TDD-first,
  also accepted the zero-volume and stale-adjustment hardening, and then closed
  the review's benchmark-action and mixed-vintage observations. Two successful
  follow-ups returned PASS; the final current-tree review found no new
  CRITICAL/HIGH/MEDIUM defect. Its remaining LOW notes (duplicate indicator
  implementations, interior-calendar hardening, strict supplemental acceptance,
  and benchmark SPLIT_ADJUSTED provenance) are explicit nonblocking residuals.
- This checkpoint performs no provider/model live smoke and no account, balance,
  holdings, broker, order, cancel/replace, message, credential, schedule, push,
  PR-update, or merge effect. US composition changes remain read-only SHADOW/UAT
  infrastructure and do not activate legacy callers or operated readiness.

### 2026-07-28 Scenario C KR scenario-kernel checkpoint (`t_d11a9e69`)

- The KR configured-target composition now enriches the actual KIS snapshot with
  PIT FMP annual-income observations plus FMP filing/earnings, AgentNews market,
  and internal next-review evidence, then builds one exact immutable scenario
  input pack shared by `SWING_V1` and `TREND_V1`. The evaluator sends that pack,
  exact allowed evidence IDs/features, and deterministic model/prompt/sampling
  provenance to the tool-free OAuth model.
- TDD-first live-remediation cycles closed four observed failures rather than
  substituting fixtures: missing expected model provenance, an invented optional
  market-context gap, predicate validity/quant-score bounds, and regime-score
  divergence. Prompt constraints now mirror the deterministic validator contract.
- Final genuine read-only run under ignored private path
  `.hermes/uat/scenario-c-20260728T031430Z/` returned
  `PERSISTED_READBACK_VERIFIED`, quality `ACCEPT`, a fresh non-replay invocation,
  actual KIS/FMP/AgentNews HTTP 200 evidence, and two ChatGPT OAuth
  `gpt-5.6-sol` structured responses with zero tools. No fixture, manual evidence
  JSON, monkeypatch, cached-only substitute, or synthetic bridge was used.
- Independent SQLite queries proved both rows are `PARSED` + `ACCEPTED` with
  non-null normalized proposals and honest `WATCH` actions. SWING persisted 9
  score components, 4 predicates, 1 stop, 1 target, 10 `ACCEPT`, and 9
  `RECALCULATE` dispositions. TREND persisted 9 score components, 2 predicates,
  2 stops, 2 targets, 13 `ACCEPT`, and 5 `RECALCULATE` dispositions. Both have
  non-empty regime drivers/falsifiers, bull/base/bear evidence, uncertainty,
  future predicate validity/next review, zero missing/stale declarations, and
  persisted observed trigger values plus boolean evaluations. Research contains
  exactly two runs/snapshots/proposals and 37 dispositions; ops contains completed
  `ANALYSIS_PERSISTED` and `SUCCESS` rows with zero active leases.
- Independent Claude read-only review initially found one HIGH trust gap: scenario
  pack issues were only prompt-visible. GPT accepted it, added a focused RED
  regression, and wired every pack entry veto into each deterministic evaluator
  hard-veto set. Follow-up review confirmed the HIGH fully closed, confirmed the
  repository already enforces model/prompt/sampling provenance before persistence,
  and found no new CRITICAL/HIGH/MEDIUM defect. Nonblocking residuals: the internal
  next-review placeholder is self-referential, FMP annual period start assumes a
  calendar fiscal year, and cross-strategy pack vetoes are deliberately fail-closed.
- The focused current-tree suite passed 63 tests before review remediation; the
  accepted-HIGH RED test failed for the six omitted pack vetoes, then it and the
  complete-live-pack neighbor passed. Definitive broad gates and checkpoint commit
  are recorded by the Kanban closeout comment after this handoff freeze.
- This is the KR scenario kernel only, not legacy candidate/report/dashboard
  integration, explicit user UAT, or operated readiness. No account, balance,
  holdings, broker, order, cancel/replace, message, schedule, credential change,
  push, PR update, or merge occurred.

### 2026-07-28 Scenario D US scenario-kernel review checkpoint (`t_536c394a`)

- The current US configured-target composition uses the actual FMP
  non-split-adjusted daily-price path, FMP fundamentals and filing/earnings
  evidence, AgentNews US context, and FMP split-calendar evidence for both
  configured identities (`AAPL` and `SPY`). Coverage is deliberately disclosed as
  `VERIFIED` + `SPLITS_ONLY`; it is not described as full corporate-action
  coverage. Missing, stale, or future coverage for either configured identity
  adds a deterministic RAW-price coverage veto to both strategy inputs.
- Split evidence identity is computed only from the configured symbols, requested
  window, and retained normalized split rows. Unrelated market-wide rows can
  change the raw transport hash but cannot change the domain coverage identity.
  The latest-completed-session boundary and pre-normalization current-session
  filter remain explicit in persisted provenance.
- The genuine private v2 artifact is preserved under ignored path
  `.hermes/uat/scenario-d-v2-20260728T045659Z/`. Its sanitized evidence reports
  FMP price/fundamentals/split-calendar and AgentNews HTTP `200`, ChatGPT OAuth
  `gpt-5.6-sol` with two structured responses and zero tools, quality `ACCEPT`,
  latest completed session `2026-07-27`, price basis `RAW` with
  `FMP_NON_SPLIT_ADJUSTED_ENDPOINT`, split-only coverage for `AAPL` + `SPY`, no
  broker call, no schedule activation, and no credential value.
- Independent read-only SQLite verification found two decision snapshots, two
  feedback runs, two strict proposal records, one market snapshot, one report,
  and 43 append-only field-disposition events. Both proposals re-parse through
  `TradePlanProposal`; their raw-output SHA-256 references, proposal IDs,
  strategy IDs, and decisions match persisted values. SWING is `PARSED` +
  `ACCEPTED` + `WATCH` with one false and one true persisted trigger. TREND is
  `PARSED` + `ACCEPTED` + `ENTRY_CANDIDATE` with all five persisted triggers
  true. Every bull/bear evidence reference belongs to the exact persisted
  decision-snapshot allowlist, both proposals declare zero missing/stale items,
  and ops contains completed `ANALYSIS_PERSISTED` + `SUCCESS` rows with zero
  active leases.
- `read_persisted_shadow` selected the exact analysis job and regenerated Markdown
  byte-for-byte equal to `report.md`; both hashes are
  `7cd3b315712074bcd00d274c86c0b210f7b67fe0a9e04d4a4bf762bd28a9af89`.
  This proves lossless persisted readback of the configured-target US kernel, not
  legacy-surface integration or operated readiness.
- Current frozen-tree verification passed:
  - `python -m pytest tests/agents/test_trade_plan_prompt_contract.py tests/app/test_market_snapshot_composer.py tests/app/test_shadow_report.py tests/app/test_us_product_composition.py tests/app/test_us_product_uat.py tests/data/providers/test_fmp_splits_http.py -q` — `26 passed`.
  - `python -m pytest tests/app tests/agents tests/features tests/strategies tests/data/providers tests/llm tests/policy tests/feedback -q` — `519 passed`.
  - `python -m pytest tests/safety tests/runtime -q` — `72 passed`.
  - `python -m compileall -q prism_core prism_app cores/llm` — passed.
  - `python tools/audit_broker_boundaries.py` — passed with `violations: 0`;
    the 22 legacy-dangerous references remain inventory-only and unchanged.
  - `git diff --check`, added-line secret/injection/debug scans, intended-path
    private-artifact inspection, and comparison against configured secret values
    passed with zero findings.
- The interrupted worker's completed follow-up review was recorded on the card as
  clearing all CRITICAL/HIGH/MEDIUM findings, but its usable stdout was not retained.
  A fresh current-tree Claude read-only repository exploration exhausted its turn
  budget and was discarded. The required frozen-bundle retry completed with exit
  `0`, empty stderr, and `PASS`: no CRITICAL/HIGH/blocking-MEDIUM defect and no
  checkpoint blocker. GPT independently checked the review's material claims.
  Three accepted LOW residuals remain nonblocking: an injected backwards split
  receipt clock fails closed as a validation error rather than the transport's
  sanitized error type; the inherited fixed 16:00 ET observation convention is
  conservative on early-close sessions; and duplicate coverage inputs may render
  duplicate provider symbols while set-based verification remains correct. The
  review's decision/predicate LOW was rejected: deterministic
  `scenario_completeness` explicitly marks contradictory `WATCH`, `NO_ENTRY`, or
  `ENTRY_CANDIDATE` trigger results incomplete, and current SQLite evidence is
  consistent end to end.
- This card performed read-only artifact/SQLite inspection, tests, static audits,
  and review only. It made no new provider/model/network call; account, balance,
  holdings, broker, order, cancel/replace, Telegram/message, schedule, credential,
  migration, deployment, push, PR, merge, or risk/kill-switch effects remain absent.
  The next gate is a local checkpoint commit followed by the required human review;
  explicit user UAT and operated readiness remain pending.

## Immediate next actions

1. Review the integrated report/dashboard artifacts and run the copyable `python -m prism_app user-surface --help` UAT command for explicit user acceptance.
2. Reconcile or remove dormant legacy dashboard components in a separately scoped cleanup if standalone full-project `tsc` becomes a delivery requirement; do not reintroduce real-account DTOs.
3. Keep schedules, Telegram publication, broker, account, and order capabilities disabled until the next separately approved operational gate.

## New/modified files

Tracked modifications currently include:

- `prism_app/daily_pipeline.py`
- `prism_core/data/providers/kis_http.py`
- `tests/data/providers/test_kis_http_transport.py`
- `tests/data/providers/test_kis_provider.py`
- `tests/e2e/test_phase1_feedback_shadow_only.py`
- `tests/integration/test_kis_live_market_data.py`

Untracked implementation/tests currently include:

- `.hermes/plans/2026-07-26_173423-phase1-product-vertical-integration.md`
- `prism_app/__main__.py`
- `prism_app/cli.py`
- `prism_app/live_data_uat.py`
- `prism_app/strategy_evaluator.py`
- `prism_core/features/market_inputs.py`
- `prism_core/strategies/quant_score.py`
- `tests/app/test_cli.py`
- `tests/app/test_live_data_uat.py`
- `tests/app/test_structured_strategy_evaluator.py`
- `tests/features/test_market_inputs.py`
- `tests/strategies/test_quant_score_service.py`

Refresh `git status` because this list may change. Do not commit `.hermes/uat/` by default.

## Current verdict

- Foundations/modules: implemented through the CLI composition/readback and existing report/dashboard seams; final Milestone D local gates passed
- Fixture verification: definitive local CI command groups passed 1,297 tests on the staged delivery tree; exact-head hosted CI pending
- Live provider boundary: one actual KIS/FMP/AgentNews cycle completed with source/as-of evidence and fail-closed deterministic rejection
- Real OAuth LLM smoke: actual OAuth proxy call passed with structured output and tracing disabled; no tools or broker capability
- Deferred structured-output boundary: shape-only SDK contract and deterministic application semantic rejection implemented; task-scoped regressions green
- Product vertical integration: actual KIS/FMP/AgentNews/OAuth SQLite SHADOW cycle persisted and read back through the existing Markdown report and localhost dashboard
- User UAT: agent-observed existing-surface UAT passed; explicit user acceptance remains pending
- Operated readiness: **not ready**
