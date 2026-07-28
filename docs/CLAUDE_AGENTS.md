# AI Agent System

> Current architecture of PRISM-INSIGHT's report, macro, trading, memory, and communication agents.

**Language**: [English](CLAUDE_AGENTS.md) | [한국어](CLAUDE_AGENTS_ko.md)
**Verified against source**: 2026-07-29

The old “13+ agents” count is no longer a useful contract. PRISM-INSIGHT now has static agent factories, inline report agents, dynamic consultation agents, and workflow/context objects. This document identifies them by execution path instead of marketing count.

## 1. Terms

| Term | Meaning |
|---|---|
| Agent definition | Prompt, logical name, and MCP server names. KR report definitions use `ReportAgent`. |
| Runtime agent | Executable `AgentSpec` or `mcp_agent.agents.agent.Agent` passed to an LLM backend. |
| Workflow object | Deterministic orchestration or conversation state. It is not necessarily an LLM agent. |
| Agent directory | A mapping from stable report section keys to instantiated definitions. |

`ConversationContext` in `telegram_ai_bot.py`, for example, is conversation state rather than a separate “Dialogue Manager” LLM agent.

## 2. Architecture at a glance

Top-level KR and US runs follow this order:

1. Fetch market data and generate macro intelligence.
2. Run trigger screening and send an optional signal alert.
3. Generate each selected stock's report.
4. Ingest the report into the archive.
5. Generate a PDF and Telegram summary.
6. Update holdings, evaluate exits, and evaluate new entries.
7. Persist journal and memory feedback when enabled.

The detailed screening-to-feedback view is in [PIPELINE_ARCHITECTURE_ko.md](PIPELINE_ARCHITECTURE_ko.md).

### Per-stock report flow

KR:

```text
prefetch
  -> six base section definitions
  -> sequential execution by default
     (optional PRISM_PARALLEL_REPORT=true)
  -> investment strategy
  -> executive summary
  -> charts / optional vision
  -> final Markdown
```

US:

```text
prefetch
  -> five yfinance-backed sections sequentially
     while news runs concurrently
  -> investment strategy
  -> executive summary
  -> charts
  -> final Markdown
```

This difference is deliberate: KR preserves the default order-sensitive path, while the US flow overlaps news research with the sequential data-backed sections.

## 3. Runtime and provider layer

The shared report path uses the following boundary:

```text
ReportAgent
  -> AgentSpec + LLMParams
  -> OpenAIAgentsBackend
  -> OpenAI Responses API
```

Key files:

- `cores/agents/report_agent.py`: lightweight `ReportAgent` definition.
- `cores/llm/ports.py`: backend-neutral `AgentSpec`, `LLMParams`, and result types.
- `cores/llm/backends/openai_agents_backend.py`: executable OpenAI Agents backend.
- `cores/report_generation.py`: runtime conversion, strategy, and summary generation.

Trading, macro, parts of communication, and US report definitions still use `mcp_agent.agents.agent.Agent` where an augmented LLM and MCP attachment are required. Do not assume both agent types have the same constructor.

## 4. Default model matrix

Defaults are call-site or environment-variable defaults, not a promise that every agent uses one model.

| Workflow | Default model | Effort | Source/override |
|---|---|---|---|
| Shared report sections, strategy, summary | `gpt-5.6-terra` | `medium` | `REPORT_MODEL`, `REPORT_REASONING_EFFORT` |
| KR/US trading judgment | `gpt-5.6-sol` | `high` | tracking call sites |
| Telegram consultation | `gpt-5.6-terra` | `medium` | `report_generator.py` |
| Telegram translation | `gpt-5.6-luna` | backend default | `translate_telegram_message()` |
| Macro intelligence | `gpt-5.4-mini` | `none` | market orchestrators |
| Telegram summary evaluation/optimization | `gpt-5.4-mini` | `none` | `telegram_summary_agent.py` |
| Trading journal | `gpt-5.4-mini` | `none` | `tracking/journal.py` |
| Memory compression | `gpt-5.4` | `none` | `tracking/compression.py` |

Always inspect the current call site before changing model behavior. A model name in a prompt factory alone may be overridden at execution time.

## 5. Agent inventory

### 5.1 KR report agents

The directory returned by `cores.agents.create_agents()` contains six base definitions.

| Section key | Factory/definition | Primary evidence |
|---|---|---|
| `price_volume_analysis` | `create_price_volume_analysis_agent()` | Price, volume, technical structure |
| `investor_trading_analysis` | `create_investor_trading_analysis_agent()` | Investor supply/demand |
| `company_status` | `create_company_status_agent()` | Financial and business status |
| `company_overview` | `create_company_overview_agent()` | Company, products, competitors |
| `news_analysis` | `create_news_analysis_agent()` | Recent news, sector leaders, catalysts |
| `market_index_analysis` | `create_market_index_analysis_agent()` | KOSPI/KOSDAQ and market context |

