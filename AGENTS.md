# AGENTS.md — PRISM-INSIGHT Repository Rules

This file governs every agent working from the repository root. It describes mandatory working rules, not historical product behavior.

## 1. Authority and required reading

Read and apply sources in this order when they conflict:

1. `docs/PRODUCT_SCOPE_AND_STRATEGY.md` — approved product, strategy, safety, and operating baseline
2. `AGENTS.md` — mandatory repository working rules
3. `CLAUDE.md` — current-vs-target architecture and developer guide
4. `docs/CLAUDE_AGENTS.md` — agent inputs, outputs, and authority boundaries
5. `.hermes/plans/2026-07-23_204700-prism-current-to-target-transformation.md` — detailed current-to-target execution plan
6. Other historical docs, release notes, and existing code

Existing code describes the current system; it does not override the approved target. If legacy behavior conflicts with the product baseline, preserve it only behind an explicit legacy boundary until it can be migrated safely.

## 2. Product mission

PRISM-INSIGHT is becoming a personal KR/US investment research and decision system for one user on a personal Mac.

Approved baseline:

- KR primary market-data provider: KIS
- LS Securities is a Phase 2 alternative/fallback candidate only; do not add it in parallel in Phase 1
- US primary market-data provider: FMP
- Official evidence sources include KRX/KIND/DART, SEC EDGAR, FRED/ALFRED, and ECOS
- Separate strategy families: `SWING_V1` and `TREND_V1`
- LLMs propose structured `TradePlanProposal` objects; deterministic code validates policy, risk, sizing, and execution eligibility
- Phase 1 supports research, SHADOW feedback, reports, a local dashboard, read-only Telegram interaction, and an internal simulated broker
- Phase 1 makes no broker order calls, including KIS demo calls
- Phase 2 may add KIS broker paper/demo only through durable `OrderIntent` and a hardened `ExecutionService`
- Live trading is not approved and must not be inferred from analysis, backtests, paper results, configuration, or user credentials

## 3. Current system versus target system

The repository still contains a legacy automated-trading flow:

```text
trigger batch -> report agents -> trading scenario -> tracking agent -> KIS order paths
```

The target flow is:

```text
point-in-time snapshot
-> data quality gate
-> strategy-specific quantitative features
-> LLM TradePlanProposal
-> deterministic proposal validator / policy / risk / sizing
-> research, report, SHADOW, or internal paper
```

Do not extend legacy coupling by adding new responsibilities to `stock_tracking_agent.py`, `telegram_ai_bot.py`, or the giant trading prompts. Prefer new small modules under `prism_core/` and thin use-case services under `prism_app/`, then convert legacy entrypoints to wrappers after parity tests.

Keep the currently useful PRISM candidate-selection, report/PDF, Telegram, and scheduling surfaces unless a target contract requires replacement. Do not build a second user-facing product merely because new core modules exist. Once a bounded module is contract-tested and, where applicable, actual-endpoint smoke-tested, integrate it into one narrow current caller, compare parity or SHADOW output, and observe persistence/reporting failure behavior before expanding the next slice. Do not wait for every Phase 1 module to exist before any integration, and do not wire an incomplete contract into a legacy god object. Existing code is a donor: reuse only characterized formulas, renderers, fixtures, and hardened primitives behind the new contracts; retire obsolete paths only after the replacement slice passes its gates.

## 4. Non-negotiable safety rules

### Broker and account effects

- Phase 1 application code must not import, instantiate, or call KR/US broker order adapters.
- `demo` is not equivalent to internal paper. KIS demo is an external broker effect and belongs to Phase 2.
- Never call `.async_buy_stock(...)`, `.async_sell_stock(...)`, cancel, replace, or live account APIs unless the user has explicitly approved a scoped Phase 2 broker-paper task.
- Never enable live mode, add a live mode, reuse live credentials for paper, increase risk limits, or disable a kill switch without a separate explicit approval containing account/order/risk scope.
- In Phase 2, every broker-paper action must require a durable `OrderIntent`; `intent=None` delegation is a migration defect, not an approved compatibility path.
- Unknown broker outcomes must be reconciled before any retry. Never assume timeout means failure.

### Messaging and other external effects

