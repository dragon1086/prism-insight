# CLAUDE.md - AI Assistant Guide for PRISM-INSIGHT

> **Version**: 2.23.0 | **Updated**: 2026-09-30

## Quick Overview

**PRISM-INSIGHT** = AI-powered Korean/US stock analysis & automated trading system

```yaml
Stack: Python 3.10+, mcp-agent, GPT-5.x (per-agent models in docs/CLAUDE_AGENTS.md), SQLite, Telegram, KIS API
Scale: ~265,000+ LOC, 13+ AI agents, KR/US dual market support, BTC (demo)
```

## Project Structure

```
prism-insight/
├── cores/                    # AI Analysis Engine
│   ├── agents/              # 13 specialized AI agents
│   ├── chatgpt_proxy/       # ChatGPT OAuth Proxy (Codex endpoint)
│   ├── analysis.py          # Core orchestration
│   └── report_generation.py # Report templates
├── trading/                  # KIS API Trading (KR)
├── prism-us/                # US Stock Module (mirror of KR)
│   ├── cores/agents/        # US-specific agents
│   ├── trading/             # KIS Overseas API
│   └── us_stock_analysis_orchestrator.py
├── examples/                 # Dashboards, messaging
└── tests/                    # Test suite
```

## Analysis Pipeline

```
[Morning Run]
trigger_batch.py / us_trigger_batch.py
    → Surge/momentum detection → stock candidates (JSON)
    ↓
stock_analysis_orchestrator.py
    → data_prefetch (parallel data fetch)
    → cores/analysis.py — 6 analysis agents (sequential)
        Technical Analyst → Trading Flow → Financial → Industry → News → Market
    → Investment Strategist (integrates all 6 reports)
    → report_generation.py → PDF
    → telegram_summary_agent → Telegram message (Korean)
    ↓
stock_tracking_agent.py  (runs independently, cron)
    → sell_decision_agent → KIS sell order
    → buy via trigger signal → KIS buy order
```

> **Multi-account (v2.9.0)**: `stock_tracking_agent` fans out buy/sell to all accounts in `kis_devlp.yaml`. Telegram report is sent from primary account only.

---

## AI Agents

13 specialized agents organized in 4 teams. Full details → [`docs/CLAUDE_AGENTS.md`](docs/CLAUDE_AGENTS.md)

| # | Agent | File | Purpose |
|---|-------|------|---------|
| 1 | Technical Analyst | `cores/agents/stock_price_agents.py` | Price/volume, RSI, MACD, Bollinger |
| 2 | Trading Flow Analyst | `cores/agents/stock_price_agents.py` | Institutional/foreign/individual flows |
| 3 | Financial Analyst | `cores/agents/company_info_agents.py` | PER, PBR, ROE, valuation |
| 4 | Industry Analyst | `cores/agents/company_info_agents.py` | Business model, competitive position |
| 5 | News Analyst | `cores/agents/news_strategy_agents.py` | News, catalysts, disclosures |
| 6 | Market Analyst | `cores/agents/market_index_agents.py` | KOSPI/KOSDAQ, macro (result cached) |
| 7 | Investment Strategist | `cores/agents/news_strategy_agents.py` | Synthesizes 1-6 into actionable strategy |
| 8 | Macro Intelligence | `cores/agents/macro_intelligence_agent.py` | Market regime, leading/lagging sectors |
| 9 | Summary Optimizer | `cores/agents/telegram_summary_optimizer_agent.py` | Report → 400-char Telegram message |
| 10 | Quality Evaluator | `cores/agents/telegram_summary_evaluator_agent.py` | Summary QA loop until EXCELLENT |
| 11 | Translation Specialist | `cores/agents/telegram_translator_agent.py` | KR→EN/JA/ZH/ES broadcast |
| 12 | Buy Specialist | `cores/agents/trading_agents.py` | Entry decision, score threshold |
| 13 | Sell Specialist | `cores/agents/trading_agents.py` | Hold/sell decision, stop-loss |