Prefetched data is preferred. The relevant MCP server is retained as a fallback when prefetch is unavailable.

`investment_strategy_agent` and `summary_agent` are not registry entries. `cores/report_generation.py` constructs both inline after the six base sections complete.

### 5.2 US report agents

`prism-us/cores/agents/__init__.py` maps the corresponding US definitions:

| Section key | Factory |
|---|---|
| `price_volume_analysis` | `create_us_price_volume_analysis_agent()` |
| `institutional_holdings_analysis` | `create_us_institutional_holdings_analysis_agent()` |
| `company_status` | `create_us_company_status_agent()` |
| `company_overview` | `create_us_company_overview_agent()` |
| `news_analysis` | `create_us_news_analysis_agent()` |
| `market_index_analysis` | `create_us_market_index_analysis_agent()` |

The shared strategy and summary generators are reused.

### 5.3 Macro intelligence

- KR: `create_macro_intelligence_agent()`
- US: `create_us_macro_intelligence_agent()`

These agents research leading/lagging sectors, risks, themes, and events. The numerical screening regime is computed deterministically before the prompt and must not be replaced by an LLM guess.

### 5.4 Trading agents

| Market | Buy | Sell |
|---|---|---|
| KR | `create_trading_scenario_agent()` | `create_sell_decision_agent()` |
| US | `create_us_trading_scenario_agent()` | `create_us_sell_decision_agent()` |

They provide scenario judgment inside deterministic score, portfolio, cooldown, and execution gates. The six-section report registry does not own these agents.

### 5.5 Journal and memory

| Definition | Status |
|---|---|
| `create_trading_journal_agent()` | Active through `JournalManager` / US manager when enabled |
| `create_memory_compressor_agent()` | Active in KR compression |
| `create_context_retriever_agent()` | Defined, no production call site currently found |
| `create_intuition_validator_agent()` | Defined, no production call site currently found |

Do not describe “defined but not wired” definitions as active production agents.

### 5.6 Communication

- `create_telegram_summary_optimizer_agent()`
- `create_telegram_summary_evaluator_agent()`
- `create_telegram_translator_agent()`
- `translate_telegram_message()`

The evaluator/optimizer loop targets a concise Telegram message and iterates until the configured quality threshold is met.

### 5.7 Dynamic consultation and research agents

`report_generator.py` builds additional agents at runtime, including:

- KR/US evaluation and follow-up agents
- journal conversation agent
- Firecrawl search analyst and follow-up agent

They are dynamic prompt instances, not stable entries in `cores/agents/__init__.py`.

### 5.8 Visual role gallery

The illustrations below are the original PRISM-INSIGHT role images. They are
grouped by their current responsibility; the executable contracts remain the
factories and call sites listed above.

<table>
  <tr>
    <td align="center"><img src="images/aiagent/technical_analyst.jpeg" alt="Technical Analyst" width="150"><br><strong>Technical Analyst</strong><br><code>price_volume_analysis</code></td>
    <td align="center"><img src="images/aiagent/tranding_flow_analyst.jpeg" alt="Trading Flow Analyst" width="150"><br><strong>Trading Flow Analyst</strong><br><code>investor_trading_analysis</code></td>
    <td align="center"><img src="images/aiagent/financial_analyst.jpeg" alt="Financial Analyst" width="150"><br><strong>Financial Analyst</strong><br><code>company_status</code></td>
  </tr>
  <tr>
    <td align="center"><img src="images/aiagent/industry_analyst.jpeg" alt="Industry Analyst" width="150"><br><strong>Industry Analyst</strong><br><code>company_overview</code></td>
    <td align="center"><img src="images/aiagent/information_analyst.jpeg" alt="Information Analyst" width="150"><br><strong>Information Analyst</strong><br><code>news_analysis</code></td>
    <td align="center"><img src="images/aiagent/market_analyst.jpeg" alt="Market Analyst" width="150"><br><strong>Market Analyst</strong><br><code>market_index_analysis</code></td>
  </tr>
  <tr>
    <td align="center"><img src="images/aiagent/investment_strategist.jpeg" alt="Investment Strategist" width="150"><br><strong>Investment Strategist</strong><br>Inline strategy synthesis</td>
    <td align="center"><img src="images/aiagent/summary_specialist.jpeg" alt="Summary Optimizer" width="150"><br><strong>Summary Optimizer</strong><br>Telegram summary</td>
    <td align="center"><img src="images/aiagent/quality_inspector.jpeg" alt="Quality Evaluator" width="150"><br><strong>Quality Evaluator</strong><br>Evaluator-optimizer loop</td>
  </tr>
  <tr>
    <td align="center"><img src="images/aiagent/translator_specialist.png" alt="Translation Specialist" width="150"><br><strong>Translation Specialist</strong><br>Multilingual distribution</td>
    <td align="center"><img src="images/aiagent/buy_specialist.jpeg" alt="Buy Specialist" width="150"><br><strong>Buy Specialist</strong><br>Entry scenario judgment</td>
    <td align="center"><img src="images/aiagent/sell_specialist.jpeg" alt="Sell Specialist" width="150"><br><strong>Sell Specialist</strong><br>Exit judgment</td>
  </tr>
  <tr>
    <td align="center"><img src="images/aiagent/portfolio_consultant.jpeg" alt="Portfolio Consultant" width="150"><br><strong>Portfolio Consultant</strong><br>Dynamic consultation</td>
    <td align="center"><img src="images/aiagent/dialogue_manager.jpeg" alt="Dialogue Manager" width="150"><br><strong>Conversation Context</strong><br>Workflow state, not a standalone LLM agent</td>
    <td></td>
  </tr>