- Environment names do not prohibit Telegram testing. Development, test, and operations may send test messages without per-run approval when the transport is explicitly enabled and both destination chat and inbound user are allowlisted.
- Automated unit/CI paths use fake transport by default for determinism. Live smoke messages must include `[TEST]`, environment, and request ID and must use rate limiting, dedupe, and audit logging.
- Public AgentNews KR/US Markdown endpoints may be fetched live in development, test, and operations without per-run approval. Unit tests use fixtures for reproducibility; live integration/smoke tests validate the network adapter.
- The target interactive bot is allowlisted by both chat ID and user ID and exposes read-only commands only.
- Natural-language input must not mutate orders, risk, credentials, policies, prompts, or kill-switch state.

### External integration verification

- Fixture/fake/mock tests are mandatory for deterministic unit/contract/CI paths, but they never prove an external adapter is live-verified or operated-ready.
- KIS, FMP, KRX/KIND/DART, SEC EDGAR, FRED/ALFRED, ECOS, AgentNews, Telegram, and external LLM transports require a separate bounded live integration/smoke against the actual endpoint before an operated-readiness claim. Sources not covered by a standing approval retain their own explicit capability/approval gate.
- Actual KIS and FMP **market-data-only** live integration/smoke is approved. It must not read account identifiers or call balance, holdings, order, cancel, replace, broker-paper, or live-trading APIs.
- Keep external smoke tests outside default hermetic CI behind explicit markers/jobs. Record only sanitized endpoint, status, provider/model version, timestamps, schema/capability result, latency/quality, and request correlation; never persist secrets, authorization headers, account data, or private raw payloads.
- Missing credentials, entitlement, network, or provider capability must produce a visible skip/block/fail-closed result. Never replace missing live evidence with a fixture, demo key, cached claim, or fabricated output.
- Completion reports must separately state foundation/tests, live integration evidence, runtime wiring, and operated readiness. A passing fixture suite proves only the first state.

### Credentials and user data

- Never display, copy into chat, change, commit, or upload real credentials.
- Treat `.env`, `mcp_agent.secrets.yaml`, `trading/config/kis_devlp.yaml`, OAuth stores, account numbers, Telegram IDs, SQLite databases, reports, PDFs, logs, and generated JSON as private user data.
- Unit tests use fixtures and temporary databases, not the user's databases. Explicit external smoke jobs may use locally configured credentials without printing or changing them. KIS/FMP market-data smoke is allowed; account/broker credentials and effects retain separate Phase 2 approval boundaries.
- Legacy DB migration is copy-only from a read-only source. Do not alter or split `stock_tracking_db.sqlite` in place.

## 5. Strategy and LLM boundaries

### Strategies

- `SWING_V1` and `TREND_V1` require separate IDs, versions, prompts, features, thresholds, books, outcomes, and lessons.
- The same security may appear in both strategy books, but consolidated portfolio risk must aggregate symbol, sector, market, currency, and open-order exposure.
- A lesson is strategy-scoped by default. Cross-strategy use requires separate validation.

### LLMs may

- propose regime probabilities, scores with reasons, entry predicates, stop/target candidates, risk multipliers, re-entry/pyramiding candidates, counter-evidence, falsifiers, and uncertainty;
- summarize evidence and generate reports;
- produce retrospective and lesson candidates.

### LLMs may not

- calculate final order quantity or approve an order;
- create executable `OrderIntent` directly;
- widen a stop, raise configured exposure, authorize averaging down, or bypass policy;
- change code, prompts, configuration, or active lessons through self-feedback;
- use future data or facts unavailable at the declared as-of time.

All LLM outputs that affect decisions must be schema-validated, versioned, stored with raw output and evidence references, and processed using field-level `ACCEPT`, `CLAMP`, `RECALCULATE`, or `REJECT` dispositions.

## 6. Data and research rules

- Preserve `observed_at`, `available_at`, `ingested_at`, and `as_of_date` where applicable.
- Record provider, provider symbol, snapshot ID, and data-quality state.
- Keep raw and adjusted prices explicit; do not silently mix them.
- Missing, stale, partial, or conflicting core data must fail closed for new proposals. A report may be produced with a visible warning only when policy marks the data `REPORT_ONLY`.
- Backtests must use point-in-time universes, corporate actions, delistings, realistic fees/tax/spread/slippage, next-bar or explicitly modeled execution, cash/NAV accounting, and sealed out-of-sample evaluation.
- Backtests do not authorize broker paper or live use.
- Feedback lessons follow `CANDIDATE -> SHADOW`; legacy journal principles are `LEGACY_UNVALIDATED`. `PAPER_PROMOTED` is unavailable until Phase 2 evidence and explicit promotion criteria exist.

