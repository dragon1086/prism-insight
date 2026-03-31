# CLAUDE.md - AI Assistant Guide for PRISM-INSIGHT

> **Version**: 2.9.0 | **Updated**: 2026-03-31

## Quick Overview

**PRISM-INSIGHT** = AI-powered Korean/US stock analysis & automated trading system

```yaml
Stack: Python 3.10+, mcp-agent, GPT-5/Claude 4.6, SQLite, Telegram, KIS API
Scale: ~75,000+ LOC, 13+ AI agents, KR/US dual market support
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

> US agents mirror KR under `prism-us/cores/agents/` (no Macro Intelligence, Trading Journal, Translation agents).

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
| `us_pending_orders` | US queued reserved orders (v2.7.1) |

## Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| `could not convert string to float: ''` | Fixed in v2.2 - use `_safe_float()` |
| Playwright PDF fails | `python3 -m playwright install chromium` |
| Korean fonts missing | `sudo dnf install google-nanum-fonts && fc-cache -fv` |
| KIS auth fails | Check `trading/config/kis_devlp.yaml` |
| prism-us import error | v2.9.0: `importlib.util` 기반 임포트로 해결됨. 직접 수정 시 `cores/openai_debug.py` 참고 |
| Telegram message in English | v2.2.0 restored Korean templates - pull latest |
| Broadcast translation empty | gpt-5-mini fallback added in v2.2.0 |
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

| Ver | Date | Changes |
|-----|------|---------|
| 2.9.0 | 2026-03-31 | **외부 기여 3종 + 매매 안정성 수정** - 다중 계좌 지원 (tkgo11, #228): 주·부계좌 병렬 팬아웃 + DB 마이그레이션, US 소셜 센티먼트 (alexander-schneider, #229): Adanos API 통합, US 모듈 네임스페이스 충돌 수정 (lifrary, #227): `importlib.util` 기반 임포트, KIS API 오류 3종 (APTR0057·APBK1234) + Telegram JSON sanitize + 손절 방어 강화 (#239), US 매도 ORD_DVSN 누락 수정 (#238), Telegram 타임아웃 지수 백오프 재시도 (#237), OpenAI 400 디버그 로깅 (#232) |
| 2.7.0 | 2026-03-24 | **ChatGPT OAuth Proxy + README 전면 업데이트** - ChatGPT Plus/Pro 구독으로 API 키 없이 분석 실행 가능 (`cores/chatgpt_proxy/`), Codex 엔드포인트 모델 매핑·SSE 파싱·response_format 변환 (#224), README 5개 언어 전면 개편 (모바일 앱·홍보영상·매매실적·Macro Intelligence 반영), 대시보드 스크린샷 교체 |
| 2.6.0 | 2026-03-12 | **거시경제 인텔리전스 + 하이브리드 종목선정 + 텔레그램 얼럿 강화** - Macro Intelligence 에이전트 도입 (시장 체제 판단, 주도/낙후 섹터 식별), 탑다운+바텀업 하이브리드 종목 선정 (#202), US score-decision override 버그 수정 (#203), US trigger results 파일 경로 통일 (#204), KR/US 텔레그램 시그널 얼럿에 시장국면·선정채널·점수/R·R/손절 정보 추가 + PDF 커버 날짜 regex 수정 (#205) |
| 2.5.2 | 2026-03-04 | **FCM NOT_FOUND 토큰 삭제 + Telegram Evaluator 다중 JSON 파싱 수정** - `firebase_bridge.py` `_INVALID_TOKEN_CODES`에 `NOT_FOUND` 추가 (만료 토큰 0/8 실패 반복 해결, #196), `telegram_summary_agent.py` GPT-5.x reasoning 모델 다중 JSON 응답 파싱 실패 → `_RobustEvaluatorLLM` 래퍼 + `generate_str()` fallback 추가 (#197) |
| 2.5.1 | 2026-02-22 | **Claude Sonnet 4.6 업그레이드** - `report_generator.py` 내 모델 `claude-sonnet-4-5-20250929` → `claude-sonnet-4-6` (5곳), knowledge cutoff Jan 2025 → Aug 2025 |
| 2.5.0 | 2026-02-22 | **Telegram /report 일일 횟수 환급 + 한국어 메시지 복원** - 서버 오류(서브프로세스 타임아웃, 내부 AI 에이전트 오류) 시 `/report`·`/us_report` 일일 사용 횟수 자동 환급 (`refund_daily_limit`, `_is_server_error` 추가, `send_report_result` 내 환급 처리), `AnalysisRequest`에 `user_id` 필드 추가, Telegram 봇 사용자 대면 메시지 한국어 템플릿 복원 |
| 2.4.9 | 2026-02-21 | **US 분석 버그 5종 수정** - `data_prefetch._df_to_markdown` tabulate 의존성 제거 (직접 마크다운 테이블 생성), `us_telegram_summary_agent` evaluator 프롬프트에 `needs_improvement` JSON 형식 명세 추가 + 평가 등급 0-3으로 정정 (Pydantic validation 오류 해결), `create_us_sell_decision_agent` US holding 매도 판단에 연결 (규칙 기반→AI 기반, fallback 유지), `redis_signal_publisher` 로그 KRW 하드코딩→`market` 필드 기반 USD/KRW 동적 출력, GCP Pub/Sub credentials 경로 로그 추가 + `GCP_CREDENTIALS_PATH` 미설정 경고 (401 진단 개선) |
| 2.4.8 | 2026-02-19 | **US 매수 가격 수정 + GCP 인증 + Firebase Bridge 타입 감지 버그 3종 수정** - `get_current_price()` KIS `last` 빈 문자열 시 `base`(전일종가) fallback 추가, `async_buy_stock()` KIS 가격 조회 실패 시 `limit_price` fallback (예약주문 보장), GCP Pub/Sub 401 → 명시적 `service_account.Credentials` 인증으로 전환, `detect_type()` 포트폴리오 키워드 구체화 (`포트폴리오 관점` 오탐 방지), `detect_type()` 트리거 키워드(`트리거/급등/급락/surge`) analysis 이전에 체크 (매수신호 포함 트리거 알림 정상 분류), `extract_title()` 파일경로 체크를 markdown 정리 이전으로 이동 (PDF 파일명 언더바 보존) |
| 2.4.7 | 2026-02-16 | **주간 리포트 확장 + 압축 후행평가** - 주간 매매 요약, 매도 후 평가, AI 장기 학습 인사이트, L1→L2 압축 후행 교훈, 다국어 broadcast 지원 |

For full history, see git log.
