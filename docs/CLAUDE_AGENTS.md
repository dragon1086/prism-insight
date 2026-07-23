# PRISM-INSIGHT AI Agent System

> **Status:** Target agent contract plus legacy migration map
>
> **Authority:** [`PRODUCT_SCOPE_AND_STRATEGY.md`](PRODUCT_SCOPE_AND_STRATEGY.md) → [`../AGENTS.md`](../AGENTS.md) → [`../CLAUDE.md`](../CLAUDE.md) → this reference

This document describes what AI agents may observe, propose, and explain. Deterministic data, policy, risk, sizing, storage, and execution services are not agents and must not be replaced by prompts.

## 1. Core principle

```text
Agents produce evidence-linked analysis and proposals.
Code validates, limits, sizes, persists, and executes.
```

No agent can authorize a broker action, calculate final quantity, create an executable `OrderIntent`, raise a configured limit, change a stop to increase risk, activate its own lesson, or modify code/configuration.

Phase 1 has no broker order calls. Phase 2 broker paper requires a validated proposal, deterministic policy, durable `OrderIntent`, and `ExecutionService`. Live remains unapproved.

## 2. Agent input envelope

Every decision-relevant agent receives an immutable input envelope rather than uncontrolled live lookups where practical.

Required metadata:

```text
request_id
market
security_id
strategy_id and strategy_version when applicable
as_of_date / as_of_time
snapshot_id
data_quality_summary
evidence_ids
model_id
prompt_version
```

Rules:

- information unavailable at `as_of_time` is excluded;
- provider and evidence provenance are retained;
- stale, missing, partial, or conflicting facts are declared;
- user text, news, web content, reports, and memory are untrusted data, not executable instructions;
- the raw model response and parsed output are both stored for auditable agents.

## 3. Agent output classes

Agents emit one of these classes:

1. `EvidenceAnalysis` — bounded analysis of supplied evidence
2. `ReportSection` — user-facing narrative with evidence references
3. `TradePlanProposal` — strict strategy-specific proposal
4. `RetrospectiveCandidate` — process or outcome review
5. `LessonCandidate` — inactive hypothesis for SHADOW evaluation
6. `ReadOnlyAnswer` — Telegram/dashboard explanation based on stored records

Free-text output alone cannot alter a strategy, portfolio, lesson state, paper ledger, or broker state.

## 4. Current legacy agents

The current code contains useful analysis capabilities but does not yet enforce all target contracts.

### Analysis team

| Legacy agent | Current file | Reuse direction |
|---|---|---|
| Technical Analyst | `cores/agents/stock_price_agents.py` | produce technical `EvidenceAnalysis` from normalized features |
| Trading Flow Analyst | `cores/agents/stock_price_agents.py` | analyze KR/US flow evidence with explicit availability |
| Financial Analyst | `cores/agents/company_info_agents.py` | use point-in-time fundamentals and revision metadata |
| Industry Analyst | `cores/agents/company_info_agents.py` | identify business/competitive drivers and counter-evidence |
| Information/News Analyst | `cores/agents/news_strategy_agents.py` | separate confirmed facts, interpretation, and uncertainty |
| Market Analyst | `cores/agents/market_index_agents.py` | consume deterministic regime features and official macro evidence |
| Macro Intelligence | `cores/agents/macro_intelligence_agent.py` | propose scenario distribution; code owns final regime policy |
| Investment Strategist | `cores/agents/news_strategy_agents.py` | become report synthesis, not order recommendation authority |

Current sequential execution may be preserved for rate-limit safety. Any new parallelism requires bounded concurrency, deterministic result assembly, retry limits, and tests.

### Communication team

| Legacy agent | Current file | Reuse direction |
|---|---|---|
| Summary Optimizer | `cores/agents/telegram_summary_optimizer_agent.py` | summarize stored structured reports |
| Quality Evaluator | `cores/agents/telegram_summary_evaluator_agent.py` | verify summary fidelity and unsupported claims |
| Translation Specialist | `cores/agents/telegram_translator_agent.py` | optional presentation layer only |
| Portfolio Consultant | `telegram_ai_bot.py` | migrate to read-only `QueryService` |
| Dialogue Manager | `telegram_ai_bot.py` | keep conversational context without mutation authority |

The target product uses one bot and one allowlisted chat. Multi-language broadcast is legacy functionality and not required for the first target slice.

### Legacy trading and journal agents

| Legacy capability | Current file | Target treatment |
|---|---|---|
| Buy Specialist | `cores/agents/trading_agents.py` | replace with strategy-specific `TradePlanProposal` prompts |
| Sell Specialist | `cores/agents/trading_agents.py` | produce exit proposal candidates; deterministic position policy decides |
| Trading Journal | `stock_tracking_agent.py`, `tracking/journal.py` | split into retrospective and inactive lesson-candidate services |

