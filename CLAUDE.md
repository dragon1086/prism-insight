# CLAUDE.md - AI Assistant Guide for PRISM-INSIGHT

> **Version**: 2.4.8 | **Updated**: 2026-02-19

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

## i18n Strategy (v2.2.0)

- **Code comments/logs**: English (for international collaboration)
- **Telegram messages**: Korean templates (default channel is KR)
- **Broadcast channels**: Translation agent converts to target language

```bash
# Default channel (Korean)
python stock_analysis_orchestrator.py --mode morning

# Broadcast to all language channels (non-blocking, parallel per language)
python stock_analysis_orchestrator.py --mode morning --broadcast-languages en,ja,zh,es
```

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

## Detailed Documentation

For comprehensive guides, see:
- `docs/US_STOCK_PLAN.md` - US module implementation details
- `docs/CLAUDE_AGENTS.md` - AI agent system documentation
- `docs/CLAUDE_TASKS.md` - Common development tasks
- `docs/CLAUDE_TROUBLESHOOTING.md` - Full troubleshooting guide

---

## Version History

| Ver | Date | Changes |
|-----|------|---------|
| 2.4.8 | 2026-02-19 | **US 매수 가격 수정 + GCP 인증 + Firebase Bridge 타입 감지 버그 3종 수정** - `get_current_price()` KIS `last` 빈 문자열 시 `base`(전일종가) fallback 추가, `async_buy_stock()` KIS 가격 조회 실패 시 `limit_price` fallback (예약주문 보장), GCP Pub/Sub 401 → 명시적 `service_account.Credentials` 인증으로 전환, `detect_type()` 포트폴리오 키워드 구체화 (`포트폴리오 관점` 오탐 방지), `detect_type()` 트리거 키워드(`트리거/급등/급락/surge`) analysis 이전에 체크 (매수신호 포함 트리거 알림 정상 분류), `extract_title()` 파일경로 체크를 markdown 정리 이전으로 이동 (PDF 파일명 언더바 보존) |
| 2.4.7 | 2026-02-16 | **주간 리포트 확장 + 압축 후행평가** - 주간 매매 요약 섹션 (매수 수익률, 매도 실적), 매도 후 평가 (현재가 비교 → 잘 팔았다/더 기다릴 수 있었다), AI 장기 학습 인사이트 (trading_intuitions 반영), L1→L2 압축 시 현재가 비교 후행 교훈 자동 생성 (KR: pykrx, US: yfinance), 주간 리포트 `--broadcast-languages` 다국어 broadcast 지원, `/triggers` 참조 제거 |
| 2.4.6 | 2026-02-12 | **US 트레이딩 에이전트 신호 체계 정비** - language 기본값 `"en"`→`"ko"` 통일 (prism-us 전체 11파일), KO↔EN 프롬프트 동기화 (진입 기준 완화, 점수 정의 등 6항목), 미국 시장에 맞지 않는 기관 수급/13F 신호 제거, Form 4 내부자 신호 제거 (perplexity 웹검색 비신뢰), 애널리스트 투자의견 제거 (후행 지표+sell-side 편향), 모든 매매 신호를 yahoo_finance 가격/거래량 기반으로 통일 (O'Neil CAN SLIM 원칙) |
| 2.4.5 | 2026-02-11 | **Firebase notify 논블로킹 + Broadcast-Tracking 동시 실행** - Firebase `_notify_firebase()` 인라인 `await` → `_schedule_firebase()` 태스크로 변경 후 메서드 끝에서 `asyncio.gather()` 수거 (Telegram 전송 블로킹 제거), 오케스트레이터 step 5-1 제거 (broadcast 완료 대기 → tracking 즉시 시작, broadcast는 async I/O로 동시 실행), `finally` 블록이 broadcast 완료 보장 (KR/US 공통) |
| 2.4.4 | 2026-02-11 | **Tracking 시스템 Firebase Bridge 통합** - stock_tracking_agent(KR)·us_stock_tracking_agent(US) 전체 메시지 전송 포인트에 `firebase_bridge.notify()` 추가 (매수/매도/보류/포트폴리오/요약 메시지 + 다국어 브로드캐스트 채널), Prism Mobile 푸시 알림 지원, `_notify_firebase()` 헬퍼 메서드 (KR: market="kr", US: market="us"), 분할 메시지 시 첫 파트 message_id로 deep link 생성 |
| 2.4.3 | 2026-02-11 | **Broadcast PDF 순차 처리** - `_send_translated_pdfs` 언어별 병렬→순차 처리 (Playwright 동시 실행 OOM 방지), finally 블록 안전망 유지 (KR/US 공통) |
| 2.4.2 | 2026-02-11 | **Broadcast finally 블록 수정 + 언어별 병렬 처리** - broadcast gather를 `finally` 블록으로 이동 (early return/예외 시에도 반드시 대기), `_send_translated_messages`·`_send_translated_pdfs`·`_send_translated_trigger_alert` 언어별 `asyncio.gather()` 병렬 처리 (KR/US 6개 메서드), 다국어 채널 PDF 생성 중단 버그 해결 |
| 2.4.1 | 2026-02-11 | **다국어 채널 + Broadcast 논블락킹 + US 진입전략 수정** - KR/US 결정문자열 정규화(`_normalize_decision`), US 0% 진입률 버그 수정 (bull detection 완화, score 6 정의 변경, min_score 하향, score-decision 일관성 강제), KR score-decision 일관성 강제 추가, Broadcast 번역 논블락킹 전환 (fire-and-forget + 파이프라인 끝 수거), 언어별 병렬 번역 (`asyncio.gather`), ja/zh/es 다국어 텔레그램 채널 지원, Firebase Bridge lang 필드 추가 |
| 2.4.0 | 2026-02-10 | **직접 API prefetch + 트리거 신뢰도 카드 + Firebase Bridge** - pykrx(KR)/yfinance(US) 직접 호출로 MCP·Firecrawl·Perplexity 호출 대폭 절감 (API 비용 KR ~50%, US ~30% 절감), 트리거별 A/B/C/D 신뢰도 등급 카드 (대시보드+텔레그램+주간리포트), Firebase Bridge opt-in 모듈 (PRISM-Mobile 연동 기반), /triggers 명령어 및 주간 인사이트 리포트 추가 |
| 2.3.0 | 2026-02-07 | **자기개선 매매 피드백 루프 완성** - Performance Tracker 데이터 KR/US 공통 반영, LLM 노이즈 제거, US 대시보드 AI보유 분석 수정, 보편 원칙 필터 강화 (supporting_trades>=2, LIMIT 5), 보안 강화, 스폰서 배지 |
| 2.2.2 | 2026-02-07 | **Performance Tracker 피드백 루프 정리** - LLM 프롬프트에서 missed_opportunities/traded_vs_watched 제거 (편향 방지), US _extract_trading_scenario에 journal context 주입, KR trigger_type 전달 수정, 자기개선 매매 문서화 |
| 2.2.0 | 2026-02-04 | **코드베이스 영문화 + 텔레그램 한글 복구** - i18n (코드 주석/로그 영문화, 텔레그램 메시지 한글 유지), US holding decisions, demo.py, Product Hunt 랜딩, 다수 버그 수정 (31커밋, 155파일) |
| 2.1.1 | 2026-01-31 | KIS API 빈 문자열 버그 수정 - `_safe_float()`, `_safe_int()` 헬퍼, 예약주문 limit_price fallback |
| 2.1 | 2026-01-30 | 영문 PDF 회사명 누락 수정, gpt-5-mini 업그레이드 |
| 2.0 | 2026-01-29 | US Telegram 메시지 형식 통일 |
| 1.9 | 2026-01-28 | US 시총 필터 $20B, 대시보드 마켓 선택기 |

For full history, see git log.
