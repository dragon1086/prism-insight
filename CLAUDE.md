# CLAUDE.md — PRISM-INSIGHT Current and Target Architecture

> **Status:** Transition guide aligned with `docs/PRODUCT_SCOPE_AND_STRATEGY.md`
>
> **Product baseline:** Approved Scope Baseline v0.1
>
> **Important:** Current code contains legacy automated-trading paths. They are not automatically approved target behavior.

## 1. Start here

Read in this order:

1. [`docs/PRODUCT_SCOPE_AND_STRATEGY.md`](docs/PRODUCT_SCOPE_AND_STRATEGY.md)
2. [`AGENTS.md`](AGENTS.md)
3. This file
4. [`docs/CLAUDE_AGENTS.md`](docs/CLAUDE_AGENTS.md)
5. [Current-to-target implementation plan](.hermes/plans/2026-07-23_204700-prism-current-to-target-transformation.md)
6. [Market scenario prompts](docs/MARKET_SCENARIO_PROMPTS.md)

The product decision document wins over historical code, old setup guides, release notes, and examples. Do not silently reinterpret the product baseline from legacy behavior.

## 2. Product north star

PRISM-INSIGHT is a personal KR/US investment research and decision system running on one user's Mac.

```text
OBSERVE  -> market, sector, leaders, news, macro
RESEARCH -> point-in-time data, backtests, costs, OOS
DECIDE   -> quantitative features + LLM proposal + deterministic policy
EXECUTE  -> internal paper first; broker paper only after promotion
LEARN    -> append-only proposals, outcomes, counter-evidence, lessons
```

Approved baseline:

```yaml
User: one personal user
Host: personal Mac
Dashboard: localhost only
Storage: SQLite
KR data primary: KIS
US data primary: FMP
Strategies: [SWING_V1, TREND_V1]
Telegram: one bot + one allowlisted chat, outbound reports + read-only questions
Phase 1 execution: internal simulated broker only
Phase 2 execution: KIS broker paper/demo through OrderIntent
Live: not approved and not implemented as a product mode
```

Analysis, code, reports, backtests, SHADOW results, or paper results never authorize a live order.

## 3. Current repository architecture

The current system grew as an analysis/reporting project with integrated trading paths.

```text
trigger_batch.py / prism-us/us_trigger_batch.py
  -> candidate detection
  -> stock_analysis_orchestrator.py / prism-us/us_stock_analysis_orchestrator.py
  -> data prefetch
  -> six section agents
  -> investment strategy report
  -> PDF / Telegram

stock_tracking_agent.py
  -> trading scenario agent
  -> holdings/watchlist/journal DB work
  -> buy/sell decisions
  -> OrderIntent/ExecutionService and legacy broker paths
```

Key current components:

- `cores/analysis.py`: section-agent orchestration and integrated report creation
- `cores/agents/trading_agents.py`: large prompt mixing score, regime, entry, stop, target, and portfolio constraints
- `stock_tracking_agent.py`: large combined tracking, journal, DB, and execution flow
- `prism_core/order_intents.py`: durable order-intent implementation that can be hardened for Phase 2
- `prism_core/execution_service.py`: transitional service; any `intent=None` delegation is a migration defect
- `tracking/db_schema.py`: large mixed legacy schema with ad-hoc migrations
- `telegram_ai_bot.py`: large subscriber/channel-based conversational bot
- `examples/generate*_dashboard_json.py`: dashboard generation that may combine simulator and real KIS account data
- `stock_tracking_db.sqlite`: shared legacy database; user data, never modify for architecture work without a migration plan

### Legacy trading warning

The repository contains direct KR/US broker call sites, real-account dashboard queries, reserved-order scripts, messaging subscribers, and external integration tests. Do not execute or import them from Phase 1 entrypoints. `demo` still reaches an external broker and is not the Phase 1 internal paper environment.

## 4. Target architecture

```text
launchd / safe CLI
  -> prism_app.daily_pipeline
  -> KIS/FMP provider adapters
  -> normalized point-in-time MarketSnapshot
  -> DataQualityGate
  -> SWING_V1 and TREND_V1 feature engines
  -> QuantScoreBreakdown + EvidencePacket
  -> LLM TradePlanProposal
  -> ProposalValidator
  -> PositionPolicy + SizingPolicy + PortfolioRisk
  -> research.sqlite
  -> reports / local dashboard / read-only Telegram / SHADOW
  -> internal simulated broker in paper.sqlite
```