</table>

## 6. Registry and orchestration contracts

The current KR public entry point is:

```python
from cores.analysis import analyze_stock

report_markdown = await analyze_stock(
    company_code="005930",
    company_name="삼성전자",
    reference_date="20260729",
    language="ko",
    macro_context=macro_context,
)
```

`analyze_stock()` returns final Markdown text. `reference_date` uses `YYYYMMDD`, not `YYYY-MM-DD`.

The six stable section keys are consumed by the analysis and report assembly path. Renaming a key requires updating its consumers and tests. Agent factories are instantiated by the directory; do not return creator lambdas as directory values.

## 7. Adding or changing an agent

### 7.1 Add a shared KR report definition

Use `ReportAgent`, whose contract is `name`, `instruction`, and `server_names`.

```python
# cores/agents/ownership_agent.py
from cores.agents.report_agent import ReportAgent


def create_ownership_agent(
    company_name: str,
    company_code: str,
    reference_date: str,
    language: str = "ko",
) -> ReportAgent:
    return ReportAgent(
        name="ownership_analysis_agent",
        instruction=f"""
Analyze ownership changes for {company_name} ({company_code})
as of {reference_date}. Write in {language}.
""".strip(),
        server_names=["kospi_kosdaq"],
    )
```

Then:

1. Add the factory and stable key to `cores/agents/__init__.py`.
2. Add the key to the base-section order in `cores/analysis.py`.
3. Decide whether prefetch can remove the MCP dependency.
4. Update report assembly and prompts that enumerate sections.
5. Add a focused test for the directory key, prompt inputs, and final section.

Do not pass `description=` or `mcp_servers=` to `ReportAgent`; those belonged to an older example.

### 7.2 Add an MCP runtime agent

Only use this form in workflows that execute an augmented LLM directly:

```python
from mcp_agent.agents.agent import Agent


def create_runtime_agent() -> Agent:
    return Agent(
        name="runtime_agent",
        instruction="Return a structured decision with evidence.",
        server_names=["sqlite", "time"],
    )
```

The caller remains responsible for attaching the configured LLM/backend and executing it. Follow an existing trading or journal call site rather than mixing this constructor into the shared report directory.

## 8. MCP names and configuration

Logical server names come from `cores/llm/mcp_servers.yaml` and the legacy-compatible config loader:

- `firecrawl`
- `perplexity`
- `webresearch`
- `deepsearch`
- `kospi_kosdaq`
- `sqlite`
- `time`
- `yahoo_finance`

The legacy example also defines `sec_edgar`; it is not currently in the native registry.

The native registry and `mcp_agent.config.yaml.example` must stay aligned. See [SETUP_ko.md](SETUP_ko.md) for the pinned Firecrawl and Perplexity commands.

## 9. Verification and maintenance

For agent changes, run the smallest affected tests plus the report path:

```bash
pytest tests -q
python3 demo.py AAPL --language ko
python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

The latter two can make network/LLM calls; use them only with the intended credentials and cost budget.

Maintenance rules:

- Update this file and `CLAUDE_AGENTS_ko.md` together.
- Derive model tables from execution call sites, not marketing copy.
- Distinguish static factories, inline agents, dynamic agents, and workflow state.
- Preserve sequential report ordering unless the existing parallel flag/path is explicitly under test.
- Keep KR and US differences visible instead of calling them identical.

## 10. Related documents

- [Korean version](CLAUDE_AGENTS_ko.md)
- [Pipeline architecture](PIPELINE_ARCHITECTURE_ko.md)
- [Screening and batch algorithms](TRIGGER_BATCH_ALGORITHMS.md)
- [Trading journal and memory](TRADING_JOURNAL.md)
