# CLAUDE.md - AI Assistant Guide for PRISM-INSIGHT

> **Version**: 2.2.0 | **Updated**: 2026-02-04

## Quick Overview

**PRISM-INSIGHT** = AI-powered Korean/US stock analysis & automated trading system

```yaml
Stack: Python 3.10+, mcp-agent, GPT-5/Claude 4.5, SQLite, Telegram, KIS API
Scale: ~70 files, 16,000+ LOC, 13+ AI agents, KR/US dual market support
```

## Project Structure

```
prism-insight/
├── cores/                    # AI Analysis Engine
│   ├── agents/              # 13 specialized AI agents
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

## Key Entry Points

| Command | Purpose |
|---------|---------|
| `python stock_analysis_orchestrator.py --mode morning` | KR morning analysis |
| `python stock_analysis_orchestrator.py --mode morning --no-telegram` | Local test (no Telegram) |
| `python prism-us/us_stock_analysis_orchestrator.py --mode morning` | US morning analysis |
| `python trigger_batch.py morning INFO` | KR surge detection only |
| `python prism-us/us_trigger_batch.py morning INFO` | US surge detection only |
| `python demo.py 005930` | Single stock report (KR) |
| `python demo.py AAPL --market us` | Single stock report (US) |

## Configuration Files

| File | Purpose |
|------|---------|
| `.env` | Telegram tokens, channel IDs, Redis/GCP settings |
| `mcp_agent.secrets.yaml` | API keys (OpenAI, Anthropic, Firecrawl, etc.) |
| `mcp_agent.config.yaml` | MCP server configuration |
| `trading/config/kis_devlp.yaml` | KIS trading API credentials |

**Setup**: Copy `*.example` files and fill in credentials.

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

### Sequential Agent Execution
```python
# ✅ Correct - respects rate limits
for section in sections:
    report = await generate_report(agent, section)

# ❌ Wrong - hits rate limits
reports = await asyncio.gather(*[generate_report(a, s) for s in sections])
```

## Trading Constraints

```python
MAX_SLOTS = 10              # Max stocks to hold
MAX_SAME_SECTOR = 3         # Max per sector
DEFAULT_MODE = "demo"       # Always default to demo

# Stop Loss (Trigger-based)
TRIGGER_CRITERIA = {
    "intraday_surge": {"sl_max": 0.05},  # -5%
    "volume_surge": {"sl_max": 0.07},    # -7%
    "default": {"sl_max": 0.07}          # -7%
}
```

## KR vs US Differences

| Item | KR | US |
|------|----|----|
| Data Source | pykrx, kospi_kosdaq MCP | yfinance, sec-edgar MCP |
| Market Hours | 09:00-15:30 KST | 09:30-16:00 EST |
| Market Cap Filter | 5000억 KRW | $20B USD |
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

## Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| `could not convert string to float: ''` | Fixed in v2.2 - use `_safe_float()` |
| Playwright PDF fails | `python3 -m playwright install chromium` |
| Korean fonts missing | `sudo dnf install google-nanum-fonts && fc-cache -fv` |
| KIS auth fails | Check `trading/config/kis_devlp.yaml` |
| prism-us import error | Use `_import_from_main_cores()` helper |
| Telegram message in English | v2.2.0 restored Korean templates - pull latest |
| Broadcast translation empty | gpt-5-mini fallback added in v2.2.0 |

## i18n Strategy (v2.2.0)

- **Code comments/logs**: English (for international collaboration)
- **Telegram messages**: Korean templates (default channel is KR)
- **Broadcast channels**: Translation agent converts to target language

```bash
# Default channel (Korean)
python stock_analysis_orchestrator.py --mode morning

# Broadcast to English channel
python stock_analysis_orchestrator.py --mode morning --broadcast-languages en
```

## Commit Convention

```
feat: New feature
fix: Bug fix
docs: Documentation
refactor: Code refactoring
test: Tests
```

## Detailed Documentation

For comprehensive guides, see:
- `docs/RELEASE_NOTES_v2.2.0.md` - Latest release notes
- `docs/US_STOCK_PLAN.md` - US module implementation details
- `docs/CLAUDE_AGENTS.md` - AI agent system documentation
- `docs/CLAUDE_TASKS.md` - Common development tasks
- `docs/CLAUDE_TROUBLESHOOTING.md` - Full troubleshooting guide
- `prism-us/IMPLEMENTATION_STATUS.md` - US module status

---

## Version History

| Ver | Date | Changes |
|-----|------|---------|
| 2.2.1 | 2026-02-06 | **문서화 & 스폰서 개선** - README에 Landing/Dashboard 섹션 추가, AI3 Platinum Sponsor 배지 (랜딩+대시보드), 프리미엄 스폰서 바 디자인, 쌍방향 링크 (Landing↔Dashboard) |
| 2.2.0 | 2026-02-04 | **코드베이스 영문화 + 텔레그램 한글 복구** - i18n (코드 주석/로그 영문화, 텔레그램 메시지 한글 유지), US holding decisions, demo.py, Product Hunt 랜딩, 다수 버그 수정 (31커밋, 155파일) |
| 2.1.1 | 2026-01-31 | KIS API 빈 문자열 버그 수정 - `_safe_float()`, `_safe_int()` 헬퍼, 예약주문 limit_price fallback |
| 2.1 | 2026-01-30 | 영문 PDF 회사명 누락 수정, gpt-5-mini 업그레이드 |
| 2.0 | 2026-01-29 | US Telegram 메시지 형식 통일 |
| 1.9 | 2026-01-28 | US 시총 필터 $20B, 대시보드 마켓 선택기 |

For full history, see git log.
