# AGENTS.md - Codex Guide for PRISM-INSIGHT

This file governs the repository rooted here.

## Project Summary

PRISM-INSIGHT is an AI-powered Korean/US stock analysis and automated trading system built around:

- Python 3.10+
- GPT-5 / Claude based analysis agents
- SQLite storage
- Telegram delivery
- KIS trading APIs
- KR and US market flows

Primary source material for project context lives in `CLAUDE.md` and supporting docs under `docs/`.

## Repository Map

- `cores/`: main analysis engine, report generation, agent definitions, ChatGPT OAuth proxy
- `cores/agents/`: specialized analysis, communication, and trading agents
- `trading/`: Korean trading integration and account handling
- `prism-us/`: US market mirror flows and trading support
- `tracking/`: journal, memory, trading state helpers
- `messaging/`: Redis and GCP Pub/Sub messaging
- `tests/`: targeted regression tests
- `docs/`: setup, troubleshooting, and agent references

## Preferred Commands

Use targeted, low-side-effect commands first.

### Setup

```bash
pip install -r requirements.txt
python3 -m playwright install chromium
```

### Local analysis runs

```bash
python stock_analysis_orchestrator.py --mode morning --no-telegram
python prism-us/us_stock_analysis_orchestrator.py --mode morning
python demo.py 005930
python demo.py AAPL --market us
python weekly_insight_report.py --dry-run
```

### Focused tests

```bash
pytest tests/test_trading_journal.py
pytest tests/test_tracking_agent.py
pytest tests/test_portfolio_reporter.py
pytest tests/test_multi_account_domestic.py
```

Avoid broad production-like runs unless the task requires them.

## Change Rules

- Default to safe paths: prefer `--no-telegram`, `--dry-run`, demo mode, or isolated tests.
- Do not change or commit real credentials, tokens, or secrets in `.env`, `mcp_agent.secrets.yaml`, or `trading/config/kis_devlp.yaml`.
- Treat generated logs, PDFs, JSON outputs, and SQLite databases as user data unless the task explicitly targets them.
- Keep changes narrow and consistent with existing patterns; this repo has substantial behavior encoded in prompts and orchestration order.

## Authorized PR merge and deployment workflow

- User instruction (2026-09-06): for PRISM-INSIGHT task-scoped, validated changes,
  proceed with merge and deployment without waiting for a separate approval review.
- The main branch requires zero approving reviews by explicit user request.
  Preserve the PR requirement, CI/status checks, and all other branch protections.
- Check the exact PR head, dependency order, relevant tests, and CI before merging.
- Any feature change, incident repair, or deployment request must first apply the
  change-scope verification gates in docs/SERVER_GIT_OPERATIONS_ko.md. This is
  mandatory task routing, not optional memory recall. Record pre-deploy tests,
  post-deploy smoke, and whether the first scheduled run has actually completed.
  Do not interpret the review waiver as permission to merge failing or unrelated work.
- Follow docs/SERVER_GIT_OPERATIONS_ko.md: clean target, verified commit, ff-only
  deployment, no destructive reset/stash/clean, and no credential/runtime-data edits.
- BTC deployment remains Bybit demo unless the user separately approves real funds.
  New risk-budget defaults, strategy promotion, and unrelated app-server deployments
  are not authorized merely by this review-workflow preference.
- See docs/BTC_TPSL_DEPLOYMENT_2026-09-06_ko.md for the authorization and initial rollout evidence.

## Engineering Rules

### Async and I/O

- In async flows, use non-blocking patterns.
- Do not introduce blocking network calls such as `requests.get(...)` inside async execution paths; use the repo's async approach instead.

### Agent execution

- Preserve sequential execution of analysis agents unless there is clear existing infrastructure for safe parallelism.
- Do not replace sequential report generation with `asyncio.gather(...)` for LLM-heavy sections; rate limits and prompt ordering matter here.
- Market analysis may use cache-aware behavior; preserve that pattern when editing orchestration.

### Trading and data safety

- Default trading behavior should remain safe (`demo` unless explicitly required otherwise).
- Preserve portfolio constraints and stop-loss logic unless the task explicitly changes trading rules.
- When parsing KIS API numeric fields, prefer existing safe conversion helpers over direct casts.
- Before changing screening, regime, entry, exit, or sizing behavior, follow
  `docs/TRADING_CHANGE_REVIEW_HARNESS.md`. In particular, do not generalize a
  trigger-local failure into a global hard gate without testing counterexamples.
