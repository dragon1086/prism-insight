<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300">
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
  <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.6-green.svg" alt="Anthropic">
  <img src="https://img.shields.io/badge/ChatGPT_Plus-Codex_OAuth-ff6b35.svg" alt="ChatGPT Plus">
</div>

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

---

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

## NEW: ChatGPT Plus/Pro Subscription Support

**No API key? No problem.** PRISM-INSIGHT now supports running analysis directly through your ChatGPT Plus ($20/mo) or Pro ($200/mo) subscription via the **Codex OAuth Proxy**.

```bash
# One-time login
python -m cores.chatgpt_proxy.oauth_login

# Run with your ChatGPT subscription
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning
```

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
- **Launch Promo (until Apr 23, 2026)** — Install now and get **20 free credits** (normally 10)

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

Your AI-generated PDF reports will be saved in `prism-us/pdf_reports/`.

<details>
<summary>Or use Docker (no Python setup needed)</summary>

```bash
# 1. Set your OpenAI API key
export OPENAI_API_KEY=sk-your-key-here

# 2. Start container
docker-compose -f docker-compose.quickstart.yml up -d

# 3. Run analysis
docker exec -it prism-quickstart python3 demo.py NVDA
```

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

# 3. Install perplexity-ask MCP server
cd perplexity-ask && npm install && npm run build && cd ..

# 4. Setup config
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit mcp_agent.secrets.yaml with your OpenAI API key
# Edit mcp_agent.config.yaml with KRX credentials (Kakao account)

# 5. Run analysis (no Telegram required!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### Option B: Docker (Recommended for Production)

```bash
# 1. Clone & Configure
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit config files with your API keys

# 2. Build & Run
docker-compose up -d

# 3. Run analysis manually (optional)
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

**Full Setup Guide**: [docs/SETUP.md](docs/SETUP.md)

---

## What is PRISM-INSIGHT?

PRISM-INSIGHT is a **completely open-source, free** AI-powered stock analysis system for **Korean (KOSPI/KOSDAQ)** and **US (NYSE/NASDAQ)** markets.

### Core Capabilities
- **Surge Stock Detection** — Automatic detection of stocks with unusual volume/price movements
- **AI Analysis Reports** — Professional analyst-grade reports generated by 13 specialized AI agents
- **Trading Simulation** — AI-driven buy/sell decisions with portfolio management
- **Automated Trading** — Real execution via Korea Investment & Securities API
- **Telegram Integration** — Real-time alerts and multi-language broadcasting
- **Macro Intelligence** — Market regime detection, sector rotation analysis, risk event monitoring

### AI Models
- **Analysis & Trading**: OpenAI GPT-5 / GPT-5.4-mini (via API or ChatGPT Plus subscription)
- **Report Generation**: Anthropic Claude Sonnet 4.6
- **Translation**: OpenAI GPT-5 (EN, JA, ZH, ES support)

---

## AI Agent System

13+ specialized agents collaborate in teams:

| Team | Agents | Purpose |
|------|--------|---------|
| **Macro** | 1 agent | Market regime, sector rotation, risk events |
| **Analysis** | 6 agents | Technical, Financial, Industry, News, Market analysis |
| **Strategy** | 1 agent | Investment strategy synthesis |
| **Communication** | 3 agents | Summary, Quality evaluation, Translation |
| **Trading** | 3 agents | Buy/Sell decisions, Journal |
| **Consultation** | 2 agents | User interaction via Telegram |

<details>
<summary>View Agent Workflow Diagram</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="Agent Workflow" width="700">
</details>

**Detailed Agent Documentation**: [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md)

---

## Key Features

| Feature | Description |
|---------|-------------|
| **AI Analysis** | Expert-level stock analysis through GPT-5 multi-agent system |
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

## Trading Performance

### KR Market — Season 2

| Metric | Value |
|--------|-------|
| Period | 2025.09.30 ~ 2026.03.24 |
| Total Trades | 86 |
| Win Rate | 45.35% |
| Avg Return per Trade | +2.84% |
| **Cumulative Return** | **+244.63%** |
| Current Holdings | 5 stocks |

### US Market (Beta)

| Metric | Value |
|--------|-------|
| Period | 2026.01.28 ~ 2026.03.21 |
| Total Trades | 13 |
| Current Holdings | 6 stocks |

**[Live Dashboard](https://analysis.stocksimulation.kr/)**

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
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | Surge detection algorithms |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | Trading memory system |
| [docs/chatgpt-oauth/setup.md](docs/chatgpt-oauth/setup.md) | ChatGPT OAuth proxy setup |

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
- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)** — KRX stock data
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

---

## License

**Dual Licensed:**

### For Individual & Open-Source Use
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Free under AGPL-3.0 for personal use, non-commercial projects, and open-source development.

### For Commercial SaaS Use
Separate commercial license required for SaaS companies.

**Contact**: dragon1086@naver.com
**Details**: [LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md)

---

## Disclaimer

Analysis information is for reference only, not investment advice. All investment decisions and resulting profits/losses are the investor's responsibility.

---

## Sponsorship

### Support the Project

Monthly operating costs (~$310/month):
- OpenAI API: ~$235/month
- Anthropic API: ~$11/month
- Firecrawl + Perplexity: ~$35/month
- Server infrastructure: ~$30/month

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