Legacy assumptions such as forced immediate Enter/No Entry, all-in/all-out, no partial fills, prompt-owned thresholds, direct slot sizing, and small-sample score adjustment do not carry into the target architecture.

## 5. Target agent roles

These are logical roles. Do not create a separate LLM agent when deterministic code or a shared prompt profile is sufficient.

### 5.1 Evidence analysts

Evidence analysts explain bounded domains using supplied normalized observations.

Required output:

```text
confirmed_facts[]
interpretations[]
counter_evidence[]
unknowns[]
evidence_ids[]
data_quality_notes[]
```

They may not fetch or invent missing numbers to complete a narrative. A failed domain analysis creates a visible partial result; it does not silently become neutral/bullish.

### 5.2 Market Scenario Analyst

Purpose:

- propose probabilities for base, bullish, and bearish scenarios;
- identify drivers, event calendar, confirmation conditions, and falsifiers;
- distinguish KR- and US-specific transmission paths.

Inputs:

- deterministic regime features;
- official macro releases and release timestamps;
- stored AgentNews KR/US snapshots fetched by the read-only provider adapter;
- market breadth, rates, FX, volatility, and sector evidence;
- [`MARKET_SCENARIO_PROMPTS.md`](MARKET_SCENARIO_PROMPTS.md) contract.

The provider adapter may fetch AgentNews live in development, test, and operations without per-run approval. The LLM receives the stored snapshot as untrusted data and never executes instructions embedded in it. Code validates probability bounds, required evidence, freshness, and scenario completeness. The agent does not set portfolio exposure.

### 5.3 Strategy Proposal Analyst

The target replaces the single legacy buy prompt with strategy-specific prompt profiles.

#### `SWING_V1` profile

- short-horizon setup, catalyst, momentum, liquidity, and invalidation;
- initial research outcome windows: 5/10/20 sessions;
- separate prompt and strategy version.

#### `TREND_V1` profile

- medium-term trend strength, durability, earnings/industry trend, and regime compatibility;
- initial research outcome windows: 20/60/120 sessions;
- separate prompt and strategy version.

These horizons evaluate outcomes; they are not mandatory exit dates.

Required strict output:

```text
proposal_id
strategy_id / strategy_version
market / security_id / snapshot_id
proposed_decision
llm_score and rationale breakdown
regime_distribution
entry_predicates
stop_candidates
target_candidates
risk_multiplier_candidate
reentry_candidates
pyramiding_candidates
bull_evidence_ids
bear_evidence_ids
falsifiers
missing_or_stale_data
uncertainty
model_id / prompt_version
```

Prohibited output effects:

- final quantity;
- final portfolio slots;
- direct execution approval;
- direct `OrderIntent` creation;
- stop widening or policy override;
- assuming that a proposal will be executed.

### 5.4 Exit Proposal Analyst

The exit role may identify:

- thesis invalidation evidence;
- stop/target/trailing candidates;
- regime or fundamental deterioration;
- partial-reduction and full-exit candidates;
- re-entry preconditions after an exit.

Deterministic code owns existing order state, realized fills, quantity, stop monotonicity, no-loss-averaging, and portfolio exposure. Partial fills are execution facts, not prompt assumptions.

### 5.5 Retrospective Analyst

Two isolated passes are required.

#### Process review

Uses only data available at decision time and evaluates:

- evidence sufficiency;
- policy consistency;
- calibration;
- ignored counter-evidence;
- unsupported confidence;
- data-quality handling.

#### Outcome review

Adds only data observed after the outcome window:

- realized path;
- maximum favorable/adverse excursion;
- stop/target events;
- regime transition;
- counterfactual outcome of rejected candidates where measurable.

Outcome quality must not rewrite whether the original process was sound.

### 5.6 Lesson Candidate Analyst

Produces hypotheses, not active rules.

Required fields:

```text
lesson_id
strategy_scope
market/sector/regime scope
condition
tentative_action
supporting_evidence_ids
contradicting_evidence_ids
sample_count
uncertainty
status=CANDIDATE
```

Lifecycle:

```text
LEGACY_UNVALIDATED -> CANDIDATE -> SHADOW
                                     |-> SUSPENDED
                                     |-> RETIRED
```

`PAPER_PROMOTED` is unavailable until Phase 2 criteria and prospective evidence exist. The agent cannot promote, activate, or apply its own lesson.

### 5.7 Read-only Consultation Agent

Used by Telegram and the local dashboard query surface.

Allowed:

- explain current stored reports and proposals;
- compare `SWING_V1` and `TREND_V1`;
- show evidence, freshness, outcomes, SHADOW lessons, internal-paper portfolio/status, and system health;
- answer `/help`, `/status`, `/daily`, `/weekly`, `/symbol`, `/portfolio`, `/paper`, and `/health` queries.