> US agents mirror KR under `prism-us/cores/agents/` (no Trading Journal or Translation agents).

---

## Key Entry Points

| Command | Purpose |
|---------|---------|
| `python stock_analysis_orchestrator.py --mode morning` | KR morning analysis |
| `python stock_analysis_orchestrator.py --mode morning --no-telegram` | Local test (no Telegram) |
| `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning` | ChatGPT OAuth proxy mode |
| `python prism-us/us_stock_analysis_orchestrator.py --mode morning` | US morning analysis |
| `python trigger_batch.py morning INFO` | KR surge detection only |
| `python prism-us/us_trigger_batch.py morning INFO` | US surge detection only |
| `python demo.py 005930` | Single stock report (KR) |
| `python demo.py AAPL --market us` | Single stock report (US) |
| `python prism-us/us_pending_order_batch.py` | US pending order batch (10:05 KST cron) |
| `python prism-us/us_pending_order_batch.py --dry-run` | US pending order dry run |
| `python weekly_insight_report.py --dry-run` | Weekly insight report (print only) |
| `python weekly_insight_report.py --broadcast-languages en,ja` | Weekly report + broadcast |

## Configuration Files

| File | Purpose |
|------|---------|
| `.env` | Telegram tokens, channel IDs, Redis/GCP settings, `PRISM_OPENAI_AUTH_MODE` |
| `mcp_agent.secrets.yaml` | API keys (OpenAI, Anthropic, Firecrawl, etc.) |
| `mcp_agent.config.yaml` | MCP server configuration |
| `trading/config/kis_devlp.yaml` | KIS trading API credentials |

**Setup**: Copy `*.example` files and fill in credentials.

### Key Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram bot token |
| `TELEGRAM_CHANNEL_ID` | ✅ | KR channel ID |
| `PRISM_OPENAI_AUTH_MODE` | ✅ | `api_key` (default) or `chatgpt_oauth` |
| `ADANOS_API_KEY` | ⬜ | US social sentiment (Adanos). Omit to disable |
| `ENABLE_TRADING_JOURNAL` | ⬜ | `true` to enable trading journal agent |
| `GCP_CREDENTIALS_PATH` | ⬜ | GCP service account JSON for Pub/Sub |

### Multi-Account Setup (v2.9.0)

```yaml
# trading/config/kis_devlp.yaml
accounts:
  - id: primary       # Telegram reports use this account
    app_key: ...
    app_secret: ...
    account_no: XXXXXXXX-XX
  - id: secondary
    app_key: ...
    app_secret: ...
    account_no: YYYYYYYY-YY
```

> DB migration (`account_id` column) runs automatically on first start.

## Code Conventions

### Async Pattern (Required)
```python
# ✅ Correct
async with AsyncTradingContext(mode="demo") as trader:
    result = await trader.async_buy_stock(ticker)

# ❌ Wrong - blocks event loop
result = requests.get(url)  # Use aiohttp instead
```

### Safe Type Conversion (v2.2 - KIS API)
```python
# KIS API may return '' instead of 0 - always use safe helpers
from trading.us_stock_trading import _safe_float, _safe_int
price = _safe_float(data.get('last'))  # Handles '', None, invalid strings
```

### Korean Report Tone (v2.3.0)
All Korean (ko) report sections must use formal polite style (합쇼체):
```python
# ✅ Correct - 높임말
"상승세를 보이고 있습니다"
"주목할 필요가 있습니다"

# ❌ Wrong - 반말
"상승세를 보인다"
"주목할 필요가 있다"
```
Rule is enforced in `cores/report_generation.py` (common prompts) and each agent's instruction.

### Sequential Agent Execution
```python
# ✅ Correct - respects rate limits
for section in sections:
    report = await generate_report(agent, section)

# ❌ Wrong - hits rate limits
reports = await asyncio.gather(*[generate_report(a, s) for s in sections])
```

## Trading Constraints