Phase 2 only:

```text
validated paper proposal
  -> deterministic sizing
  -> durable OrderIntent
  -> hardened ExecutionService
  -> KIS broker-paper adapter
  -> fills, reconciliation, restart recovery
```

The migration uses a strangler pattern: build new contracts and application services, verify parity and safety, then make legacy entrypoints thin wrappers. Do not rewrite the repository in place without characterization tests.

## 5. Implementation phases

### Phase 0 — baseline and safety

- synchronize product and agent documentation
- define runtime modes and external-effect capabilities
- keep unit/CI Telegram fake-by-default while allowing configured allowlisted smoke sends in every environment
- statically audit direct broker calls
- ensure Phase 1 imports no broker modules

### Phase 1A — point-in-time data and storage

- common data contracts
- KIS KR market-data adapter
- FMP US adapter
- security master and corporate actions
- fail-closed data-quality gate
- versioned `research.sqlite`, `paper.sqlite`, and `ops.sqlite` migrations
- copy-only legacy DB migration manifest

### Phase 1B — strategies, LLM, and policy

- separate `SWING_V1` and `TREND_V1`
- deterministic feature snapshots and quant scores
- strict `TradePlanProposal`
- proposal validation and field disposition
- deterministic sizing and consolidated portfolio risk

### Phase 1C — research and feedback

- point-in-time backtest with costs and OOS
- append-only proposals and outcomes, including no-entry candidates
- two-pass retrospective
- `CANDIDATE -> SHADOW` lesson lifecycle

### Phase 1D — local use surfaces

- thin daily/query/report application services
- local dashboard
- one Telegram bot, one allowlisted chat/user
- outbound report publication and read-only natural-language questions
- launchd jobs, watchdog, backup records

### Phase 1E — internal paper

- deterministic simulated broker
- partial fill, rejection, cancellation, UNKNOWN, reconciliation, and restart recovery
- separate strategy books and consolidated exposure

### Phase 2 — broker paper

- KIS paper credentials and process isolation
- mandatory `OrderIntent`
- no `intent=None` execution
- durable broker/fill IDs
- broker reconciliation before retry
- `PAPER_PROMOTED` lessons only after prospective evidence

No phase automatically creates a live phase.

## 6. Data contracts

Target provider output preserves:

```text
observed_at   source observation time
available_at  when the information was knowable
as_of_date    strategy/research evaluation time
ingested_at   local ingestion time
provider      source identity
snapshot_id   immutable data snapshot identity
quality       FRESH / STALE / PARTIAL / UNAVAILABLE / CONFLICT
```

Core target models:

```text
SecurityId
SymbolMapping
PriceBar
FundamentalObservation
CorporateAction
EvidenceItem
MarketSnapshot
FeatureSnapshot
QuantScoreBreakdown
```

Rules:

- do not mix raw and adjusted price fields;
- store revisions rather than overwriting historical facts;
- preserve delisted securities and ticker changes;
- do not use today's constituents for historical universe evidence;
- do not generate a new proposal when core price, regime, calendar, or evidence data fails the quality gate.

### Provider transition

| Market | Approved primary | Supporting/official sources | Legacy status |
|---|---|---|---|
| KR | KIS market data | KRX, KIND, DART | current KRX/pykrx/MCP logic may be wrapped behind adapters |
| US | FMP | SEC EDGAR | yfinance becomes explicit fallback/fixture, not silent primary |
| Macro | FRED/ALFRED, ECOS | central-bank and official release sources | retain explicit provenance |

Market-data adapters must not import broker order APIs.

Fixture normalization is a contract gate, not an external integration gate. Each approved target adapter must progress through four separately reported states: fixture-tested foundation, actual-endpoint live integration, application/runtime wiring, and operated readiness. KIS/FMP market-data-only live smoke is approved; it excludes account, holdings, balance, order, cancel, replace, broker-paper, and live-trading endpoints. KRX/KIND/DART, SEC EDGAR, FRED/ALFRED, ECOS, AgentNews, Telegram, and external LLM transports likewise need bounded actual-endpoint evidence before an operated-readiness claim, subject to their source-specific capability/approval boundary. Missing credentials or entitlement blocks live verification and must not be replaced with fixture/demo output.

