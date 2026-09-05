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
- Keep `MISSING` as unknown and count only `CONFIRMED` fills as realized samples.
- Never promote a trigger or entry-quality rule to SHADOW or LIVE automatically.
  LIVE requires the trading change harness and explicit user approval.

### Report output

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
- If you could not run validation, say so explicitly and explain why.
- In summaries, reference the files changed and note any operational risk, especially around trading, messaging, or credential handling.