Before changing screening, regime, entry, exit, or position-sizing behavior,
follow [`docs/TRADING_CHANGE_REVIEW_HARNESS.md`](docs/TRADING_CHANGE_REVIEW_HARNESS.md).
The review must test counterexamples and prefer a single, narrow decision-layer
change over duplicated screening, hard-gate, and prompt penalties.

```python
MAX_SLOTS = 10              # Max stocks to hold
MAX_SAME_SECTOR = 3         # Max per sector
DEFAULT_MODE = "demo"       # Always default to demo

# Stop loss: per-trigger sl_max (5-8%) in TRIGGER_CRITERIA,
# trigger_batch.py (KR) and prism-us/us_trigger_batch.py (US)
```

## KR vs US Differences

| Item | KR | US |
|------|----|----|
| Data Source | KIS API, kospi_kosdaq MCP | yfinance, sec-edgar MCP |
| Market Hours | 09:00-15:30 KST | 09:30-16:00 EST |
| Market Cap Filter | 5000억 KRW | $1B USD (env `US_SCREENING_MIN_MARKET_CAP_USD`) |
| DB Tables | `stock_holdings` | `us_stock_holdings` |
| Trading API | KIS 국내주식 | KIS 해외주식 (예약주문 지원) |

## US Reserved Orders (Important)

US market operates on different timezone. When market is closed:
- **Buy**: Requires `limit_price` for reserved order
- **Sell**: Can use `limit_price` or `use_moo=True` (Market On Open)

```python
# Smart buy/sell auto-selects method based on market hours
result = await trading.async_buy_stock(ticker=ticker, limit_price=current_price)
result = await trading.async_sell_stock(ticker=ticker, limit_price=current_price)
```

## Database Tables

| Table | Purpose |
|-------|---------|
| `stock_holdings` / `us_stock_holdings` | Current portfolio |
| `trading_history` / `us_trading_history` | Trade records |
| `watchlist_history` / `us_watchlist_history` | Analyzed but not entered |
| `analysis_performance_tracker` / `us_analysis_performance_tracker` | 7/14/30-day tracking |
| `us_holding_decisions` | US AI holding analysis (v2.2.0) |
| `us_pending_orders` | US queued reserved orders (v2.7.1) |

## Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| Playwright PDF fails | `python3 -m playwright install chromium` |
| Korean fonts missing | `sudo dnf install google-nanum-fonts && fc-cache -fv` |
| KIS auth fails | Check `trading/config/kis_devlp.yaml` |
| prism-us import error | v2.9.0: `importlib.util` 기반 임포트로 해결됨. 직접 수정 시 `cores/openai_debug.py` 참고 |
| `/report` 오류 후 재사용 불가 | v2.5.0 수정 - 서버 오류 시 자동 환급됨, 재시도 가능 |
| US 예약주문 시간외 실패 | v2.7.1 - 10시 이전 주문은 자동 큐잉 → 10:05 KST 배치 실행 |
| ChatGPT OAuth 404 | Codex 엔드포인트 미지원 모델 → `_MODEL_MAP` 자동 매핑 (v2.7.0) |
| ChatGPT OAuth proxy 무반응 | `python -m cores.chatgpt_proxy.oauth_login`으로 토큰 갱신 |

## i18n Strategy (v2.2.0)

- **Code comments/logs**: English
- **Telegram messages**: Korean templates (default channel is KR)
- **Broadcast channels**: Translation agent converts to target language (`--broadcast-languages en,ja,zh,es`)

## Branch & Commit Convention

### Branch Rule
- **코드 파일 변경** (`.py`, `.ts`, `.tsx`, `.js`, `.jsx` 등): 반드시 feature 브랜치에서 작업 후 PR 생성
- **문서만 변경** (`.md` 등): main 직접 커밋 허용
- 브랜치 네이밍: `feat/`, `fix/`, `refactor/`, `test/` + 설명 (예: `fix/us-dashboard-ai-holding`)

### Commit Message
```
feat: New feature
fix: Bug fix
docs: Documentation
refactor: Code refactoring
test: Tests
```

---

## Version History

Release notes live in `docs/RELEASE_NOTES_v*.md`; for full history, see git log.