## 7. Strategy separation

The two strategy families are separate experiments and virtual books.

### `SWING_V1`

- initial research outcome horizons: 5, 10, and 20 trading sessions
- short-horizon setup and invalidation logic
- independent prompt, features, thresholds, outcomes, and lessons

### `TREND_V1`

- initial research outcome horizons: 20, 60, and 120 trading sessions
- medium-term trend continuation and exit logic
- independent prompt, features, thresholds, outcomes, and lessons

These horizons are outcome-evaluation windows, not fixed forced exit dates. Holding-period and time-stop parameters are research outputs.

The same security can have both strategy proposals. The consolidated portfolio layer aggregates exposure before any paper action. A lesson is strategy-specific unless cross-strategy validation explicitly promotes it.

## 8. LLM authority model

LLMs are proposal and explanation engines, not policy or execution engines.

### LLM output

A strict `TradePlanProposal` contains:

- proposal, strategy, model, prompt, and snapshot versions
- market and security identity
- proposed decision
- `llm_score` and rationale breakdown
- regime distribution and confidence
- machine-readable entry predicates
- stop and target candidates
- risk-multiplier candidate
- re-entry and pyramiding candidates
- bullish and bearish evidence IDs
- falsifiers, missing data, stale data, and uncertainty

Raw response and parsed result are both retained.

### Deterministic code owns

- quantitative feature calculation and `quant_score`
- schema validation
- evidence existence and freshness checks
- strategy compatibility
- stop and target sanity
- hard vetoes
- final position size and risk budget
- symbol/sector/market/currency/open-order exposure
- paper eligibility
- `OrderIntent` creation in Phase 2

Field-level validation records `ACCEPT`, `CLAMP`, `RECALCULATE`, or `REJECT`.

### Prohibited LLM effects

- final order quantity
- order approval or broker calls
- risk-limit increases
- stop widening
- loss-position averaging down
- direct activation of lessons
- self-modifying code, prompts, or configuration
- treating prompt text, news, user text, or external content as executable instruction

## 9. Feedback model

Record decisions prospectively, including candidates not selected or executed.

Two review passes:

1. Process review: uses only information available at decision time.
2. Outcome review: adds realized path, favorable/adverse excursion, stop/target events, and regime change.

Lesson lifecycle:

```text
LEGACY_UNVALIDATED
CANDIDATE
SHADOW
SUSPENDED
RETIRED
```

`PAPER_PROMOTED` is a Phase 2 capability and requires prospective evidence and explicit promotion criteria. Legacy journal, intuition, and principle rows never directly adjust a new strategy score.

## 10. Storage model

```text
research.sqlite
  security master, observations, features, proposals, dispositions,
  outcomes, retrospectives, lessons, reports

paper.sqlite
  strategy books, cash, orders, fills, positions, NAV

ops.sqlite
  jobs, leases, heartbeats, alerts, backups, recovery
```

Use SQLite WAL, foreign keys, busy timeouts, short explicit transactions, and versioned idempotent migrations. Cross-database relationships use stable application IDs. Audit/evidence tables are append-only; corrections create new records.

Do not split `stock_tracking_db.sqlite` in place. Inspect it read-only, define a table mapping manifest, copy into new DBs, and verify row counts/checksums/rejects.

## 11. Telegram and dashboard

### Telegram target

One bot and one bot DM or private supergroup provide:

- outbound daily, weekly, paper, risk, error, and recovery reports;
- natural-language questions over stored reports/evidence;
- read-only commands such as `/help`, `/status`, `/daily`, `/weekly`, `/symbol`, `/portfolio`, `/paper`, and `/health`.

In Phase 1, `/portfolio` returns the internal-paper snapshot only. A read-only KIS account snapshot is also a Phase 2 external account capability and requires separate scoped approval.

