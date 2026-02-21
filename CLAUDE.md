# CLAUDE.md - AI Assistant Guide for PRISM-INSIGHT

> **Version**: 2.5.0 | **Updated**: 2026-02-22

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
| `python weekly_insight_report.py --dry-run` | Weekly insight report (print only) |
| `python weekly_insight_report.py --broadcast-languages en,ja` | Weekly report + broadcast |

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
| `/report` 오류 후 재사용 불가 | v2.5.0 수정 - 서버 오류 시 자동 환급됨, 재시도 가능 |

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

| Ver | Date | Changes |
|-----|------|---------|
| 2.5.0 | 2026-02-22 | **Telegram /report 일일 횟수 환급 + 한국어 메시지 복원** - 서버 오류(서브프로세스 타임아웃, 내부 AI 에이전트 오류) 시 `/report`·`/us_report` 일일 사용 횟수 자동 환급 (`refund_daily_limit`, `_is_server_error` 추가, `send_report_result` 내 환급 처리), `AnalysisRequest`에 `user_id` 필드 추가, Telegram 봇 사용자 대면 메시지 한국어 템플릿 복원 |
| 2.4.9 | 2026-02-21 | **US 분석 버그 5종 수정** - `data_prefetch._df_to_markdown` tabulate 의존성 제거 (직접 마크다운 테이블 생성), `us_telegram_summary_agent` evaluator 프롬프트에 `needs_improvement` JSON 형식 명세 추가 + 평가 등급 0-3으로 정정 (Pydantic validation 오류 해결), `create_us_sell_decision_agent` US holding 매도 판단에 연결 (규칙 기반→AI 기반, fallback 유지), `redis_signal_publisher` 로그 KRW 하드코딩→`market` 필드 기반 USD/KRW 동적 출력, GCP Pub/Sub credentials 경로 로그 추가 + `GCP_CREDENTIALS_PATH` 미설정 경고 (401 진단 개선) |
| 2.4.8 | 2026-02-19 | **US 매수 가격 수정 + GCP 인증 + Firebase Bridge 타입 감지 버그 3종 수정** - `get_current_price()` KIS `last` 빈 문자열 시 `base`(전일종가) fallback 추가, `async_buy_stock()` KIS 가격 조회 실패 시 `limit_price` fallback (예약주문 보장), GCP Pub/Sub 401 → 명시적 `service_account.Credentials` 인증으로 전환, `detect_type()` 포트폴리오 키워드 구체화 (`포트폴리오 관점` 오탐 방지), `detect_type()` 트리거 키워드(`트리거/급등/급락/surge`) analysis 이전에 체크 (매수신호 포함 트리거 알림 정상 분류), `extract_title()` 파일경로 체크를 markdown 정리 이전으로 이동 (PDF 파일명 언더바 보존) |
| 2.4.7 | 2026-02-16 | **주간 리포트 확장 + 압축 후행평가** - 주간 매매 요약, 매도 후 평가, AI 장기 학습 인사이트, L1→L2 압축 후행 교훈, 다국어 broadcast 지원 |

For full history, see git log.