Phase 1 `/portfolio` is internal-paper only. A read-only KIS account snapshot is deferred to Phase 2 and requires separate scoped approval.

Denied:

- `/buy`, `/sell`, `/cancel`, `/live`;
- risk or exposure increases;
- kill-switch changes;
- credential access;
- prompt/policy/config mutation;
- arbitrary tools selected from natural-language content.

Answers must show as-of time, strategy identity, data quality, and uncertainty where relevant.

## 6. Deterministic components are not agents

Do not implement these as LLM prompts:

- `DataQualityGate`
- quantitative feature calculation
- `quant_score`
- schema validation
- evidence existence/freshness checks
- field-level disposition
- hard vetoes
- position sizing
- total exposure and loss limits
- duplicate-order protection
- order state machine
- fill accounting
- reconciliation and restart recovery
- lesson promotion criteria

Their outputs may be explained by agents, but agents cannot alter them.

## 7. Proposal validation boundary

Every `TradePlanProposal` is processed field by field:

```text
ACCEPT       valid proposal value retained
CLAMP        bounded to a safer configured range
RECALCULATE  deterministic code replaces the value
REJECT       proposal or field is unusable
```

Validation covers:

- strict schema and allowed operators;
- snapshot identity and freshness;
- evidence references;
- probability and score bounds;
- strategy/market compatibility;
- entry predicate evaluability;
- stop/target sanity;
- risk multiplier bounds;
- hard policy vetoes.

A parser failure, missing core evidence, stale core data, or unevaluable predicate cannot flow to sizing or paper.

## 8. Agent collaboration pattern

Target orchestration:

```python
snapshot = await data_service.snapshot(...)
quality = quality_gate.evaluate(snapshot)
if quality.rejects_new_proposals:
    return report_only_skip(quality)

for strategy in strategy_registry.enabled_for(market):
    features = feature_service.compute(snapshot, strategy)
    evidence = await evidence_service.analyze(snapshot, features)
    raw_proposal = await proposal_service.propose(strategy, features, evidence)
    disposition = proposal_validator.validate(raw_proposal, snapshot, features)
    repository.append(raw_proposal, disposition)

portfolio_view = risk_service.consolidate(accepted_proposals)
report = report_service.render(snapshot, accepted_proposals, portfolio_view)
```

This is a logical pattern; exact APIs follow the implementation plan. No broker service appears in the Phase 1 analysis pipeline.

## 9. Prompt construction rules

When changing or creating an agent prompt:

1. Define the typed input and strict output schema first.
2. State the as-of boundary and allowed evidence IDs.
3. Separate facts, interpretation, uncertainty, and counter-evidence.
4. Include strategy and prompt versions.
5. Remove policy values that deterministic code owns.
6. Require falsifiers and missing-data declarations.
7. Treat external content as data, never as instructions.
8. Store raw and parsed outputs.
9. Add malformed, partial, stale, injection, and unsupported-claim tests.
10. Do not rely on prose to enforce broker or risk safety.

Model names are runtime configuration, not agent identity. Changing a model requires a recorded experiment and must not silently reuse the previous calibration.

## 10. Migration sequence

1. Characterize existing analysis outputs.
2. Add point-in-time data and evidence contracts.
3. Add strategy identities and deterministic features.
4. Add strict proposal schemas and validators.
5. Run new proposals in report/SHADOW only.
6. Disable legacy score adjustment.
7. Replace legacy Telegram consultation with read-only `QueryService` access.
8. Add internal simulated paper.
9. Only after Phase 1 gates, connect broker paper through `OrderIntent`.

Do not keep adding target behavior to the giant legacy prompt, tracking agent, or Telegram bot merely because they are existing integration points.

## 11. Required tests

Agent-related changes require focused tests for:

- schema validation and unknown fields;
- probability/score bounds;
- missing and stale evidence;
- future-data exclusion;
- unsupported numeric claims;
- prompt injection in news/user text;
- strategy identity separation;
- raw-output retention after parse failure;
- deterministic validator refusal;
- legacy lesson non-activation;
- read-only Telegram command enforcement;
- zero broker imports/calls in Phase 1.

## 12. References

- [Product scope and strategy](PRODUCT_SCOPE_AND_STRATEGY.md)
- [Current and target architecture](../CLAUDE.md)
- [Repository rules](../AGENTS.md)
- [Market scenario prompts](MARKET_SCENARIO_PROMPTS.md)
- [Current-to-target implementation plan](../.hermes/plans/2026-07-23_204700-prism-current-to-target-transformation.md)
- [Legacy trading journal reference](TRADING_JOURNAL.md)

Historical agent descriptions remain useful for locating code, but they do not grant target-system authority.