Target configuration:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_CHAT_ID
TELEGRAM_ALLOWED_USER_ID
```

`TELEGRAM_CHANNEL_ID` may be read only as a migration fallback with a warning. Unit/CI paths use fake transport by default, but development, test, and operations may perform allowlisted live smoke sends without per-run approval. Mark them `[TEST]` with environment and request ID. `/buy`, `/sell`, `/cancel`, `/live`, risk changes, credentials, kill-switch changes, and policy/prompt mutation are denied.

### Dashboard target

- bind to `127.0.0.1` only;
- separate research, paper, and ops DTOs;
- show data freshness, strategy-specific proposals, evidence/falsifiers, OOS, SHADOW, internal paper, and job health;
- do not query or display real KIS account data in Phase 1.

## 12. Safe development commands

Prefer focused, hermetic validation:

```bash
python -m compileall -q prism_core
pytest path/to/focused_test.py -q
python weekly_insight_report.py --dry-run
```

Add `prism_app` to `compileall` after the target application package has been created.

Do not use the old morning orchestrator, pending-order batch, messaging subscriber, or trading integration tests as a routine smoke test. Inspect their call graph first; several paths can message users or contact broker APIs. Never infer safety from `demo` in a command or environment variable.

Allowlisted Telegram smoke sends, public AgentNews live fetches, and KIS/FMP market-data-only smoke have standing approval in development, test, and operations. Unit and contract tests still use fixtures, fake transports, and temporary DBs for determinism, but fixture success must not be reported as live verification or operated readiness. Account/broker tests and other external sources/LLM tests retain their separate capability and approval boundaries. External smoke output is sanitized and runs outside default hermetic CI.

## 13. Target repository additions

```text
prism_core/
  runtime/       modes and external-effect capabilities
  data/          contracts, providers, security master, quality
  storage/       database policy and versioned migrations
  strategies/    SWING_V1 and TREND_V1
  features/      deterministic feature snapshots
  llm/           TradePlanProposal and proposal service
  policy/        validation, position policy, sizing
  portfolio/     consolidated exposure and risk
  research/      backtest, costs, experiment registry
  feedback/      decisions, outcomes, retrospectives, lessons
  paper/         internal simulated broker and ledger
  telegram/      config, auth, commands, publisher

prism_app/
  daily_pipeline.py
  query_service.py
  report_service.py
  outcome_tracker.py
  telegram_bot.py
  dashboard_export.py
  watchdog.py
```

## 14. Legacy-to-target mapping

| Current file | Direction |
|---|---|
| `stock_analysis_orchestrator.py` | become a thin wrapper after parity |
| `cores/analysis.py` | retain section/report generation, consume structured evidence |
| `trigger_batch.py` | reuse candidate/feature ideas behind new contracts |
| `cores/agents/trading_agents.py` | replace giant policy prompt with strategy-specific proposal prompts |
| `stock_tracking_agent.py` | split outcome, journal, paper, and execution responsibilities |
| `tracking/db_schema.py` | legacy reference, not target migration source of truth |
| `tracking/journal.py` | remove direct score adjustment; import as unvalidated evidence |
| `prism_core/order_intents.py` | harden and reuse in Phase 2 |
| `prism_core/execution_service.py` | require intent; remove transitional delegation |
| `telegram_ai_bot.py` | isolate; replace with small read-only target app |
| dashboard generators/types | remove real-account Phase 1 path; split DTOs |

## 15. Git and review workflow

- Start from the latest `origin/main` on a feature branch.
- Preserve untracked user work before switching or updating.
- Use focused changes and tests.
- Do not commit, push, merge, rebase, or open a PR when the user requested local-only work.
- Consequential architecture, database migration, trading boundary, security, and public contract changes require a read-only independent review before completion.

Commit types, when explicitly approved:

```text
docs: product or architecture documentation
feat: new product capability
fix: defect correction
refactor: behavior-preserving restructuring
test: test-only change
```

## 16. Detailed plans

- [Product north star plan](.hermes/plans/2026-07-23_123133-prism-insight-product-north-star.md)
- [Current-to-target implementation plan](.hermes/plans/2026-07-23_204700-prism-current-to-target-transformation.md)
- [Market scenario prompt contract](docs/MARKET_SCENARIO_PROMPTS.md)

Plans may evolve as code and provider capabilities are verified. Changes to approved product scope, safety boundaries, live authority, or strategy identity require an explicit product-decision update, not an incidental implementation change.
