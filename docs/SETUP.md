# PRISM-INSIGHT Setup Guide

> Complete installation and configuration guide for PRISM-INSIGHT

**Language**: [English](SETUP.md) | [한국어](SETUP_ko.md)

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start with Docker](#quick-start-with-docker)
3. [Manual Installation](#manual-installation)
4. [Configuration Files](#configuration-files)
5. [Platform-Specific Setup](#platform-specific-setup)
6. [Optional Components](#optional-components)
7. [Verification](#verification)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.10+ | Core runtime |
| Node.js | 18+ | MCP servers (perplexity-ask) |
| pip | Latest | Package management |

### API Keys (Required for Full Features)

| Service | Purpose | Get Key |
|---------|---------|---------|
| OpenAI | GPT-5 for analysis & trading | [platform.openai.com](https://platform.openai.com/api-keys) |
| Anthropic | Claude for Telegram bot | [console.anthropic.com](https://console.anthropic.com/) |
| Firecrawl | Web crawling MCP | [firecrawl.dev](https://www.firecrawl.dev/) |
| Perplexity | Web search MCP | [perplexity.ai](https://www.perplexity.ai/) |

### API Keys (Optional)

| Service | Purpose | Get Key |
|---------|---------|---------|
| Telegram Bot | Channel messaging | [BotFather](https://t.me/botfather) |
| Korea Investment & Securities | Automated trading | [KIS Developers](https://apiportal.koreainvestment.com/) |
| Finnhub | US stock news | [finnhub.io](https://finnhub.io/) |

---

## Quick Start with Docker

Docker is the recommended way to run PRISM-INSIGHT in production environments.

### Step 1: Clone Repository

```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
```

### Step 2: Prepare Configuration Files

```bash
# Core configuration (required)
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml

# Edit config files with your API keys
# - mcp_agent.secrets.yaml: OpenAI API key
# - mcp_agent.config.yaml: KRX credentials (Kakao account)
```

### Step 3: Build and Run

```bash
# Build and start container
docker-compose up -d

# Check container status
docker ps

# View logs
docker-compose logs -f
```

### Step 4: Run Analysis

```bash
# Run morning analysis manually
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram

# Run with Telegram (if configured)
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning
```

### Docker Commands Reference

```bash
# Stop container
docker-compose down

# Rebuild after code changes
docker-compose up -d --build

# View real-time logs
docker-compose logs -f prism-insight

# Access container shell
docker exec -it prism-insight-container /bin/bash
```

> **Note**: The Docker container includes scheduled cron jobs for automated daily analysis. See `docker/entrypoint.sh` for the schedule configuration.

---

## Manual Installation

For development or custom environments, follow these steps for manual installation.

### Step 1: Clone Repository

```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
```

### Step 2: Install Python Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Prepare Configuration Files

Copy example files to create your configuration:

```bash
# Core configuration (required)
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml

# Environment variables (optional - for Telegram)
cp .env.example .env

# Streamlit dashboard (optional)
cp ./examples/streamlit/config.py.example ./examples/streamlit/config.py

# Trading configuration (optional - for automated trading)
cp ./trading/config/kis_devlp.yaml.example ./trading/config/kis_devlp.yaml
```

### Step 4: Configure API Keys

Edit `mcp_agent.secrets.yaml` with your API keys:

```yaml
# Required
OPENAI_API_KEY: "sk-..."

# Optional (for full features)
ANTHROPIC_API_KEY: "sk-ant-..."
FIRECRAWL_API_KEY: "fc-..."
PERPLEXITY_API_KEY: "pplx-..."
```

### Step 5: Configure MCP Servers

Edit `mcp_agent.config.yaml`:

```yaml
execution_engine: asyncio

mcp:
  servers:
    kospi_kosdaq:
      command: "python3"
      args: ["-m", "kospi_kosdaq_stock_server"]
      env:
        KAKAO_ID: "your_kakao_email@example.com"
        KAKAO_PW: "your_kakao_password"

    firecrawl: firecrawl-mcp
    perplexity: node perplexity-ask/dist/index.js
    sqlite: uv run mcp-server-sqlite --directory sqlite stock_tracking_db.sqlite
    time: uvx mcp-server-time

openai:
  default_model: gpt-5
  reasoning_effort: medium
```

> **Note**: Kakao credentials are required for Korean market data (KRX Data Marketplace authentication).
>
> **2-Step Verification**: If enabled on your Kakao account, you'll need to confirm in the app for each analysis. To disable: Kakao App > Settings > Kakao Account > Account Security > 2-Step Verification 'Off'.

### Step 6: Install Playwright (PDF Generation)

```bash
# Install package (included in requirements.txt)
pip install playwright

# Download Chromium browser
python3 -m playwright install chromium
```

See [Platform-Specific Setup](#platform-specific-setup) for detailed instructions.

### Step 7: Install perplexity-ask MCP Server

```bash
cd perplexity-ask
npm install
cd ..
```

### Step 8: Install Korean Fonts (Linux Only)

Required for Korean text in charts. See [Platform-Specific Setup](#platform-specific-setup).

---

## Configuration Files

### Core Settings (Required)

| File | Purpose |
|------|---------|
| `mcp_agent.config.yaml` | MCP server configuration |
| `mcp_agent.secrets.yaml` | API keys and secrets |

### Telegram Settings (Optional)

| File | Purpose |
|------|---------|
| `.env` | Telegram channel ID, bot token |

```bash
# .env file
TELEGRAM_CHANNEL_ID="-1001234567890"
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"

# Multi-language broadcasting (optional)
TELEGRAM_CHANNEL_ID_EN="-1001234567891"
TELEGRAM_CHANNEL_ID_JA="-1001234567892"
TELEGRAM_CHANNEL_ID_ZH="-1001234567893"
```

> **Tip**: Use `--no-telegram` option to run without Telegram configuration!

### Trading Settings (Optional)

| File | Purpose |
|------|---------|
| `trading/config/kis_devlp.yaml` | Korea Investment & Securities API |

```yaml
# kis_devlp.yaml
default_unit_amount: 10000     # Buy amount per stock (KRW)
auto_trading: true
default_mode: demo             # "demo" or "real"

kis_app_key: "YOUR_APP_KEY"
kis_app_secret: "YOUR_APP_SECRET"
kis_account_number: "12345678-01"
kis_account_code: "01"
```

### Web Interface Settings (Optional)

| File | Purpose |
|------|---------|
| `examples/streamlit/config.py` | Streamlit dashboard API keys |

---

## Platform-Specific Setup

### macOS

```bash
# Playwright
pip3 install playwright
python3 -m playwright install chromium

# Korean fonts: Built-in support, no installation needed
```

### Ubuntu / Debian

```bash
# Playwright with dependencies
pip install playwright
python3 -m playwright install --with-deps chromium

# Korean fonts
./cores/ubuntu_font_installer.py

# Refresh font cache
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

### Rocky Linux 8 / CentOS / RHEL

```bash
# Playwright
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

# Korean fonts
sudo dnf install google-nanum-fonts

# Refresh font cache
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

### Windows

```bash
# Playwright
pip install playwright
python -m playwright install chromium

# Korean fonts: Built-in support, no installation needed
```

For detailed Playwright setup, see [utils/PLAYWRIGHT_SETUP.md](../utils/PLAYWRIGHT_SETUP.md).

---

## Optional Components

### Automated Scheduling (Crontab)

Set up automatic execution:

```bash
# Simple setup (recommended)
chmod +x utils/setup_crontab_simple.sh
utils/setup_crontab_simple.sh

# Or advanced setup
chmod +x utils/setup_crontab.sh
utils/setup_crontab.sh
```

See [utils/CRONTAB_SETUP.md](../utils/CRONTAB_SETUP.md) for details.

### US Stock Market Module

For US market analysis (NYSE, NASDAQ):

```bash
# Additional API key (optional, for enhanced news)
# Add to .env:
FINNHUB_API_KEY="your_finnhub_key"

# Run US analysis
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

### Event-Driven Trading Signals

For Redis/Upstash or GCP Pub/Sub integration:

```bash
# .env file
UPSTASH_REDIS_REST_URL="https://xxx.upstash.io"
UPSTASH_REDIS_REST_TOKEN="your-token"

# Or for GCP
GCP_PROJECT_ID="your-gcp-project"
GCP_PUBSUB_SUBSCRIPTION_ID="your-subscription"
GCP_CREDENTIALS_PATH="/path/to/service-account.json"
```

---

## Verification

### Quick Test (No Telegram)

```bash
# Run morning analysis without Telegram
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### Test Individual Components

```bash
# 1. Test surge stock detection
python trigger_batch.py morning INFO --output trigger_results.json

# 2. Test PDF conversion
python pdf_converter.py sample.md sample.pdf

# 3. Test MCP server connection
python cores/main.py
```

### Expected Output

Successful run will create:
- `trigger_results_*.json` - Detected surge stocks
- `reports/*.md` - Analysis reports in Markdown
- `pdf_reports/*.pdf` - PDF versions of reports
- `stock_tracking_db.sqlite` - Trading simulation database

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Playwright PDF fails | Run `python3 -m playwright install chromium` |
| Korean fonts missing | Install fonts and run `fc-cache -fv` |
| MCP server fails | Check API keys in `mcp_agent.secrets.yaml` |
| Kakao auth fails | Disable 2-step verification or confirm in app |
| JSON parsing error | Library auto-repairs; check logs for details |

### Debug Mode

Enable verbose logging:

```bash
# Set log level in code or environment
export LOG_LEVEL=DEBUG
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### Log Files

Check logs for errors:

```bash
# Recent log files
ls -la *.log

# View specific log
tail -f stock_analysis_*.log
```

### Getting Help

- **Documentation**: [docs/](../docs/)
- **GitHub Issues**: [Report bugs](https://github.com/dragon1086/prism-insight/issues)
- **Telegram**: [@stock_ai_agent](https://t.me/stock_ai_agent)
- **Discussions**: [GitHub Discussions](https://github.com/dragon1086/prism-insight/discussions)

---

## Next Steps

After successful setup:

1. **Try the Quick Start**: Run `python stock_analysis_orchestrator.py --mode morning --no-telegram`
2. **Explore the Dashboard**: Visit [analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)
3. **Join the Community**: Subscribe to [Telegram Channel](https://t.me/prism_insight_global_en)
4. **Customize**: Modify agents in `cores/agents/` directory

---

**Document Version**: 1.0
**Last Updated**: 2026-01-28
