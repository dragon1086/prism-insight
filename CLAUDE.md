# CLAUDE.md - AI Assistant Guide for PRISM-INSIGHT

> **Last Updated**: 2025-11-14
> **Version**: 1.0
> **Purpose**: Comprehensive guide for AI assistants working on the PRISM-INSIGHT codebase

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Codebase Structure](#3-codebase-structure)
4. [Development Workflows](#4-development-workflows)
5. [AI Agent System](#5-ai-agent-system)
6. [Key Conventions](#6-key-conventions)
7. [Configuration Management](#7-configuration-management)
8. [Testing Guidelines](#8-testing-guidelines)
9. [Common Tasks](#9-common-tasks)
10. [Important Constraints](#10-important-constraints)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Project Overview

### What is PRISM-INSIGHT?

PRISM-INSIGHT is a **production-grade AI-powered Korean stock market analysis and trading system** featuring:

- **13 specialized AI agents** collaborating for comprehensive stock analysis
- **Automated surge stock detection** with configurable criteria
- **Professional analyst-grade reports** in multiple languages (Korean, English, Japanese, Chinese)
- **Trading simulation** with real-world performance tracking
- **Automated trading** via Korea Investment & Securities (KIS) API
- **Telegram integration** for real-time distribution and user interaction

### Technology Stack

```yaml
Language: Python 3.10+
AI Framework: mcp-agent (Multi-Agent Orchestration)
LLM Models:
  - OpenAI GPT-4.1 (Analysis agents)
  - OpenAI GPT-5 (Trading agents)
  - Anthropic Claude Sonnet 4.5 (Telegram bot)
Data Sources:
  - pykrx: Korean stock market data
  - MCP Servers: kospi_kosdaq, firecrawl, perplexity
Communication: python-telegram-bot (v20+)
Trading API: Korea Investment & Securities
Database: SQLite with aiosqlite (async)
PDF Generation: Playwright (Chromium-based)
Charts: matplotlib, seaborn, mplfinance
```

### Project Scale

- **~56 Python files** | **~8,400+ lines of code**
- **13+ specialized AI agents** with distinct roles
- **Multiple entry points** for different workflows
- **Full async/await** throughout the codebase
- **Multi-language support** (ko, en, ja, zh, es, fr, de)

---

## 2. Architecture

### High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    PRISM-INSIGHT SYSTEM                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌─────────────────┐   ┌─────────────┐ │
│  │   Trigger    │───▶│   Orchestrator  │──▶│   Analysis  │ │
│  │    Batch     │    │     Pipeline    │   │    Engine   │ │
│  └──────────────┘    └─────────────────┘   └─────────────┘ │
│         │                    │                      │        │
│         ▼                    ▼                      ▼        │
│  ┌──────────────┐    ┌─────────────────┐   ┌─────────────┐ │
│  │   Watchlist  │    │  13 AI Agents   │   │   Reports   │ │
│  │  Detection   │    │   Collaborate   │   │  (MD/PDF)   │ │
│  └──────────────┘    └─────────────────┘   └─────────────┘ │
│                              │                      │        │
│                              ▼                      ▼        │
│                      ┌─────────────────┐   ┌─────────────┐ │
│                      │    Trading      │   │  Telegram   │ │
│                      │   Simulation    │   │  Messages   │ │
│                      └─────────────────┘   └─────────────┘ │
│                              │                      │        │
│                              ▼                      ▼        │
│                      ┌─────────────────┐   ┌─────────────┐ │
│                      │   KIS Trading   │   │  Multi-lang │ │
│                      │      API        │   │  Channels   │ │
│                      └─────────────────┘   └─────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Main Execution Flow

```
Daily Trigger (09:10 Morning / 15:30 Afternoon)
    ↓
trigger_batch.py (Surge Stock Detection)
    ├─ Volume surge detection
    ├─ Gap detection (price jumps)
    └─ Sector performance analysis
    ↓
stock_analysis_orchestrator.py (Main Pipeline)
    ├─ 1. Send Prism Signal Alert (Telegram)
    ├─ 2. Generate AI Analysis Reports
    │   ├─ Sequential agent execution (rate limit friendly)
    │   └─ 13 specialized agents collaborate
    ├─ 3. Convert Markdown to PDF
    │   └─ Playwright (Chromium-based)
    ├─ 4. Generate Telegram Summaries
    │   ├─ Summary Optimizer Agent
    │   └─ Quality Evaluator Agent (iterative improvement)
    ├─ 5. Distribute to Telegram Channels
    │   └─ Multi-language broadcasting support
    └─ 6. Trading Simulation & Execution
        ├─ Buy/Sell Decision Agents
        ├─ Portfolio Management (max 10 slots)
        └─ KIS API Execution (demo or real mode)
```

---

## 3. Codebase Structure

### Directory Layout

```
prism-insight/
├── 📂 cores/                           # 🤖 Core AI Analysis Engine
│   ├── 📂 agents/                     # AI Agent Definitions
│   │   ├── company_info_agents.py     # Financial & Business Analysis
│   │   ├── stock_price_agents.py      # Technical & Flow Analysis
│   │   ├── news_strategy_agents.py    # News & Investment Strategy
│   │   ├── market_index_agents.py     # Market & Macro Analysis
│   │   ├── trading_agents.py          # Trading Scenario & Sell Decisions
│   │   ├── telegram_summary_optimizer_agent.py   # Summary Generation
│   │   ├── telegram_summary_evaluator_agent.py   # Quality Inspection
│   │   └── telegram_translator_agent.py          # Multi-language Support
│   ├── analysis.py                    # Core analysis orchestration
│   ├── report_generation.py           # Report templating & generation
│   ├── stock_chart.py                 # Chart generation (matplotlib)
│   ├── language_config.py             # Multi-language templates
│   └── utils.py                       # Utility functions
│
├── 📂 trading/                         # 💱 Automated Trading System
│   ├── domestic_stock_trading.py      # KIS API wrapper
│   ├── kis_auth.py                    # Authentication & token management
│   ├── portfolio_telegram_reporter.py # Portfolio reporting
│   ├── 📂 config/                     # Trading configurations
│   │   ├── kis_devlp.yaml.example     # KIS API config template
│   │   └── kis_devlp.yaml             # Actual config (gitignored)
│   └── 📂 samples/                    # API usage examples
│
├── 📂 examples/                        # Web Interfaces
│   ├── 📂 streamlit/                  # Streamlit dashboard
│   │   ├── app_modern.py              # Main app
│   │   └── config.py.example          # Config template
│   └── 📂 dashboard/                  # Next.js frontend
│
├── 📂 tests/                           # Test Suite
│   ├── test_async_trading.py          # Trading system tests
│   ├── test_tracking_agent.py         # Agent tests
│   ├── quick_test.py                  # Quick integration tests
│   └── test_*.py                      # Various unit tests
│
├── 📂 utils/                           # Utility Scripts
│   ├── setup_crontab.sh               # Crontab automation setup
│   ├── setup_playwright.sh            # Playwright installation
│   └── CRONTAB_SETUP.md               # Setup documentation
│
├── 📂 perplexity-ask/                  # MCP Server (Node.js)
│   └── Perplexity search integration
│
├── 📂 sqlite/                          # SQLite MCP Server
│
├── 📂 docs/                            # Documentation & Images
│
├── stock_analysis_orchestrator.py     # 🎯 Main Orchestrator
├── stock_tracking_agent.py            # Trading Simulation Agent
├── stock_tracking_enhanced_agent.py   # Enhanced Trading Agent
├── telegram_ai_bot.py                 # Telegram Bot (Claude-based)
├── telegram_bot_agent.py              # Bot message handling
├── telegram_summary_agent.py          # Summary generation pipeline
├── trigger_batch.py                   # Surge Stock Detection
├── pdf_converter.py                   # Markdown → PDF conversion
├── telegram_config.py                 # TelegramConfig class
├── analysis_manager.py                # Background job queue
├── check_market_day.py                # Market holiday validation
│
├── requirements.txt                   # Python dependencies
├── .env.example                       # Environment variables template
├── mcp_agent.config.yaml.example      # MCP agent config template
├── mcp_agent.secrets.yaml.example     # MCP secrets template
├── Dockerfile                         # Docker container
└── docker-compose.yml                 # Docker orchestration
```

### Key Files Reference

| File | Purpose | When to Modify |
|------|---------|----------------|
| `stock_analysis_orchestrator.py` | Main pipeline orchestrator | Changing overall workflow |
| `cores/analysis.py` | Core analysis engine | Modifying agent collaboration |
| `cores/agents/*.py` | Individual agent definitions | Changing agent prompts/behavior |
| `cores/report_generation.py` | Report templates | Changing report format |
| `trading/domestic_stock_trading.py` | KIS API wrapper | Trading functionality changes |
| `stock_tracking_agent.py` | Trading decisions | Modifying trading strategy |
| `trigger_batch.py` | Stock screening | Changing detection criteria |
| `telegram_config.py` | Telegram configuration | Telegram settings |
| `pdf_converter.py` | PDF generation | PDF styling/formatting |
| `cores/language_config.py` | Multi-language templates | Adding/modifying languages |

---

## 4. Development Workflows

### Setting Up Development Environment

```bash
# 1. Clone repository
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install Playwright browsers
python3 -m playwright install chromium

# 4. Copy configuration templates
cp .env.example .env
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
cp ./examples/streamlit/config.py.example ./examples/streamlit/config.py
cp ./trading/config/kis_devlp.yaml.example ./trading/config/kis_devlp.yaml

# 5. Configure API keys in copied files
# Edit .env, mcp_agent.secrets.yaml, etc.

# 6. Install perplexity-ask MCP server (Node.js)
cd perplexity-ask && npm install && cd ..

# 7. Install Korean fonts (Linux only)
# Rocky Linux/CentOS: sudo dnf install google-nanum-fonts
# Ubuntu: Run ./cores/ubuntu_font_installer.py
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

### Running the System

```bash
# === Full Pipeline (Recommended) ===
# Morning analysis
python stock_analysis_orchestrator.py --mode morning

# Afternoon analysis
python stock_analysis_orchestrator.py --mode afternoon

# Both morning + afternoon
python stock_analysis_orchestrator.py --mode both

# Local testing without Telegram
python stock_analysis_orchestrator.py --mode morning --no-telegram

# === Individual Components ===
# Trigger batch only (surge stock detection)
python trigger_batch.py morning INFO --output trigger_results.json

# Generate analysis for specific stock
cd cores && python main.py

# Trading simulation
python stock_tracking_agent.py

# Telegram bot
python telegram_ai_bot.py

# === Multi-Language Broadcasting ===
# English report
python stock_analysis_orchestrator.py --mode morning --language en

# Multi-language broadcast
python stock_analysis_orchestrator.py --mode morning --broadcast-languages en,ja,zh
```

### Git Workflow

```bash
# Current branch naming convention
# Format: claude/claude-md-{session-id}

# Check current branch
git status

# Make changes and commit
git add .
git commit -m "feat: Add new feature description"

# Push to remote (use -u for first push)
git push -u origin claude/claude-md-mhyclezlwq2jq1tr-012iDsS6HRyXv357QdgTMNg2

# Create pull request
gh pr create --title "PR Title" --body "Description"
```

### Commit Message Conventions

Follow conventional commit format:

```
feat: Add new feature
fix: Bug fix
docs: Documentation changes
refactor: Code refactoring
test: Test additions/modifications
chore: Maintenance tasks
perf: Performance improvements
```

---

## 5. AI Agent System

### The 13 Specialized Agents

#### Analysis Team (6 Agents) - GPT-4.1 Based

**1. Technical Analyst** (`create_price_volume_analysis_agent`)
- **File**: `cores/agents/stock_price_agents.py`
- **Purpose**: Stock price and volume technical analysis
- **Analyzes**: Trends, moving averages, support/resistance, RSI, MACD, Bollinger Bands
- **Output**: Technical analysis section of report

**2. Trading Flow Analyst** (`create_investor_trading_analysis_agent`)
- **File**: `cores/agents/stock_price_agents.py`
- **Purpose**: Investor trading pattern analysis
- **Analyzes**: Institutional/foreign/individual trading flows, volume patterns
- **Output**: Trading flow section

**3. Financial Analyst** (`create_company_status_agent`)
- **File**: `cores/agents/company_info_agents.py`
- **Purpose**: Financial metrics and valuation
- **Analyzes**: PER, PBR, ROE, debt ratio, target prices, consensus
- **Output**: Company status section

**4. Industry Analyst** (`create_company_overview_agent`)
- **File**: `cores/agents/company_info_agents.py`
- **Purpose**: Business model and competitive position
- **Analyzes**: Business portfolio, market share, competitors, R&D, growth drivers
- **Output**: Company overview section

**5. Information Analyst** (`create_news_analysis_agent`)
- **File**: `cores/agents/news_strategy_agents.py`
- **Purpose**: News and catalyst identification
- **Analyzes**: Recent news, disclosures, industry trends, political/economic issues
- **Output**: News analysis section

**6. Market Analyst** (`create_market_index_analysis_agent`)
- **File**: `cores/agents/market_index_agents.py`
- **Purpose**: Market and macro environment
- **Analyzes**: KOSPI/KOSDAQ indices, macro indicators, global correlations
- **Output**: Market analysis section
- **Note**: Results are cached to reduce API calls

#### Strategy Team (1 Agent) - GPT-4.1 Based

**7. Investment Strategist** (`create_investment_strategy_agent`)
- **File**: `cores/agents/news_strategy_agents.py`
- **Purpose**: Synthesize all analyses into actionable strategy
- **Integrates**: All 6 analysis reports
- **Output**: Investment strategy with recommendations for different investor types

#### Communication Team (3 Agents)

**8-1. Summary Optimizer** (`telegram_summary_optimizer_agent`)
- **File**: `cores/agents/telegram_summary_optimizer_agent.py`
- **Model**: GPT-4.1
- **Purpose**: Convert detailed reports to Telegram-optimized summaries
- **Constraints**: 400 characters max, key points extraction
- **Output**: Concise Telegram message

**8-2. Quality Evaluator** (`telegram_summary_evaluator_agent`)
- **File**: `cores/agents/telegram_summary_evaluator_agent.py`
- **Model**: GPT-4.1
- **Purpose**: Evaluate summary quality and suggest improvements
- **Checks**: Accuracy, clarity, format compliance, hallucination detection
- **Process**: Iterative improvement loop until EXCELLENT rating

**8-3. Translation Specialist** (`translate_telegram_message`)
- **File**: `cores/agents/telegram_translator_agent.py`
- **Model**: GPT-4.1
- **Purpose**: Multi-language translation
- **Languages**: en, ja, zh, es, fr, de
- **Preserves**: Technical terms, market context, formatting

#### Trading Simulation Team (2 Agents) - GPT-5 Based

**9-1. Buy Specialist** (`create_trading_scenario_agent`)
- **File**: `cores/agents/trading_agents.py`
- **Purpose**: Buy decision-making and entry strategy
- **Evaluates**: Valuation, momentum, portfolio constraints
- **Scores**: 1-10 point system (6+ = buy)
- **Output**: JSON trading scenario with entry/exit strategy

**9-2. Sell Specialist** (`create_sell_decision_agent`)
- **File**: `cores/agents/trading_agents.py`
- **Purpose**: Monitor holdings and determine sell timing
- **Monitors**: Stop-loss, profit targets, technical trends, market conditions
- **Output**: JSON sell decision with confidence score

#### User Consultation Team (2 Agents) - Claude Sonnet 4.5

**10-1. Portfolio Consultant**
- **File**: `telegram_ai_bot.py`
- **Purpose**: User portfolio evaluation and advice
- **Features**: Custom advice based on user's positions, market data, latest news
- **Adapts**: Response style to user preferences

**10-2. Dialogue Manager**
- **File**: `telegram_ai_bot.py`
- **Purpose**: Maintain conversation context
- **Features**: Context memory, follow-up handling, data lookup

### Agent Collaboration Pattern

```python
# Pattern in cores/analysis.py
async def analyze_stock(company_name, company_code, reference_date, language="ko"):
    # 1. Get agent directory
    agents = get_agent_directory(company_name, company_code, reference_date,
                                  base_sections, language)

    # 2. Sequential execution (rate limit friendly)
    section_reports = {}
    for section in base_sections:
        if section in agents:
            agent = agents[section]

            # Special handling for market analysis (use cache)
            if section == "market_index_analysis":
                report = get_cached_or_generate_market_analysis(...)
            else:
                report = await generate_report(agent, section, ...)

            section_reports[section] = report

    # 3. Generate investment strategy (integrates all reports)
    strategy = await generate_investment_strategy(
        agents["investment_strategy"],
        section_reports,
        ...
    )

    return {
        "sections": section_reports,
        "strategy": strategy
    }
```

### Creating New Agents

**Template Pattern**:

```python
# File: cores/agents/your_agent.py
from mcp_agent import Agent

def create_your_agent(company_name, company_code, reference_date, language="ko"):
    """
    Create your custom agent.

    Args:
        company_name: Company name
        company_code: Stock code
        reference_date: Analysis date (YYYY-MM-DD)
        language: "ko" or "en"

    Returns:
        Agent instance
    """
    if language == "en":
        instruction = """
        You are a specialized analyst focusing on [YOUR DOMAIN].

        Analyze the stock data and provide:
        1. [Specific point 1]
        2. [Specific point 2]

        Be concise and data-driven.
        """
    else:  # Korean (default)
        instruction = """
        당신은 [도메인]을 전문으로 하는 애널리스트입니다.

        다음을 분석하세요:
        1. [분석 항목 1]
        2. [분석 항목 2]

        간결하고 데이터 중심으로 작성하세요.
        """

    return Agent(
        instruction=instruction,
        description=f"Custom Agent for {company_name}",
        # Add MCP tools if needed
        mcp_servers=["kospi_kosdaq", "firecrawl", "perplexity"],
    )

# Register in cores/agents/__init__.py
def get_agent_directory(...):
    agents = {
        # ... existing agents
        "your_section_name": lambda: create_your_agent(...),
    }
    return agents
```

---

## 6. Key Conventions

### Code Style

**Python Style Guide**:
- Follow PEP 8 conventions
- Use type hints where beneficial
- Docstrings for functions and classes
- Descriptive variable names
- Maximum line length: ~120 characters (flexible)

**Async/Await Pattern**:
```python
# ✅ Correct: Proper async pattern
async def analyze_stock(...):
    async with app.run() as parallel_app:
        result = await generate_report(agent, ...)
        return result

# ✅ Correct: Context manager for resources
async with AsyncTradingContext(mode="demo") as trader:
    result = await trader.async_buy_stock(stock_code)

# ❌ Incorrect: Blocking calls in async function
async def bad_example():
    result = requests.get(url)  # Blocks event loop!
    return result

# ✅ Correct: Use async libraries
async def good_example():
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()
```

**Logging Convention**:
```python
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"log_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Usage
logger.info("Starting analysis for %s", company_name)
logger.warning("Market data incomplete for %s", date)
logger.error("Failed to generate report: %s", e)
```

**Error Handling Pattern**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

# Graceful degradation
try:
    report = await generate_report(agent, section, ...)
    section_reports[section] = report
except Exception as e:
    logger.error(f"Error processing {section}: {e}", exc_info=True)
    section_reports[section] = f"Analysis unavailable: {section}"
    # Continue with next section

# Retry pattern for transient failures
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=10, max=30),
)
async def generate_report(...):
    # Implementation
    pass
```

### File Naming

- Python files: `snake_case.py`
- Configuration: `*.yaml`, `*.yaml.example`
- Documentation: `*.md`, `UPPERCASE.md` for important docs
- Test files: `test_*.py`

### Variable Naming

```python
# Stock codes: 6 digits
stock_code = "005930"  # Samsung Electronics

# Company names: Korean or English
company_name = "삼성전자"
company_name_en = "Samsung Electronics"

# Dates: YYYY-MM-DD string format
reference_date = "2025-11-14"

# Language codes: ISO 639-1
language = "ko"  # or "en", "ja", "zh"

# Trading modes
mode = "demo"  # or "real"

# Paths: Use pathlib.Path
from pathlib import Path
report_path = Path("reports") / f"{company_code}_{reference_date}.md"
```

### Language Support Convention

```python
# Always support both Korean (default) and English
def create_agent(..., language: str = "ko"):
    if language == "en":
        instruction = """English prompt"""
    else:  # Korean default
        instruction = """한국어 프롬프트"""
    return Agent(instruction=instruction)

# Multi-language support
SUPPORTED_LANGUAGES = ["ko", "en", "ja", "zh", "es", "fr", "de"]

# Language-specific config
telegram_channels = {
    "ko": os.getenv("TELEGRAM_CHANNEL_ID"),
    "en": os.getenv("TELEGRAM_CHANNEL_ID_EN"),
    "ja": os.getenv("TELEGRAM_CHANNEL_ID_JA"),
    "zh": os.getenv("TELEGRAM_CHANNEL_ID_ZH"),
}
```

### Configuration Pattern

```python
# ✅ Correct: SOLID configuration class
class TelegramConfig:
    def __init__(self, use_telegram=True, broadcast_languages=None):
        self.use_telegram = use_telegram
        self.broadcast_languages = broadcast_languages or []
        self._load_env()

    def validate_or_raise(self):
        """Fail-fast validation"""
        if self.use_telegram and not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN required")

    def log_status(self):
        """Transparent logging"""
        logger.info(f"Telegram enabled: {self.use_telegram}")

# Usage
config = TelegramConfig(use_telegram=True)
config.validate_or_raise()
config.log_status()
```

---

## 7. Configuration Management

### Required Configuration Files

#### `.env` - Environment Variables (Telegram & General)

```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
TELEGRAM_AI_BOT_TOKEN="987654321:ZYXwvuTSRqponMLKjihgfEDcba"

# Telegram Channels
TELEGRAM_CHANNEL_ID="-1001234567890"        # Korean (main)
TELEGRAM_CHANNEL_ID_EN="-1001234567891"     # English
TELEGRAM_CHANNEL_ID_JA="-1001234567892"     # Japanese
TELEGRAM_CHANNEL_ID_ZH="-1001234567893"     # Chinese

# Optional: Default language
PRISM_LANGUAGE=ko  # or "en"
```

#### `mcp_agent.config.yaml` - MCP Agent Configuration

```yaml
execution_engine: asyncio

mcp:
  servers:
    # Web research and data fetching
    webresearch: npx @mzxrai/mcp-webresearch
    firecrawl: firecrawl-mcp
    perplexity: node perplexity-ask/dist/index.js

    # Stock market data
    kospi_kosdaq: python3 -m kospi_kosdaq_stock_server

    # Database
    sqlite: uv run mcp-server-sqlite

    # Utilities
    time: uvx mcp-server-time

openai:
  default_model: gpt-5
  reasoning_effort: medium
```

#### `mcp_agent.secrets.yaml` - API Keys

```yaml
# OpenAI
OPENAI_API_KEY: "sk-..."

# Anthropic
ANTHROPIC_API_KEY: "sk-ant-..."

# Firecrawl
FIRECRAWL_API_KEY: "fc-..."

# Perplexity
PERPLEXITY_API_KEY: "pplx-..."

# WiseReport (Korean financial data)
WISEREPORT_KEY: "..."
```

#### `trading/config/kis_devlp.yaml` - Trading Configuration

```yaml
# Trading settings
default_unit_amount: 10000     # Buy amount per stock (KRW)
auto_trading: true             # Enable automated trading
default_mode: demo             # "demo" or "real"

# KIS API credentials
kis_app_key: "YOUR_APP_KEY"
kis_app_secret: "YOUR_APP_SECRET"
kis_account_number: "12345678-01"
kis_account_code: "01"         # Account type code

# Trading hours
market_open_time: "09:00"
market_close_time: "15:20"
```

#### `examples/streamlit/config.py` - Streamlit Dashboard

```python
# API Keys
OPENAI_API_KEY = "sk-..."
ANTHROPIC_API_KEY = "sk-ant-..."
FIRECRAWL_API_KEY = "fc-..."
PERPLEXITY_API_KEY = "pplx-..."
WISEREPORT_KEY = "..."

# Email configuration (optional)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = "your-email@gmail.com"
SMTP_PASSWORD = "your-app-password"
```

### Configuration Loading Pattern

```python
# 1. Load from environment variables
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

# 2. Validate configuration
if not api_key:
    raise ValueError("OPENAI_API_KEY not found in .env")

# 3. Use configuration classes
from telegram_config import TelegramConfig

telegram_config = TelegramConfig(
    use_telegram=not args.no_telegram,
    broadcast_languages=args.broadcast_languages
)
telegram_config.validate_or_raise()
```

---

## 8. Testing Guidelines

### Test Structure

```
tests/
├── test_async_trading.py          # Trading system integration tests
├── test_tracking_agent.py         # Trading agent tests
├── test_portfolio_reporter.py     # Portfolio reporting tests
├── test_json_parsing.py           # JSON validation tests
├── test_parse_price_value.py      # Utility function tests
├── quick_test.py                  # Quick integration tests
└── quick_json_test.py             # Rapid JSON validation
```

### Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_async_trading.py

# Run with verbose output
python -m pytest tests/ -v

# Quick integration test
python tests/quick_test.py

# Trading system test
python tests/test_async_trading.py
```

### Writing Tests

**Unit Test Pattern**:
```python
import pytest
import asyncio

def test_price_parsing():
    """Test price value parsing utility"""
    from cores.utils import parse_price_value

    assert parse_price_value("1,234") == 1234
    assert parse_price_value("1,234.56") == 1234.56
    assert parse_price_value("N/A") is None
```

**Async Test Pattern**:
```python
import pytest
import asyncio

@pytest.mark.asyncio
async def test_async_trading():
    """Test async trading context"""
    from trading.domestic_stock_trading import AsyncTradingContext

    async with AsyncTradingContext(mode="demo", buy_amount=10000) as trader:
        # Test buy operation
        result = await trader.async_buy_stock("005930")
        assert result["success"] is True
```

**Integration Test Pattern**:
```python
# tests/quick_test.py
async def test_portfolio_check():
    """Quick portfolio check"""
    trader = DomesticStockTrading(mode="demo")
    portfolio = await trader.async_get_portfolio()

    logger.info(f"Portfolio: {len(portfolio)} positions")
    assert isinstance(portfolio, list)
```

### Testing Conventions

1. **Safety First**: Always use `mode="demo"` in tests
2. **Async Tests**: Use `@pytest.mark.asyncio` for async tests
3. **Logging**: Include logging for debugging
4. **Cleanup**: Properly close resources (use context managers)
5. **Mocking**: Mock external APIs when possible
6. **Real API Tests**: Mark as `@pytest.mark.integration` and skip by default

---

## 9. Common Tasks

### Task 1: Adding a New AI Agent

```python
# 1. Create agent file
# File: cores/agents/your_agent.py

from mcp_agent import Agent

def create_your_agent(company_name, company_code, reference_date, language="ko"):
    if language == "en":
        instruction = """Your English instruction..."""
    else:
        instruction = """한국어 지시사항..."""

    return Agent(
        instruction=instruction,
        description=f"Your Agent for {company_name}",
        mcp_servers=["kospi_kosdaq"],  # Add required MCP servers
    )

# 2. Register in cores/agents/__init__.py
from .your_agent import create_your_agent

def get_agent_directory(...):
    agents = {
        # ... existing agents
        "your_section": lambda: create_your_agent(...),
    }
    return agents

# 3. Add to base_sections in cores/analysis.py
base_sections = [
    "price_volume_analysis",
    # ... existing sections
    "your_section",  # Add your section
]

# 4. Add section template in cores/report_generation.py
section_templates = {
    # ... existing templates
    "your_section": """
## Your Section Title

{content}
""",
}
```

### Task 2: Modifying Surge Detection Criteria

```python
# File: trigger_batch.py

def detect_surge_stocks(mode="morning"):
    # Modify thresholds
    VOLUME_THRESHOLD = 2.0  # Change: Volume surge ratio
    GAP_THRESHOLD = 3.0     # Change: Price gap percentage
    MIN_MARKET_CAP = 1000   # Change: Minimum market cap (billion KRW)

    # Add custom filters
    filtered_stocks = df[
        (df['volume_ratio'] >= VOLUME_THRESHOLD) &
        (df['gap_percent'] >= GAP_THRESHOLD) &
        (df['market_cap'] >= MIN_MARKET_CAP) &
        (df['your_custom_condition'])  # Add custom condition
    ]

    return filtered_stocks
```

### Task 3: Adding Multi-Language Support

```python
# 1. Add language to cores/language_config.py
class LanguageConfig:
    SUPPORTED_LANGUAGES = ["ko", "en", "ja", "zh", "es", "fr", "de", "your_lang"]

    TEMPLATES = {
        "your_lang": {
            "report_title": "Your Language Title",
            "sections": {
                "technical_analysis": "Technical Analysis",
                # ... add all sections
            }
        }
    }

# 2. Add Telegram channel to .env
TELEGRAM_CHANNEL_ID_YOUR_LANG="-1001234567899"

# 3. Use in broadcasting
python stock_analysis_orchestrator.py --broadcast-languages ko,en,your_lang
```

### Task 4: Modifying Trading Strategy

```python
# File: cores/agents/trading_agents.py

def create_trading_scenario_agent(...):
    instruction = """
    Trading Scenario Generation Instructions:

    BUY SCORE CRITERIA (Modify these):
    - Valuation (PER, PBR vs peers): 0-3 points
    - Technical Momentum: 0-3 points
    - News Catalyst: 0-2 points
    - Market Environment: 0-2 points
    - TOTAL: 10 points (buy threshold: 6+)

    RISK MANAGEMENT (Modify these):
    - Stop Loss: -5% to -7% (change percentage)
    - Target Price: +10% to +30% (change percentage)
    - Risk/Reward Ratio: Min 2:1 (change ratio)

    PORTFOLIO CONSTRAINTS (Modify these):
    - Max positions: 10 (change number)
    - Max same sector: 3 (change number)
    - Sector concentration: 30% (change percentage)
    """
    return Agent(instruction=instruction, ...)

# Apply changes
# 1. Modify instruction text
# 2. Update stock_tracking_agent.py if needed
# 3. Test with quick_test.py
```

### Task 5: Customizing Report Format

```python
# File: cores/report_generation.py

# 1. Modify report template
REPORT_TEMPLATE = """
# 📊 {company_name} ({company_code}) Investment Analysis Report

**Analysis Date**: {reference_date}
**Analyst**: PRISM-INSIGHT AI Agent System
**Language**: {language}

---

## 📌 Your Custom Section

{custom_content}

---

{sections}

---

## 💡 Investment Strategy

{investment_strategy}

---

**Disclaimer**: {disclaimer}
"""

# 2. Add custom sections
def generate_full_report(section_reports, investment_strategy, ...):
    custom_content = generate_custom_section(...)

    report = REPORT_TEMPLATE.format(
        company_name=company_name,
        custom_content=custom_content,
        sections=format_sections(section_reports),
        investment_strategy=investment_strategy,
        ...
    )
    return report
```

### Task 6: Adding New MCP Server

```python
# 1. Install MCP server
npm install -g your-mcp-server
# or
pip install your-mcp-server

# 2. Add to mcp_agent.config.yaml
mcp:
  servers:
    your_server: npx your-mcp-server
    # or
    your_server: python3 -m your_mcp_server

# 3. Add credentials to mcp_agent.secrets.yaml (if needed)
YOUR_SERVER_API_KEY: "your-api-key"

# 4. Use in agent
def create_your_agent(...):
    return Agent(
        instruction="...",
        mcp_servers=["your_server"],  # Add your server
    )
```

---

## 10. Important Constraints

### API Rate Limits

**OpenAI Rate Limits**:
- **Sequential execution** required (not parallel) to respect rate limits
- Retry with exponential backoff on failures
- Market analysis cached to reduce calls

```python
# ✅ Correct: Sequential execution
for section in base_sections:
    report = await generate_report(agent, section, ...)
    section_reports[section] = report

# ❌ Incorrect: Parallel execution (will hit rate limits)
tasks = [generate_report(agent, section, ...) for section in base_sections]
reports = await asyncio.gather(*tasks)  # Don't do this!
```

**Retry Pattern**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=10, max=30),
)
async def generate_report(...):
    # Will retry up to 2 times with exponential backoff
    pass
```

### Trading Constraints

**Portfolio Constraints**:
```python
MAX_SLOTS = 10              # Maximum stocks to hold
MAX_SAME_SECTOR = 3         # Maximum stocks in same sector
SECTOR_CONCENTRATION = 0.3  # Maximum 30% in one sector
```

**Risk Management**:
```python
# Stop Loss Rules
STOP_LOSS_MIN = -0.05      # -5%
STOP_LOSS_MAX = -0.07      # -7%
EXTREME_STOP_LOSS = -0.10  # -10% (extreme cases)

# Target Price
TARGET_MIN = 0.10          # +10%
TARGET_TYPICAL = 0.20      # +20%
TARGET_MAX = 0.30          # +30%

# Risk/Reward Ratio
MIN_RISK_REWARD_RATIO = 2.0  # Min 2:1 (20% target / 10% stop)

# Validation Example
if support_level_loss > 0.07:
    # Support beyond -7% requires Risk/Reward ≥ 2:1
    risk_reward = expected_gain / abs(support_level_loss)
    if risk_reward < 2.0:
        return {"suitable": False, "reason": "Insufficient risk/reward ratio"}
```

**Trading Mode Safety**:
```python
# Default mode: demo (safe)
DEFAULT_MODE = "demo"

# Real mode requires explicit confirmation
if mode == "real":
    confirm = input("⚠️  REAL TRADING MODE. Confirm? (yes/no): ")
    if confirm.lower() != "yes":
        logger.warning("Real trading cancelled by user")
        return
```

### Time-Based Data Accuracy

**Critical**: During market hours (09:00-15:20), today's data is incomplete!

```python
from datetime import datetime, time

def get_reliable_analysis_date():
    """
    Get reliable date for analysis.

    During market hours (09:00-15:20):
    - ❌ Today's volume/candles are INCOMPLETE
    - ✅ Use PREVIOUS day's data

    After market close (15:30+):
    - ✅ Today's data is COMPLETE and reliable
    """
    now = datetime.now()
    market_open = time(9, 0)
    market_close = time(15, 20)

    if market_open <= now.time() <= market_close:
        # During market hours: use previous day
        reference_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        logger.warning(f"Market hours: Using previous day {reference_date}")
    else:
        # After hours: today's data is complete
        reference_date = now.strftime("%Y-%m-%d")

    return reference_date
```

### Language Support

**Supported Languages**:
- Korean (ko): Default, most complete
- English (en): Full support
- Japanese (ja), Chinese (zh): Via translation
- Spanish (es), French (fr), German (de): Via translation

**Language Convention**:
```python
# Always provide Korean as fallback
def create_agent(..., language: str = "ko"):
    if language == "en":
        instruction = """English instruction"""
    else:  # Default to Korean
        instruction = """한국어 지시사항"""
    return Agent(instruction=instruction)
```

### Database Constraints

**SQLite with Async Support**:
```python
import aiosqlite

# ✅ Correct: Use aiosqlite for async operations
async with aiosqlite.connect("database.db") as db:
    async with db.execute("SELECT * FROM table") as cursor:
        rows = await cursor.fetchall()

# ❌ Incorrect: Don't use synchronous sqlite3 in async code
import sqlite3
conn = sqlite3.connect("database.db")  # Blocks event loop!
```

**Database Tables**:
- `stock_holdings`: Current portfolio
- `trading_history`: All trades (buy/sell)
- `watchlist_history`: Tracked stocks
- `holding_decisions`: Sell signals
- `market_condition`: Market analysis cache

---

## 11. Troubleshooting

### Common Issues

#### Issue 1: Playwright PDF Generation Fails

**Symptoms**:
```
Error: Browser executable not found
```

**Solution**:
```bash
# Install Chromium browser
python3 -m playwright install chromium

# Ubuntu: Install dependencies
python3 -m playwright install --with-deps chromium

# Or use setup script
cd utils && chmod +x setup_playwright.sh && ./setup_playwright.sh
```

#### Issue 2: Korean Fonts Not Displaying in Charts

**Symptoms**: Korean text shows as squares in generated charts

**Solution**:
```bash
# Rocky Linux/CentOS
sudo dnf install google-nanum-fonts

# Ubuntu/Debian
python3 cores/ubuntu_font_installer.py

# Rebuild font cache
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

#### Issue 3: Telegram Bot Not Responding

**Symptoms**: Bot doesn't reply to messages

**Checklist**:
1. Verify `.env` configuration:
   ```bash
   cat .env | grep TELEGRAM
   ```
2. Check bot token validity
3. Verify bot has access to channel
4. Check logs:
   ```bash
   tail -f log_*.log
   ```
5. Test configuration:
   ```python
   from telegram_config import TelegramConfig
   config = TelegramConfig()
   config.validate_or_raise()
   config.log_status()
   ```

#### Issue 4: MCP Server Connection Failed

**Symptoms**:
```
Error: MCP server 'kospi_kosdaq' not responding
```

**Solution**:
```bash
# 1. Check MCP server installation
python3 -m kospi_kosdaq_stock_server  # Should start server

# 2. Verify mcp_agent.config.yaml
cat mcp_agent.config.yaml | grep kospi_kosdaq

# 3. Check API keys in mcp_agent.secrets.yaml
cat mcp_agent.secrets.yaml | grep WISEREPORT

# 4. Test individual server
cd perplexity-ask && npm install && node dist/index.js
```

#### Issue 5: Trading API Authentication Failed

**Symptoms**:
```
Error: KIS API authentication failed
```

**Solution**:
```bash
# 1. Verify kis_devlp.yaml configuration
cat trading/config/kis_devlp.yaml

# 2. Check credentials
# - kis_app_key: Valid?
# - kis_app_secret: Valid?
# - kis_account_number: Correct format?

# 3. Test authentication
python -c "from trading.kis_auth import get_access_token; print(get_access_token())"

# 4. Check token expiration (tokens expire every 24 hours)
# Authentication happens automatically on each request
```

#### Issue 6: JSON Parsing Error in Trading Scenarios

**Symptoms**:
```
Error: Invalid JSON in trading scenario
```

**Solution**:
```python
# 1. Use json-repair for automatic fixing
from json_repair import repair_json
import ujson

try:
    data = ujson.loads(json_str)
except Exception:
    # Attempt repair
    repaired = repair_json(json_str)
    data = ujson.loads(repaired)

# 2. Test JSON validation
python tests/quick_json_test.py

# 3. Check agent output format
# Ensure trading agents return valid JSON structure
```

#### Issue 7: Out of Memory During Analysis

**Symptoms**: Process killed during large batch analysis

**Solution**:
```bash
# 1. Reduce batch size
# Modify stock_analysis_orchestrator.py
MAX_CONCURRENT_ANALYSES = 3  # Reduce from 5

# 2. Use --no-telegram to skip summary generation
python stock_analysis_orchestrator.py --mode morning --no-telegram

# 3. Increase system memory or swap

# 4. Process stocks individually
for stock_code in stock_list:
    python cores/main.py --stock-code $stock_code
```

### Debug Mode

Enable debug logging for troubleshooting:

```python
import logging

# Set to DEBUG level
logging.basicConfig(
    level=logging.DEBUG,  # Change from INFO to DEBUG
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
```

### Getting Help

1. **Check logs**: `tail -f log_*.log`
2. **GitHub Issues**: [Report issues](https://github.com/dragon1086/prism-insight/issues)
3. **Telegram**: @stock_ai_ko
4. **Documentation**:
   - [README.md](README.md)
   - [CONTRIBUTING.md](CONTRIBUTING.md)
   - [utils/CRONTAB_SETUP.md](utils/CRONTAB_SETUP.md)
   - [utils/PLAYWRIGHT_SETUP.md](utils/PLAYWRIGHT_SETUP.md)

---

## Appendix: Quick Reference

### Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `TELEGRAM_BOT_TOKEN` | Main bot token | Yes (if using Telegram) |
| `TELEGRAM_AI_BOT_TOKEN` | AI bot token | Yes (if using AI bot) |
| `TELEGRAM_CHANNEL_ID` | Main channel (Korean) | Yes (if using Telegram) |
| `TELEGRAM_CHANNEL_ID_EN` | English channel | No |
| `TELEGRAM_CHANNEL_ID_JA` | Japanese channel | No |
| `TELEGRAM_CHANNEL_ID_ZH` | Chinese channel | No |
| `PRISM_LANGUAGE` | Default language | No (defaults to "ko") |

### Command-Line Arguments

```bash
# stock_analysis_orchestrator.py
--mode {morning,afternoon,both}     # Analysis timing
--no-telegram                       # Skip Telegram integration
--language {ko,en}                  # Report language
--broadcast-languages ko,en,ja      # Multi-language broadcast

# trigger_batch.py
{morning,afternoon} {DEBUG,INFO,WARNING}  # Mode and log level
--output trigger_results.json       # Output file
```

### Key Directories

| Directory | Purpose |
|-----------|---------|
| `/cores/` | Core analysis engine and agents |
| `/trading/` | Trading system and KIS API |
| `/examples/` | Web interfaces (Streamlit, Next.js) |
| `/tests/` | Test suite |
| `/utils/` | Utility scripts and setup tools |
| `/docs/` | Documentation and images |

### Port Numbers

| Service | Port |
|---------|------|
| Streamlit Dashboard | 8501 |
| Next.js Dashboard | 3000 |

---

**Document Version**: 1.0
**Last Updated**: 2025-11-14
**Maintained By**: PRISM-INSIGHT Development Team

For questions or improvements to this document, please submit a GitHub issue or pull request.
