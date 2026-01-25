<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300">
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
  <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.5-green.svg" alt="Anthropic">
</div>

# 🔍 PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> 📖 [한국어 문서](README_ko.md)

<div align="center">

---

### 🏆 Platinum Sponsor

<a href="https://wrks.ai/en">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/en)**

**AI3**, the creators of **WrksAI** – the AI assistant for professionals,<br>
proudly sponsors **PRISM-INSIGHT** – the AI assistant for investors.

👉 [Learn more about WrksAI](https://wrks.ai/en)

---

</div>

---

### 📊 AI-Powered Korean Stock Market Analysis & Trading System

* **[Official Telegram Channel](https://t.me/prism_insight_global_en)**
  Features include surge-stock detection, stock analysis report downloads, trading simulations, and automated trading reports.

    * **➡️ Global (English) Channel:**
      [https://t.me/prism_insight_global_en](https://t.me/prism_insight_global_en)
    * **➡️ Korean Channel:**
      [https://t.me/stock_ai_agent](https://t.me/stock_ai_agent)

* **[Official Dashboard](https://analysis.stocksimulation.kr/)**:
  Real-time performance dashboard for PRISM-INSIGHT live trading & simulations
  (Includes AI portfolio analysis, trading history, and watchlists)

* **Community**:
  Via **[GitHub Discussions](https://github.com/dragon1086/prism-insight/discussions)** or the Telegram discussion group (@prism_insight_discuss)

---

## 📖 Project Overview

PRISM-INSIGHT is a **completely open-source free project** specializing in **Korean stock market (KOSPI/KOSDAQ) analysis** through **comprehensive AI analysis agents**. It automatically detects surging Korean stocks daily through a Telegram channel, generates expert-level analyst reports, and performs trading simulations and automated trading.

**✨ All features are provided 100% free!**

## 🇺🇸 US Stock Market Module (NEW)

PRISM-INSIGHT now supports **US stock market (NYSE, NASDAQ)** analysis with the same AI-powered workflow as the Korean market version.

### Key Features
- **Same AI Agent Architecture**: 13 specialized agents for comprehensive US stock analysis
- **MCP Server Integration**: yahoo-finance-mcp (OHLCV, financials), sec-edgar-mcp (SEC filings, insider trading)
- **KIS Overseas Trading API**: Automated trading via Korea Investment & Securities overseas stock API
- **Multi-language Support**: Reports in English, Korean, Japanese, Chinese

### Directory Structure
```
prism-us/
├── us_stock_analysis_orchestrator.py  # Main orchestrator
├── us_trigger_batch.py                # Surge stock detection
├── us_stock_tracking_agent.py         # Trading simulation
├── us_telegram_summary_agent.py       # Telegram summary
├── cores/
│   ├── us_data_client.py              # Unified data client (yfinance + finnhub)
│   ├── us_surge_detector.py           # Surge detection module
│   ├── us_analysis.py                 # Core analysis module
│   └── agents/                        # US-specific agents
└── trading/config/                    # KIS overseas trading config
```

### Quick Start
```bash
# Run US stock analysis pipeline (Korean default - same as Korean stock version)
python prism-us/us_stock_analysis_orchestrator.py --mode morning

# Run with English reports and messages
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en

# Test without Telegram
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

## 📈 Trading Simulator and Real Account Performance as of '26.01.25
### ⭐ Season 1 (Ended '25.09.28. No real account trading)
**Simulator Performance**
- Start Date: 2025.03.15
- Total Trades: 51
- Profitable Trades: 23
- Loss Trades: 28
- Win Rate: 45.1%
- **Cumulative Return: 408.60%**
- **[Trading Performance Summary Dashboard](https://claude.ai/public/artifacts/d546cc2e-9d2c-4787-8415-86930494e198)**

### ⭐⭐ Season 2 (In Progress)
**Simulator Performance**
- Start Date: 2025.09.29
- Total Trades: 50
- Profitable Trades: 21
- Loss Trades: 29
- Win Rate: 42.00%
- **Total Cumulative Return from Sold Stocks: 127.34%**
- **Realized Portfolio Return: 12.73%** (managed across 10 slots, 127.34% ÷ 10)
- Market Benchmark (from Season 2 start): KOSPI +45.43%, KOSDAQ +17.39%
- **[Trading Performance Summary Dashboard](https://analysis.stocksimulation.kr/)**

**Real Account Performance**
- Start Date: 2025.09.29
- Initial Capital: ₩9,969,801
- Current Total Assets (Valuation + Cash): ₩10,816,740
- **Return: +8.50%**

## 🤖 AI Agent System Architecture (Core Feature)

PRISM-INSIGHT is a **multi-agent system where 13 specialized AI agents collaborate**. Each agent specializes in a specific analysis domain and works organically together to deliver expert-level comprehensive analysis and trading.

### 📊 Analysis Team (6 Agents) - GPT-5 Based

#### 1. Technical Analyst
<img src="docs/images/aiagent/technical_analyst.jpeg" alt="Technical Analyst" width="300"/>

- **Role**: Stock price and trading volume technical analysis expert
- **Analysis Items**:
  - Price trends, moving averages, support/resistance levels
  - Chart patterns and technical indicators (RSI, MACD, Bollinger Bands)
  - Technical perspective

#### 2. Trading Flow Analyst
<img src="docs/images/aiagent/tranding_flow_analyst.jpeg" alt="Trading Flow Analyst" width="300"/>

- **Role**: Investor trading trend analysis expert
- **Analysis Items**:
  - Trading patterns of institutional/foreign/individual investors
  - Investor group trends through volume analysis

#### 3. Financial Analyst
<img src="docs/images/aiagent/financial_analyst.jpeg" alt="Financial Analyst" width="300"/>

- **Role**: Corporate finance and valuation analysis expert
- **Analysis Items**:
  - Financial statement analysis (revenue, operating profit, net income)
  - Valuation assessment (PER, PBR, ROE, etc.)
  - Target price and securities firm consensus

#### 4. Industry Analyst
<img src="docs/images/aiagent/industry_analyst.jpeg" alt="Industry Analyst" width="300"/>

- **Role**: Corporate business structure and competitiveness analysis expert
- **Analysis Items**:
  - Business portfolio and market share
  - Strengths/weaknesses compared to competitors
  - R&D investment and growth drivers

#### 5. Information Analyst
<img src="docs/images/aiagent/information_analyst.jpeg" alt="Information Analyst" width="300"/>

- **Role**: News and issue trend analysis expert
- **Analysis Items**:
  - Identifying causes of same-day stock price fluctuations
  - Latest news and disclosure analysis
  - Industry trends and political/economic issues

#### 6. Market Analyst
<img src="docs/images/aiagent/market_analyst.jpeg" alt="Market Analyst" width="300"/>

- **Role**: Overall market and macroeconomic analysis expert
- **Analysis Items**:
  - KOSPI/KOSDAQ index analysis
  - Macroeconomic indicators (interest rates, exchange rates, prices)
  - Correlation between global economy and Korean market

---

### 💡 Strategy Team (1 Agent) - GPT-5 Based

#### 7. Investment Strategist
<img src="docs/images/aiagent/investment_strategist.jpeg" alt="Investment Strategist" width="300"/>

- **Role**: Integrates all analysis results to establish final investment strategy
- **Provides**:
  - Customized strategies for short/medium/long-term investors
  - Risk level and trading timing suggestions
  - Comprehensive opinion from portfolio perspective

---

### 💬 Communication Team (3 Agents) - GPT-5 Based

#### 8-1. Summary Specialist
<img src="docs/images/aiagent/summary_specialist.jpeg" alt="Summary Specialist" width="300"/>

- **Role**: Converts detailed reports into core summaries for investors
- **Features**:
  - Generates concise Telegram messages within 400 characters
  - Extracts key information and investment points
  - Telegram-optimized formatting

#### 8-2. Quality Inspector
<img src="docs/images/aiagent/quality_inspector.jpeg" alt="Quality Inspector" width="300"/>

- **Role**: Evaluates quality of generated messages and suggests improvements
- **Features**:
  - Verifies accuracy, clarity, and format compliance
  - Detects hallucinations and identifies errors
  - Collaborates with Summary Specialist for iterative improvement to EXCELLENT rating

#### 8-3. Translation Specialist
<img src="docs/images/aiagent/translator_specialist.png" alt="Translation Specialist" width="300"/>

- **Role**: Translates analysis reports and messages to multiple languages
- **Features**:
  - Supports multi-language broadcasting (English, Japanese, Chinese, etc.)
  - Preserves technical terminology and market context
  - Enables parallel transmission to language-specific Telegram channels

---

### 📈 Trading Simulation Team (3 Agents) - GPT-5 Based

#### 9-1. Buy Specialist
<img src="docs/images/aiagent/buy_specialist.jpeg" alt="Buy Specialist" width="300"/>

- **Role**: Buy decision-making and entry management based on AI reports
- **Features**:
  - Evaluates buy score based on valuation and momentum (1-10 points)
  - Manages portfolio with maximum 10 slots
  - Industry diversification and risk management
  - Dynamic target/stop-loss setting
  - Detailed trading scenario creation

#### 9-2. Sell Specialist
<img src="docs/images/aiagent/sell_specialist.jpeg" alt="Sell Specialist" width="300"/>

- **Role**: Monitors holdings based on trading scenarios and determines sell timing
- **Features**:
  - Real-time monitoring of stop-loss/profit-taking scenarios
  - Technical trend and market environment analysis
  - Portfolio optimization adjustment suggestions
  - Prudent decisions considering 100% exit characteristics

#### 9-3. Trading Journal Agent - Optional

- **Role**: Retrospective analysis of completed trades and long-term memory accumulation
- **Features**:
  - Buy/sell context comparison and lesson extraction
  - Hierarchical memory compression (detailed → summary → intuition)
  - Buy score adjustment based on past experience
  - Disabled by default (enable with `ENABLE_TRADING_JOURNAL=true` in `.env`)
  - 📖 Details: [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md)

---

### 💬 User Consultation Team (2 Agents) - Claude Sonnet 4.5 Based

#### 10-1. Portfolio Consultant
<img src="docs/images/aiagent/portfolio_consultant.jpeg" alt="Portfolio Consultant" width="300"/>

- **Role**: User portfolio evaluation and customized investment advice
- **Features**:
  - Analysis based on user's average purchase price and holding period
  - Comprehensive evaluation using latest market data and news
  - Adaptive responses to user request styles (friendly/expert/direct, etc.)
  - Customized advice for profit/loss positions

#### 10-2. Dialogue Manager
<img src="docs/images/aiagent/dialogue_manager.jpeg" alt="Dialogue Manager" width="300"/>

- **Role**: Maintains conversation context and handles follow-up questions
- **Features**:
  - Remembers and references previous conversation context
  - Consistent answers to additional questions
  - Additional data lookup when necessary
  - Maintains natural conversation flow

---

## 🔄 Agent Collaboration Workflow

  <img src="docs/images/aiagent/agent_workflow2.png" alt="Agent Workflow" width="700">

## 🎯 Key Features

- **🤖 AI Comprehensive Analysis (Core)**: Expert-level stock analysis through GPT-5 based multi-agent system
  [![Analysis Report Demo](https://img.youtube.com/vi/4WNtaaZug74/maxresdefault.jpg)](https://youtu.be/4WNtaaZug74)

- **📊 Automatic Surge Stock Detection**: Watchlist selection through hourly (morning/afternoon) market trend analysis
  <img src="docs/images/trigger-en.png" alt="Surge Stock Detection" width="500">

- **📱 Automatic Telegram Transmission**: Real-time transmission of analysis results to Telegram channel
  <img src="docs/images/summary-en.png" alt="Summary Transmission" width="500">

- **📈 Trading Simulation**: Investment strategy simulation using GPT-5 based generated reports
  <img src="docs/images/simulation1-en.png" alt="Simulation 1" width="500">
  <img src="docs/images/simulation2-en.png" alt="Simulation 2" width="500">
  <img src="docs/images/season1_dashboard.png" alt="Simulation Performance" width="500">

- **💱 Automated Trading**: Automatic trading according to trading simulation results through Korea Investment & Securities API

- **🎨 Realtime Dashboard**: We transparently disclose all information on the AI-traded portfolio, its performance relative to the market, the AI's trading rationale, full trading history, watchlist, and system maintenance costs.
  <img src="docs/images/dashboard1-en.png" alt="Dashboard 1" width="500">
  <img src="docs/images/dashboard2-en.png" alt="Dashboard 2" width="500">
  <img src="docs/images/dashboard3-en.png" alt="Dashboard 3" width="500">
  <img src="docs/images/dashboard4-en.png" alt="Dashboard 4" width="500">
  <img src="docs/images/dashboard5-en.png" alt="Dashboard 5" width="500">
  <img src="docs/images/dashboard6-en.png" alt="Dashboard 6" width="500">
  <img src="docs/images/dashboard7-en.png" alt="Dashboard 7" width="500">

- **🎬 YouTube Event Fund Crawler** (NEW): Contrarian investment strategy based on '전인구경제연구소' YouTube channel analysis
  - Automatic monitoring of new videos via RSS feed
  - Audio extraction and transcription using OpenAI Whisper API
  - AI-powered content analysis for market sentiment detection
  - Contrarian investment recommendations (inverse/leveraged ETF suggestions)
  - 📖 See [YOUTUBE_EVENT_FUND_CRAWLER.md](events/YOUTUBE_EVENT_FUND_CRAWLER.md) for details

## 🧠 AI Model Usage

- **Core Analysis & Trading**: OpenAI GPT-5 (Comprehensive stock analysis and trading simulation)
- **Telegram Conversation**: Anthropic Claude Sonnet 4.5 (Bot interaction)
- **Translation**: OpenAI GPT-5 (Multilingual broadcasting on a Telegram channel)

## 💡 MCP Servers Used

### Korean Market (KR)
- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)**: MCP server for KRX (Korea Exchange) stock data in report generation
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)**: Web crawling specialized MCP server for report generation
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol/tree/main)**: Web search specialized MCP server for report generation
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived/tree/HEAD/src/sqlite)**: MCP server specialized in internal DB storage for trading simulation records
- **[time](https://github.com/modelcontextprotocol/servers/tree/main/src/time)**: MCP server for current time retrieval

### US Market (NEW)
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)**: OHLCV, company info, financials, institutional holders (PyPI, uvx remote execution)
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)**: SEC filings, XBRL financials, insider trading data (PyPI, uvx remote execution)

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- OpenAI API Key (GPT-5, GPT-5)
- Anthropic API Key (Claude-Sonnet-4.5)
- Telegram Bot Token and Channel ID
- Playwright (for PDF conversion)
- Korea Investment & Securities API app key and secret key

### Installation

1. **Clone Repository**
```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
```

2. **Install Dependencies**
```bash
pip install -r requirements.txt
```

3. **Prepare Configuration Files**
Copy the example files to create actual configuration files:
```bash
cp .env.example .env
cp ./examples/streamlit/config.py.example ./examples/streamlit/config.py
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
```

4. **Edit Configuration Files**
Edit the copied configuration files to enter necessary API keys and settings.

**Important:** Set Kakao account credentials for KRX Data Marketplace authentication:
```bash
# .env
KAKAO_ID=your_kakao_email@example.com
KAKAO_PW=your_kakao_password
```

```yaml
# mcp_agent.config.yaml
kospi_kosdaq:
  command: "python3"
  args: ["-m", "kospi_kosdaq_stock_server"]
  env:
    KAKAO_ID: "your_kakao_id"
    KAKAO_PW: "your_kakao_password"
```

> **💡 2-Step Verification Users:** If Kakao 2-step verification is enabled, you'll need to confirm in the app for each analysis. To disable: Kakao App > Settings > Kakao Account > Account Security > 2-Step Verification 'Off'.

5. **Install Playwright** (for PDF conversion)

The system will **automatically install** Playwright browser on first run. For manual installation:

```bash
# Install Playwright package (included in requirements.txt)
pip install playwright

# Download Chromium browser
python3 -m playwright install chromium
```

**Platform-specific installation:**

```bash
# macOS
pip3 install playwright
python3 -m playwright install chromium

# Ubuntu/Debian
pip install playwright
python3 -m playwright install --with-deps chromium

# Rocky Linux 8 / CentOS / RHEL
pip3 install playwright
playwright install chromium

# If --with-deps doesn't work, install dependencies manually:
dnf install -y epel-release
dnf install -y nss nspr atk at-spi2-atk cups-libs libdrm \
    libxkbcommon libXcomposite libXdamage libXfixes \
    libXrandr mesa-libgbm alsa-lib pango cairo

# Or use the installation script
cd utils
chmod +x setup_playwright.sh
./setup_playwright.sh
```

**📖 For detailed installation instructions, see:** [utils/PLAYWRIGHT_SETUP.md](utils/PLAYWRIGHT_SETUP.md)

6. **Install perplexity-ask MCP Server**
```bash
cd perplexity-ask
npm install
```

7. **Install Korean Fonts** (Linux environment)

Korean fonts are required for Korean text display in charts on Linux.

```bash
# Rocky Linux 8 / CentOS / RHEL
sudo dnf install google-nanum-fonts

# Ubuntu 22.04+ / Debian
Run ./cores/ubuntu_font_installer.py

# Refresh font cache
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"

Note: macOS and Windows have default Korean font support, no installation needed
```

8. **Auto-run Setup (Crontab)**

Set up crontab to run automatically:

```bash
# Simple setup (recommended)
chmod +x utils/setup_crontab_simple.sh
utils/setup_crontab_simple.sh

# Or advanced setup
chmod +x utils/setup_crontab.sh
utils/setup_crontab.sh
```

See [CRONTAB_SETUP.md](utils/CRONTAB_SETUP.md) for details.

### Required Configuration Files

The following configuration files must be set up to run the project:

#### 🔧 Core Settings (Required)
- **`mcp_agent.config.yaml`**: MCP agent configuration
- **`mcp_agent.secrets.yaml`**: MCP agent secret information (API keys, etc.)

#### 📱 Telegram Settings (Optional)
- **`.env`**: Environment variables including Telegram channel ID, bot token, etc.
  - Use `--no-telegram` option to run without Telegram
  - All analysis features work normally without Telegram

#### 🌐 Web Interface Settings (Optional)
- **`./examples/streamlit/config.py`**: Report generation web settings

💡 **Tip**: Use `--no-telegram` option to run without `.env` file!

## 📋 Usage

### Basic Execution

Run the entire pipeline to automate from surge stock analysis to Telegram transmission:

```bash
# Run both morning + afternoon (Telegram enabled)
python stock_analysis_orchestrator.py --mode both

# Morning only
python stock_analysis_orchestrator.py --mode morning

# Afternoon only
python stock_analysis_orchestrator.py --mode afternoon

# Local test without Telegram (no Telegram setup needed)
python stock_analysis_orchestrator.py --mode morning --no-telegram

# Generate English reports (default: Korean)
python stock_analysis_orchestrator.py --mode morning --language en

# Broadcast to multiple language channels (requires setup in .env)
python stock_analysis_orchestrator.py --mode morning --broadcast-languages en,ja,zh
```

#### 💡 Telegram Option (`--no-telegram`)

You can run the system without Telegram setup:

**Usage Scenarios:**
- 🧪 **Local Development/Testing**: Quickly test core features without Telegram setup
- 🚀 **Performance Optimization**: Skip message generation and transmission process
- 🔧 **Debugging**: Focus only on analysis and report generation features

**Execution Effects:**
- ✅ Surge stock detection → Report generation → PDF conversion → Tracking system (all working normally)
- ❌ Telegram alerts, message generation, message transmission (skipped)
- 💰 AI summary generation cost savings

**Required Environment Variables (when using Telegram):**
```bash
# .env file
TELEGRAM_CHANNEL_ID="-1001234567890"  # Main channel (Korean by default)
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"

# Multi-language broadcasting (optional)
# Use with --broadcast-languages argument (e.g., --broadcast-languages en,ja,zh)
# TELEGRAM_CHANNEL_ID_EN="-1001234567891"  # English channel
# TELEGRAM_CHANNEL_ID_JA="-1001234567892"  # Japanese channel
# TELEGRAM_CHANNEL_ID_ZH="-1001234567893"  # Chinese channel
```

### Individual Module Execution

**1. Run Surge Stock Detection Only**
```bash
python trigger_batch.py morning INFO --output trigger_results.json
```

**2. Generate AI Analysis Report for Specific Stock (Core Feature)**
```bash
python cores/main.py
# Or use analyze_stock function directly
```

**3. PDF Conversion**
```bash
python pdf_converter.py input.md output.pdf
```

**4. Generate and Send Telegram Messages**
```bash
python telegram_summary_agent.py
python telegram_bot_agent.py
```

## 📁 Project Structure

```
prism-insight/
├── 📂 cores/                     # 🤖 Core AI Analysis Engine (Korean Market)
│   ├── 📂 agents/               # AI Agent Modules
│   │   ├── company_info_agents.py        # Company Information Analysis Agent
│   │   ├── news_strategy_agents.py       # News and Investment Strategy Agent
│   │   ├── stock_price_agents.py         # Stock Price and Volume Analysis Agent
│   │   ├── telegram_quality_inspector.py # Quality Inspector Agent
│   │   ├── telegram_summary_agent.py     # Summary Specialist Agent
│   │   └── telegram_translator_agent.py  # Translation Specialist Agent
│   ├── analysis.py              # Comprehensive Stock Analysis (Core)
│   ├── main.py                  # Main Analysis Execution
│   ├── report_generation.py     # Report Generation
│   ├── stock_chart.py           # Chart Generation
│   └── utils.py                 # Utility Functions
├── 📂 prism-us/                  # 🇺🇸 US Stock Market Module (NEW)
│   ├── us_stock_analysis_orchestrator.py  # US Main Orchestrator
│   ├── us_trigger_batch.py                # US Surge Stock Detection
│   ├── us_stock_tracking_agent.py         # US Trading Simulation
│   ├── us_telegram_summary_agent.py       # US Telegram Summary
│   ├── check_market_day.py                # US Market Holiday Checker
│   ├── 📂 cores/                          # US Core Analysis
│   │   ├── us_data_client.py              # Unified Data Client (yfinance + finnhub)
│   │   ├── us_surge_detector.py           # Surge Detection Module
│   │   ├── us_analysis.py                 # Core Analysis Module
│   │   └── 📂 agents/                     # US-specific Agents
│   └── 📂 tracking/                       # US Database Schema
├── 📂 examples/streamlit/        # Web Interface
├── 📂 trading/                   # 💱 Automated Trading System (Korea Investment & Securities API)
│   ├── kis_auth.py              # KIS API Authentication and Token Management
│   ├── domestic_stock_trading.py # Domestic Stock Trading Core Module
│   ├── portfolio_telegram_reporter.py # Portfolio Telegram Reporter
│   ├── 📂 config/               # Configuration File Directory
│   │   ├── kis_devlp.yaml       # KIS API Configuration (app key, account number, etc.)
│   │   └── kis_devlp.yaml.example # Configuration File Example
│   └── 📂 samples/              # API Sample Code
├── 📂 utils/                     # Utility Scripts
├── 📂 tests/                     # Test Code
├── stock_analysis_orchestrator.py # 🎯 Main Orchestrator (Korean Market)
├── telegram_config.py           # Telegram Configuration Management Class
├── trigger_batch.py             # Surge Stock Detection Batch
├── telegram_bot_agent.py        # Telegram Bot (Claude Based)
├── stock_tracking_agent.py      # Trading Simulation (GPT-5)
├── stock_tracking_enhanced_agent.py # Enhanced Trading Simulation
├── compress_trading_memory.py   # Trading Memory Compression & Cleanup
├── performance_tracker_batch.py # Daily Performance Tracking
├── pdf_converter.py             # PDF Conversion
├── requirements.txt             # Dependency List
├── .env.example                 # Environment Variable Example
├── mcp_agent.config.yaml.example    # MCP Agent Configuration Example
├── mcp_agent.secrets.yaml.example   # MCP Agent Secret Example
```

## 📈 Analysis Report Composition

The comprehensive analyst report generated by AI agents consists of the following sections:

1. **📊 Key Investment Points** - Summary and main points
2. **📈 Technical Analysis**
   - Stock price and trading volume analysis
   - Investor trading trend analysis
3. **🏢 Fundamental Analysis**
   - Company status analysis
   - Company overview analysis
4. **📰 News Trend Analysis** - Recent major news and issues
5. **🌐 Market Analysis** - KOSPI/KOSDAQ index and macro-environment analysis
6. **💡 Investment Strategy and Opinion** - Strategies for different investor types

## 🔧 Customization

### Modify Surge Stock Detection Criteria
In `trigger_batch.py`, you can modify:
- Trading volume increase threshold
- Stock price increase criteria
- Market capitalization filtering conditions

> **📖 Hybrid Selection Algorithm:** The trigger now selects stocks that are more compatible with buy/sell agent criteria. See [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) for details.

### Modify AI Prompts
You can customize analysis instructions in each agent file in the `cores/agents/` directory.

### Change Chart Style
You can modify chart colors, styles, and indicators in `cores/stock_chart.py`.

## 🤝 Contributing

1. Fork the project
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Create a Pull Request

## 📄 License

**PRISM-INSIGHT is dual-licensed:**

### For Individual & Open-Source Use
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** for:
- Individual personal use
- Non-commercial projects
- Open-source development
- Academic research

The AGPL-3.0 license requires:
- Source code disclosure for any modifications
- Source code availability for network/service use
- Same license for derived works

📖 **[Full AGPL-3.0 License Text](LICENSE)**

### For Commercial SaaS Use
For SaaS companies and commercial use, a separate **Commercial License** is required.

**Commercial License Benefits:**
- No obligation to disclose source code
- Freedom to modify without publishing changes
- Proprietary integration allowed
- Dedicated technical support
- Priority feature development

**Pricing:**
- Startups (<50 employees): $500/month
- SME (50-500 employees): $2,000/month
- Enterprise (>500 employees): Custom pricing
- **Exclusive SaaS License:** Flexible pricing based on discussion (1-year commitment)

📧 **Contact for Commercial Licensing:** dragon1086@naver.com  
📄 **[Commercial License Details](LICENSE-COMMERCIAL.md)**


### Why Dual License?

**For the Community:**
- Free for personal learning and development
- Contribute to open-source ecosystem
- Full transparency with AGPL-3.0

**For Sustainability:**
- Commercial licenses fund ongoing development
- Maintain server infrastructure ($174/month operating costs)
- Add new features and AI models
- Provide quality support

**For Businesses:**
- Legal clarity for commercial use
- No source code disclosure requirements
- Professional support and SLA
- Customization opportunities

---

**Questions about licensing?**
- 📧 Email: dragon1086@naver.com
- 💬 Telegram: [@stock_ai_agent](https://t.me/stock_ai_agent)
- 🐛 GitHub Issues: [Report here](https://github.com/dragon1086/prism-insight/issues)

## ⚠️ Disclaimer

The analysis information provided by this system is for investment reference purposes only and is not intended as investment advice. All investment decisions and resulting profits/losses are the responsibility of the investor.

## 📞 Contact

For project inquiries or bug reports, please submit through [GitHub Issues](https://github.com/dragon1086/prism-insight/issues).

## 💝 Sponsorship for Project Sustainability

### 💙 To Individual Sponsors

Even small support gives great strength! Support the project with the cost of a cup of coffee.

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="Sponsor on GitHub">
  </a>
</div>

### 💰 Transparent Operations

Monthly costs of approximately ₩290,000 for API and server expenses (as of November '25):
- OpenAI API (GPT-5, GPT-5): ~₩170,000/month
- Anthropic API (Claude Sonnet 4.5): ~₩30,000/month
- Firecrawl API (MCP Server): ~₩30,000/month
- Perplexity API (MCP Server): ~₩15,000/month
- Server and Infrastructure: ~₩45,000/month

Currently used by 450+ people for free.

### ✨ Current Sponsorship Status

Thank you so much! PRISM-INSIGHT continues to operate with your support.

#### 💙 Individual Sponsors
<!-- sponsors -->
**Core Supporter**
- [@tehryung-ray](https://github.com/tehryung-ray) 💙

**Supporter**
- [@ddkerty](https://github.com/ddkerty) 💙
- [@jk5745](https://github.com/jk5745) 💙
<!-- sponsors -->

---

**Important:** All features are provided free regardless of sponsorship.
Sponsorship is just support for service continuity.

---

## ⭐ Project Growth

Achieved **250+ Stars in just 10 weeks** since mid-August '25 launch!

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**⭐ If this project helped you, please give us a Star!**