## 7. Storage boundaries

Target databases:

- `research.sqlite`: security master, observations, features, proposals, dispositions, outcomes, retrospectives, lessons, and reports
- `paper.sqlite`: strategy books, cash, orders, fills, positions, and NAV
- `ops.sqlite`: job runs, leases, heartbeats, alerts, backups, and recovery events

Use versioned, idempotent migrations. Cross-database identities must be stable application IDs; do not assume cross-database foreign keys. Evidence and audit records are append-only; corrections are new records, not destructive rewrites.

## 8. Preferred commands

Use the smallest, lowest-side-effect validation first.

```bash
python -m compileall -q prism_core
pytest path/to/focused_test.py -q
python weekly_insight_report.py --dry-run
```

Add `prism_app` to `compileall` only after that target package exists.

Legacy analysis commands are not safe defaults. If a task requires them, inspect the call path and force no-message/no-broker behavior before execution. Never assume a command is safe because its name includes `demo`.

Do not run external integration tests with real Telegram, Redis, GCP, other official sources, or LLM credentials unless a standing rule or the user explicitly approved that exact external test. KIS/FMP market-data-only smoke has standing approval; account/order/broker calls do not.

## 9. Engineering rules

- Use async-safe network and I/O patterns in async paths.
- Prefer typed contracts, Pydantic/dataclasses, dependency injection, and explicit capabilities over environment checks scattered through business logic.
- Keep provider adapters separate from strategy, policy, and execution code.
- Keep quantitative values separate from LLM narrative and proposal values.
- Use short explicit SQLite transactions, WAL, foreign keys, and busy timeouts in the new storage layer.
- Preserve sequential LLM-heavy report execution unless bounded parallelism has dedicated rate-limit and failure tests.
- Korean user-facing reports use formal polite style.
- Avoid broad rewrites of legacy modules before characterization tests exist.

## 10. Repository map

Current/legacy:

- `cores/`: KR analysis/report agents and shared utilities
- `prism-us/`: US mirror flows
- `trading/`, `prism-us/trading/`: KIS trading integrations; dangerous Phase 1 boundary
- `stock_tracking_agent.py`: legacy combined tracking/journal/execution flow
- `telegram_ai_bot.py`: legacy large conversational bot
- `tracking/`: legacy DB schema, journal, and memory helpers
- `examples/dashboard/`: current local dashboard

Target additions:

- `prism_core/runtime/`: modes and external-effect capabilities
- `prism_core/data/`: point-in-time contracts, providers, security master, quality
- `prism_core/storage/`: database policy and versioned migrations
- `prism_core/strategies/`: strategy-specific contracts and feature engines
- `prism_core/llm/`: strict proposal schemas and services
- `prism_core/policy/`, `prism_core/portfolio/`: deterministic validation, sizing, and risk
- `prism_core/research/`: backtest and experiment registry
- `prism_core/feedback/`: proposals, outcomes, retrospectives, lessons
- `prism_core/paper/`: internal simulated broker and ledger
- `prism_core/telegram/`: allowlisted read-only integration
- `prism_app/`: thin application services and entrypoints

## 11. Git and change discipline

- Work from the latest `origin/main` on a feature branch for code and product-document changes.
- Before editing, inspect branch, status, remote divergence, and untracked files.
- Never discard, overwrite, stash, commit, push, rebase, merge, or open a PR without respecting the user's stated scope.
- Do not commit or push merely because tests pass; obtain approval when the user requested local-only work.
- Keep changes task-scoped. Separate product decisions, safety foundations, providers, strategies, and execution into reviewable units.

## 12. Before finishing

- Verify every stated requirement and safety invariant.
- Run focused tests plus `git diff --check`.
- Inspect the final diff and list changed/untracked files.
- State what was not tested and why.
- Explicitly report whether any network call, external message, broker call, credential change, commit, push, or PR occurred.
- For consequential architecture, trading, migration, security, or high-regression work, use a read-only independent review and resolve findings before claiming completion.