- When a request proposes introducing, combining, replacing, or retuning a
  strategy, trigger, factor, regime switch, exit, or sizing rule (including an
  LLM prompt that changes economic decisions), apply the Strategy adoption and
  fit gate in that harness before implementation. Natural-language proposals
  count; no special command is required. Compatibility review must trace actual
  screening code through report inputs, BUY/SELL prompts, deterministic gates,
  execution and exit management; conceptual strategy fit or prompt-only review
  is insufficient. Record intended stage differences versus contradictions,
  source locations and same-candidate tests before claiming compatibility.
  Keep execution/data bug repair separate
  and do not delay a proven safety fix under the guise of strategy research.

### BTC roadmap governance

- Before BTC strategy, execution, backtest, risk, observability, or deployment work,
  read `docs/BTC_ROADMAP_ko.md` and identify the milestone/task ID and prerequisites.
- Follow its evidence gates; keep execution-safety fixes separate from strategy experiments.
- At completion, update roadmap status only with verified evidence and distinguish
  implementation, tests, deployment, forward observation, and profitability proof.
- Agentmemory is a pointer/reminder, not the authoritative roadmap or status ledger.
- Roadmap review is development-time only; do not add it to the live trading loop.

### Entry-quality analysis

- Route requests such as “매수품질 데이터 분석해줘”, entry-quality validation,
  trigger comparison/replacement, replay, or entry-rule promotion review through
  `skills/prism-entry-quality-analysis/SKILL.md`.
- Read `docs/ENTRY_QUALITY_EVOLUTION_ko.md` and
  `docs/ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md` before interpreting the data.
- Generate a deterministic Evidence Packet with
  `tools/build_entry_quality_evidence_packet.py`; do not substitute ad-hoc SQL,
  fuzzy joins, or reconstructed fills.
- Keep `MISSING` as unknown. Strategy-ledger entries/exits are independent of broker
  funding and fills; do not exclude valid strategy outcomes because a broker order
  was rejected. Broker-realized PnL requires separate confirmed entry/exit evidence.
- Never promote a trigger or entry-quality rule to SHADOW or LIVE automatically.
  LIVE requires the trading change harness and explicit user approval.

### Report output

- BTC trade notifications must follow `docs/BTC_POSITION_MESSAGE_CONTRACT_20260915_ko.md`:
  preserve detailed entry/add/reduction/exit/recovery context, verified margin mode,
  exchange leverage, position margin and same-account equity ratio with currency
  and capture time. Never substitute strategy capital/exposure for broker figures,
  attach another position's snapshot to an old exit, or delay protection/execution
  for optional notification enrichment. Missing data stays explicitly unknown.
  Public notices must be compact and event-specific: do not append raw account
  snapshots or repeated unknown fields to exits. Keep full diagnostic data, group
  essential missing-data warnings, and never hide unconfirmed settlement status.
- Korean report text must use formal polite style.
- Preserve existing prompt and report structure unless the task explicitly requests prompt/report redesign.

## File-Specific Notes

- `cores/report_generation.py`: common report tone and section formatting rules
- `cores/analysis.py`: sequential orchestration and section integration
- `cores/agents/*.py`: prompt logic and agent responsibilities
- `stock_tracking_agent.py`: trading loop, sell decisions, optional journal flow
- `telegram_ai_bot.py`: user consultation flows and conversation context

## Before Finishing

- Run the smallest relevant test or command that validates the change.
- For screening/provider changes, unit tests or AST-extracted tests alone are insufficient:
  run real-module KR consumer and US morning/afternoon batch-to-JSON integration
  tests with realistic flat/MultiIndex/empty/ambiguous provider fixtures. Keep
  network, broker orders and channel sends disabled in integration tests. After
  deployment, verify the production Python and a bounded read-only provider
  smoke before claiming operational recovery; do not rerun live orders as a test.
- If you could not run validation, say so explicitly and explain why.
- In summaries, reference the files changed and note any operational risk, especially around trading, messaging, or credential handling.
