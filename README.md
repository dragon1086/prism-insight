<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="240">
  &nbsp;&nbsp;
  <a href="assets/characters/priso/README.md">
    <img src="assets/characters/priso/v1.0/priso_master_transparent.png" alt="Priso, the PRISM mascot" width="240">
  </a>
  <br>
  <sub><strong>Priso</strong> · Official PRISM Mascot</sub>
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--6_%7C_GPT--5.6-green.svg" alt="OpenAI GPT-6 and GPT-5.6">
  <img src="https://img.shields.io/badge/Anthropic-Claude_Sonnet_5.5_(optional)-green.svg" alt="Anthropic Claude Sonnet 5.5 (optional)">
  <img src="https://img.shields.io/badge/ChatGPT_Plus-Codex_OAuth-ff6b35.svg" alt="ChatGPT Plus">
</div>

[![CI](https://github.com/dragon1086/prism-insight/actions/workflows/ci.yml/badge.svg)](https://github.com/dragon1086/prism-insight/actions/workflows/ci.yml)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/2f8fd766b0634c068ff9da57ccda00c6)](https://app.codacy.com/gh/dragon1086/prism-insight/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **AI-Powered Stock Market Analysis & Trading System**
>
> 13+ specialized AI agents collaborate to detect surge stocks, generate analyst-grade reports, and execute trades automatically.

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_ko.md">한국어</a> |
  <a href="README_ja.md">日本語</a> |
  <a href="README_zh.md">中文</a> |
  <a href="README_es.md">Español</a>
</p>

### Platinum Sponsor

<div align="center">
<a href="https://wrks.ai/en">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/en)**

AI3, creator of **WrksAI** - the AI assistant for professionals,<br>
proudly sponsors **PRISM-INSIGHT** - the AI assistant for investors.
</div>

---

## NEW: Stance — Which System Trading Strategy Is Winning Right Now?

<p align="center">
  <img src="docs/images/stance-ecosystem-en.png" alt="Stance strategy leaderboard comparing return, worst drawdown, average invested exposure, and record rate across KRX and US strategies" width="100%">
</p>

**Past results? We don't take them.** Every Stance record starts at registration — no uploaded track records, no backfills. Decisions and outcomes build one continuous public record that shows a strategy's **actual skill and risk**, not a cherry-picked highlight reel. Rankings are split into KRX and US and show return beside worst drawdown, average invested exposure, and record rate.

- **Find what is working now** — compare every strategy under one rulebook
- **Look past the headline return** — see risk, real exposure, and missing records
- **Trust the timeline** — the server seals decision time and price, then calculates what happens next
- **Enter your own strategy** — let a coding agent discover, register, connect, and test it

**[See the live leaderboard](https://analysis.stocksimulation.kr/?tab=stance)** · **[Enter my strategy](https://analysis.stocksimulation.kr/?tab=stance)** · **[Read the quickstart](stance/QUICKSTART.md)**

<details>
<summary><strong>How does my strategy join?</strong></summary>

<p align="center">
  <img src="docs/images/stance-integration-en.png" alt="Connect a strategy to Stance by opening its project, pasting one instruction into a coding agent, reviewing detected strategies and profiles, and approving automatic registration and integration" width="100%">
</p>

Open your strategy project in **Codex CLI, Cursor, Claude Code, or another coding agent**, then paste the instruction copied from the Stance dashboard into its chat. The agent finds separate strategies and KRX/US portfolios, asks only for missing public profile details, shows the registration plan, and proceeds only after your approval. It also stores keys, changes the code, and runs tests.

Your strategy appears under **Building a record** from its first decision. For stock markets, official ranking begins after **63 trading days and 20 closed trades that each used at least 1% of assets**. The record starts on connection day; historical results cannot be backfilled. No live brokerage account, balance, or broker key is required.
</details>

---

## NEW: ChatGPT Plus/Pro Subscription Support

**No API key? No problem.** PRISM-INSIGHT now supports running analysis directly through your ChatGPT Plus ($20/mo) or Pro ($200/mo) subscription via the **Codex OAuth Proxy**.

```bash
# One-time login (browser will open for ChatGPT auth)
python -m cores.chatgpt_proxy.oauth_login

# Re-authenticate (switch account, or refresh expired tokens)
python -m cores.chatgpt_proxy.oauth_login --force

# Run with your ChatGPT subscription
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning
```

> Tokens auto-refresh in the background, so you only need to log in again if you change ChatGPT accounts or your password.

Zero API bills. Same powerful analysis. Your existing subscription does the work.

---

## Mobile App

<div align="center">

**Get AI stock analysis on the go**

<a href="https://play.google.com/store/apps/details?id=com.prisminsight.prism_mobile">
  <img src="https://img.shields.io/badge/Google_Play-Download-green?style=for-the-badge&logo=google-play" alt="Google Play">
</a>
<a href="https://apps.apple.com/us/app/prism-insight-stock-analysis/id6759331074">
  <img src="https://img.shields.io/badge/App_Store-Download-blue?style=for-the-badge&logo=apple" alt="App Store">
</a>

</div>

- **Smart Filtering** — Receive only the Telegram alerts you care about
- **PDF Reports** — Mobile-optimized AI analysis reports

---

## Watch PRISM-INSIGHT in Action

[![PRISM-INSIGHT Demo](https://img.youtube.com/vi/zAywb1G0wRA/maxresdefault.jpg)](https://www.youtube.com/watch?v=zAywb1G0wRA)

---

## Try It Now (No Installation Required)

### 1. Live Dashboard
See AI trading performance in real-time:
**[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. Telegram Channels
Get daily surge stock alerts and AI analysis reports:
- **[English Channel](https://t.me/prism_insight_global_en)**
- **[Korean Channel](https://t.me/stock_ai_agent)**
- **[Japanese Channel](https://t.me/prism_insight_ja)**
- **[Chinese Channel](https://t.me/prism_insight_zh)**
- **[Spanish Channel](https://t.me/prism_insight_es)**

### 3. Sample Report
Watch an AI-generated Apple Inc. analysis report:

[![Sample Report - Apple Inc. Analysis](https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg)](https://youtu.be/LVOAdVCh1QE)

---

## Try in 60 Seconds (US Stocks)

The fastest way to try PRISM-INSIGHT. Only requires an **OpenAI API key**.

```bash
# Clone and run the quickstart script
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
./quickstart.sh YOUR_OPENAI_API_KEY
```

This generates an AI analysis report for Apple (AAPL). Try other stocks:
```bash
python3 demo.py MSFT              # Microsoft
python3 demo.py NVDA              # NVIDIA
python3 demo.py TSLA --language ko  # Tesla (Korean report)
```

> **Get your OpenAI API key** from [OpenAI Platform](https://platform.openai.com/api-keys)
>
> **Optional**: Add a [Perplexity API key](https://www.perplexity.ai/) to `mcp_agent.config.yaml` for news analysis
>
> **Optional**: Add `ADANOS_API_KEY` to enrich US stock news analysis with structured social sentiment context

Your AI-generated PDF reports will be saved in `prism-us/pdf_reports/`.

<details>
<summary>Or use Docker (no Python setup needed)</summary>

```bash
# 1. Set your OpenAI API key
export OPENAI_API_KEY=sk-your-key-here

# 2. Build and start the local quickstart image
docker compose -f docker-compose.quickstart.yml up --build -d

# 3. Run analysis
docker exec -it prism-quickstart python3 demo.py NVDA
```

The first run builds the image locally, so it may take several minutes.

Reports will be saved to `./quickstart-output/`.

</details>

---

## Full Installation

### Prerequisites
- Python 3.10+ or Docker
- OpenAI API Key ([get one here](https://platform.openai.com/api-keys)) or ChatGPT Plus/Pro subscription

### Option A: Python Installation

```bash
# 1. Clone & Install
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. Install Playwright for PDF generation
python3 -m playwright install chromium

# 3. MCP servers (Firecrawl, Perplexity, ...) start on demand via npx/uv,
#    as listed in mcp_agent.config.yaml — no separate install step

# 4. Setup config
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
cp trading/config/kis_devlp.yaml.example trading/config/kis_devlp.yaml
# Edit mcp_agent.secrets.yaml with your OpenAI API key
# Edit trading/config/kis_devlp.yaml with your KIS API keys (Korean market data)

# 5. Run analysis (no Telegram required!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### Option B: Docker (Recommended for Production)

```bash
# After the config files from step 4 above are ready:
docker compose up -d
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

**Full Setup Guide**: [docs/SETUP.md](docs/SETUP.md)

---

## What is PRISM-INSIGHT?

PRISM-INSIGHT is a **completely open-source, free** AI-powered stock analysis system for **Korean (KOSPI/KOSDAQ)** and **US (NYSE/NASDAQ)** markets.

### Core Capabilities
- **Surge Stock Detection** — Automatic detection of stocks with unusual volume/price movements
- **AI Analysis Reports** — Professional analyst-grade reports generated by specialized AI agents
- **Trading Simulation** — AI-driven buy/sell decisions with portfolio management
- **Automated Trading** — Real execution via Korea Investment & Securities API
- **Telegram Integration** — Real-time alerts and multi-language broadcasting
- **Macro Intelligence** — Market regime detection, sector rotation analysis, risk event monitoring

### AI Models
Default models in the code (each can be changed in `.env`; see [.env.example](.env.example)):

| Role | Default model |
|------|---------------|
| Report sections, strategy, summary, macro intelligence | OpenAI **GPT-6 Luna** (`REPORT_MODEL`) |
| Buy/sell decisions | OpenAI **GPT-5.6 Sol**; can be switched to GPT-6.1 Sol or GPT-6 Astra (`PRISM_BUY_CODEX_MODEL`, `PRISM_SELL_CODEX_MODEL`) |
| Telegram Q&A | OpenAI **GPT-6.1 Sol** (`TELEGRAM_ANALYSIS_MODEL`) |
| Translation (EN, JA, ZH, ES), trading journal | OpenAI **GPT-6 Luna** |
| Optional on-demand insight agent | Anthropic **Claude Sonnet 5.5** (`INSIGHT_MODEL`) |

Everything runs on an OpenAI API key or a ChatGPT Plus/Pro subscription (Codex OAuth).

---

## AI Agent System

Agents are grouped by execution path rather than by a fixed count:

| Team | Agents | Purpose |
|------|--------|---------|
| **Macro** | KR / US | Leading sectors, risks, and events on top of the rule-based market regime |
| **Stock analysis** | 6 base sections per market | Technical, trading flows, company, industry, news, market |
| **Strategy & summary** | Created at run time | Turns the base sections into an investment strategy and key summary |
| **Trading** | KR / US buy and sell | AI scenarios combined with score, portfolio, and re-entry gates |
| **Journal & memory** | Review, compression, principles | Feeds closed-trade results into the next decisions |
| **Communication & consultation** | Evaluation, optimization, translation, follow-up | Telegram summaries and user conversations |

<details>
<summary>View Agent Workflow Diagram</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="Agent Workflow" width="700">
</details>

**Details**: [Pipeline architecture (KO)](docs/PIPELINE_ARCHITECTURE_ko.md) | [AI agent system](docs/CLAUDE_AGENTS.md)

---

## Key Features

| Feature | Description |
|---------|-------------|
| **AI Analysis** | Expert-level stock analysis through a multi-agent system on OpenAI GPT-6 / GPT-5.6 models |
| **Surge Detection** | Automatic watchlist via morning/afternoon market trend analysis |
| **Telegram** | Real-time analysis distribution to channels |
| **Trading Sim** | AI-driven investment strategy simulation |
| **Auto Trading** | Execution via Korea Investment & Securities API |
| **Dashboard** | Transparent portfolio, trades, and performance tracking |
| **Self-Improving** | Trading journal feedback loop — past trigger win rates automatically inform future buy decisions ([details](docs/TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)) |
| **US Markets** | Full support for NYSE/NASDAQ analysis |
| **Macro Intelligence** | Market regime detection and sector rotation for smarter stock selection |
| **Mobile App** | iOS & Android app with smart filtering and PDF reports |

<details>
<summary>View Dashboard Screenshots</summary>
<br>
<img src="docs/images/dashboard_portfolio.png" alt="Portfolio Overview" width="700">
<br><br>
<img src="docs/images/dashboard_trades.png" alt="Trading Simulator" width="700">
<br><br>
<img src="docs/images/dashboard_performance.png" alt="AI Trading Scenario" width="700">
</details>

---

## Trading Performance — Season 2

![PRISM-INSIGHT Season 2: realized 10-slot account return vs. KOSPI/KOSDAQ and S&P 500/Nasdaq](docs/images/season2-performance-en.png)

We show two measures of the same closed trades:

- **Sum of per-trade returns** — each closed trade's return added up. Not compounded and not weighted by position size.
- **10-slot account return** — realized profit of a simulated account split into 10 equal slots (a partial-size buy counts by its share of a slot). Closed trades only, not compounded.

| | Korea (Season 2) | US |
|---|---|---|
| Period | 2025-09-30 ~ 2026-10-02 | 2026-01-28 ~ 2026-10-02 |
| Closed trades | 211 | 127 |
| Win rate | 40.3% (85 wins) | 33.1% (42 wins) |
| Average return per trade | +1.68% | +0.65% |
| Sum of per-trade returns | +355.3% | +82.8% |
| **10-slot account return** | **+35.2%** | **+8.3%** |
| Largest drop of the account curve | −9.3%p | −13.7%p |
| Same-period index | KOSPI +103.5% (3,431 → 6,982)<br>KOSDAQ +5.3% (847 → 892) | S&P 500 +10.8% (6,969 → 7,723)<br>Nasdaq +14.8% (23,685 → 27,191) |
| Best closed trades | Samsung Electro-Mechanics +86.8%<br>SK hynix +73.8%<br>SK Square +57.6% | Micron +105.7%, Micron +52.8%<br>IBM +27.0% |

**The account trailed KOSPI in this period, and we want to be upfront about it.** KOSPI roughly doubled while KOSDAQ rose about 5%, a rally led by large-cap semiconductor stocks. PRISM's own exit review (October 2026) found the biggest gap: in Korean trades that rose 30% or more within 60 trading days of entry, the median realized gain was +2% while the median peak gain was +60%. The system was selling its leaders too early. The changes in the next section target exactly that, and we will keep publishing both measures so the effect can be checked.

> Source: live dashboard data ([KR](https://analysis.stocksimulation.kr/dashboard_data.json), [US](https://analysis.stocksimulation.kr/us_dashboard_data.json)), generated 2026-10-02 (KR) and 2026-10-03 KST (US). Index changes are measured from the first point of the dashboard curve (KR 2025-09-29, US 2026-01-29). Open positions are excluded. Simulated results, not investment advice.

**[Live Dashboard](https://analysis.stocksimulation.kr/)**

---

## How PRISM Trades Now (Oct 2026)

![How PRISM trades now: screening, AI analysis, buy decision, small first buy, scenario adds, leader hold, re-entry, weekly review](docs/images/how-prism-trades-en.png)

**The direction.** PRISM follows O'Neil-style trend following. Most trades are kept small and cut quickly, and the account is meant to grow in steps from a few stocks that make big moves. Trading more raises the chance of finding those stocks, but repeated stop-losses can drain the account, so the core skill is **screening and buying the right stocks**.

| Step | What happens |
|------|--------------|
| **1. Screening** | Morning and afternoon triggers pick stocks with unusual price and volume momentum. Each trigger gets a quality weight (0.7–1.3) from PRISM's own last 180 days: how often its candidates reached +20%, and the average realized result. Weak triggers no longer get a guaranteed final spot. |
| **2. AI analysis** | Specialized agents write the report (technicals, trading flows, financials, industry, news, market), then an investment strategy. |
| **3. Buy decision** | The buy agent scores the setup from 1 to 10 against a written rubric: fundamentals (profitability, balance sheet, growth, business clarity), momentum signals, and a trend check. An entry also needs the minimum score for the current market regime, a risk/reward floor, and a stop-loss no wider than the regime limit (−5% to −7%). |
| **4. Small first buy** | The account is split into 10 equal slots. A new position starts at 30–80% of one slot, sized by the stock's volatility; high-scoring setups from the strongest triggers start a step larger. |
| **5. Scenario adds** | At entry, the AI writes 2–4 add scenarios (for example a breakout, or a pullback that recovers) and updates them every day. Code buys more only when a scenario's conditions are met, only above the average cost, never more than the previous buy, within the original risk budget, and up to one full slot. A confirmed strong move (8% above entry on 1.5× normal volume, after a first add that day) can add a second time in the same session. |
| **6. Leader hold** | A stock that closes 20% above its first buy price within 4–15 trading days, without being stretched far above its 50-day average, is treated as a leader. For up to 40 trading days it is sold only if it closes below the 50-day line or falls below the first buy price. Stocks that jump 20% in 1–3 days keep the normal profit-protection stops. |
| **7. Re-entry** | A stock that was stopped out, or skipped because of its price location, is watched for up to 60 trading days. If it reclaims its key level shortly before the close (KR 14:00, US 13:50), an AI re-check must approve the buy. Up to 3 attempts in each watch period, and at most 2 re-entry orders per market per day. |
| **8. Review loop** | A weekly leader report tracks big-winner capture, missed winners, stop-loss cost, and results by trigger. A two-week review (Oct 18, 2026) judges each October change against the same yardsticks. |

Most of these changes went live between Oct 2 and Oct 4, 2026, and Oct 6 is the first trading day under all of them, so the Season 2 numbers above mostly predate them. Design notes (Korean): [direction](docs/TRADING_CHANGE_REVIEW_HARNESS.md) · [trigger priority](docs/TRIGGER_QUALITY_PRIORITY_ko.md) · [small first buy](docs/micro-split/B3_LIVE_ko.md) · [scenario adds](docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md) · [leader hold](docs/RUNNER_HOLD_RULE_ko.md) · [re-entry](docs/REENTRY_V3_LIVE_ko.md) · [weekly report](docs/WEEKLY_RUNNER_REPORT_ko.md) · [two-week review](docs/TWO_WEEK_REVIEW_ko.md)

---

## How the Trading System Learned

The Korean-market history shows two opposite failure modes: avoiding too many
entries, then taking risk without enough state control. The releases from
v1.16.7 through v2.18 progressively moved the system from prompt-level bias
correction to deterministic regime, exit-state, and re-entry safeguards.

![PRISM-INSIGHT trading evolution from observation bias to state-based risk control](docs/images/trading-evolution-en.png)

> The figures are diagnostic. Cumulative return is the sum of trade-level return
> rates, while outcomes for unentered candidates are post-hoc observations—not a
> time-weighted portfolio return or a realizable backtest.

### October 2026: what we tested, kept, and dropped

Before a rule changes, PRISM replays it on its own past candidates and trades, and the conclusion goes into a lessons ledger so the same question is not tested twice.

**Dropped** (did not beat the current rules):
- **Pocket-pivot and volume-confirmation triggers** (2018–2026): the volume condition added nothing in either market.
- **Buying when a fallen stock reclaims its 50- and 200-day lines together**: no edge, and worse in Korea.
- **A plain O'Neil 8-week hold to the 50-day line**: worse results (Korea −43%p and US about −40%p in summed trade returns), because stocks that jumped 20% in 1–3 days gave back almost all of the gain waiting for a distant 50-day line.
- **Hourly stop checks, volatility-based (ATR) stops, and executing raised stops only at the close**: all did worse than the current stops.

**Adopted**:
- **Leader hold** only for stocks that reach +20% in 4–15 trading days without being stretched above the 50-day line (Korea +57%p across 7 affected trades; a small, in-sample result that the weekly report keeps tracking).
- **Small first buys plus AI-written add scenarios** instead of a fixed +2% / +4% ladder, with faster adds for confirmed strength.
- **Up to 3 re-entry attempts** in each watch period. The old one-time limit had cut off profitable re-entries (Korea 4 of 7, US 13 of 43).
- **Trigger priority from PRISM's own record**, and a volume-surge trigger that now requires a rising price.

Full ledger (Korean): [docs/RESEARCH_LESSONS_ko.md](docs/RESEARCH_LESSONS_ko.md)

---

## US Stock Market Module

Same AI-powered workflow for US markets:

```bash
# Run US analysis
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# With English reports
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en
```

**Data Sources**: yahoo-finance-mcp, sec-edgar-mcp (SEC filings, insider trading)

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/SETUP.md](docs/SETUP.md) | Complete installation guide |
| [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md) | AI agent system details |
| [docs/PIPELINE_ARCHITECTURE_ko.md](docs/PIPELINE_ARCHITECTURE_ko.md) | Screening → analysis → trading → feedback design (KO) |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | Surge detection algorithms |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | Trading memory system |
| [docs/TRADING_CHANGE_REVIEW_HARNESS.md](docs/TRADING_CHANGE_REVIEW_HARNESS.md) | Investment direction and the review checklist for trading changes (KO) |
| [docs/RESEARCH_LESSONS_ko.md](docs/RESEARCH_LESSONS_ko.md) | Research lessons ledger: what was tested, kept, and dropped (KO) |
| [docs/TRIGGER_QUALITY_PRIORITY_ko.md](docs/TRIGGER_QUALITY_PRIORITY_ko.md) | Trigger priority from PRISM's own record (KO) |
| [docs/micro-split/B3_LIVE_ko.md](docs/micro-split/B3_LIVE_ko.md) | Small first buy and live position building (KO) |
| [docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md](docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md) | AI add scenarios and fast adds (KO) |
| [docs/RUNNER_HOLD_RULE_ko.md](docs/RUNNER_HOLD_RULE_ko.md) | Leader hold rule (KO) |
| [docs/REENTRY_V3_LIVE_ko.md](docs/REENTRY_V3_LIVE_ko.md) | Re-entry rules (KO) |
| [docs/WEEKLY_RUNNER_REPORT_ko.md](docs/WEEKLY_RUNNER_REPORT_ko.md) | Weekly leader report (KO) |
| [docs/TWO_WEEK_REVIEW_ko.md](docs/TWO_WEEK_REVIEW_ko.md) | Two-week review of the October changes (KO) |

---

## Frontend Examples

### Dashboard
Real-time portfolio tracking and performance dashboard.

**[Live Demo](https://analysis.stocksimulation.kr/)**

```bash
cd examples/dashboard
npm install
npm run dev
# Visit http://localhost:3000
```

**Features**: Portfolio overview, trading history, performance metrics, market selector (KR/US), return comparison vs KOSPI/KOSDAQ

**Dashboard Setup Guide**: [examples/dashboard/DASHBOARD_README.md](examples/dashboard/DASHBOARD_README.md)

---

## MCP Servers

### Korean Market
- **kospi_kosdaq** — built-in Korean market data server backed by the KIS API (`cores/market_data`)
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** — Web crawling
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** — Web search
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** — Trading simulation DB

### US Market
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)** — OHLCV, financials
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)** — SEC filings, insider trading

---

## Contributing

1. Fork the project
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Create a Pull Request

### Contributors & Supporters

**Code contributors** — thank you to everyone who has improved PRISM-INSIGHT:

[@dragon1086](https://github.com/dragon1086) · [@rocky-mun](https://github.com/rocky-mun) · [@tkgo11](https://github.com/tkgo11) · [@alexander-schneider](https://github.com/alexander-schneider) · [@bonggu-kang](https://github.com/bonggu-kang) · [@willagio](https://github.com/willagio) · [@lifrary](https://github.com/lifrary) · [@cjinzy](https://github.com/cjinzy) · [@don9x2E](https://github.com/don9x2E) · [@jk5745](https://github.com/jk5745) · [@sungwoowi](https://github.com/sungwoowi)

**Gold Supporter** — [@tkgo11](https://github.com/tkgo11)

Thank you for supporting the project.

---

## License

**Dual Licensed:**

### For Individual & Open-Source Use
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Free under AGPL-3.0 for personal use, non-commercial projects, and open-source development.

### For Commercial SaaS Use
Separate commercial license required for SaaS companies.

**Contact**: dragon1086@naver.com
**Details**: [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)

Third-party open source components remain subject to their respective terms.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for notices, source links,
and license texts.

---

## Disclaimer

Analysis information is for reference only, not investment advice. All investment decisions and resulting profits/losses are the investor's responsibility.

---

## Sponsorship

### Support the Project

Monthly operating costs (as of January 2026, ~$313/month):
- OpenAI API: ~$234/month
- Anthropic API: ~$11/month
- Firecrawl + Perplexity: ~$36/month
- Server infrastructure: ~$32/month

Currently serving 450+ users for free.

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="Sponsor on GitHub">
  </a>
</div>

---

## Project Growth

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**If this project helped you, please give us a Star!**

**Contact**: [GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [Telegram](https://t.me/stock_ai_agent) | [Discussions](https://github.com/dragon1086/prism-insight/discussions)
