# PRISM-INSIGHT US Stock Version Implementation Plan

## 프로젝트 개요

**목표**: 기존 한국 주식 분석 시스템과 동일한 워크플로우를 가진 미국 주식 버전 개발
**개발 기간**: 8 Phase, 약 7일 (세션 기반 분할 작업)
**원칙**: 한국 버전과 독립적인 코드베이스, 동일 SQLite DB 공유 (테이블 분리)

### 확정된 결정사항
- **Trading API**: KIS 해외주식 API (한국투자증권, 기존 인프라 활용)
- **Telegram**: 한국어 채널 기본 + `--broadcast-languages en` 옵션으로 영어 채널 번역 발송 (기존 패턴 재사용)
- **코드 구조**: 공통 유틸리티 이동 없이 기존 위치 유지, US에서 import해서 사용
- **Web Scraping**: Firecrawl MCP 적극 활용 (SEC Edgar, Yahoo Finance 등)

---

## 0. 사전 툴 테스트 결과 (2026-01-17)

### ✅ yfinance (Primary Data Source)

| 기능 | 테스트 결과 | 비고 |
|------|------------|------|
| OHLCV 데이터 | ✅ 정상 | `ticker.history(period="10d")` |
| 기업 정보 | ✅ 정상 | `ticker.info` (sector, industry, market cap 등) |
| 재무제표 | ✅ 정상 | `ticker.financials`, `ticker.balance_sheet` |
| **기관투자자 보유** | ✅ **무료** | `ticker.institutional_holders` (Vanguard, Blackrock 등) |
| 대주주 지분 | ✅ 정상 | `ticker.major_holders` |

**결론**: yfinance가 Finnhub 유료 기능을 대체 가능 → **Primary 데이터 소스로 확정**

### ⚠️ Finnhub (Supplementary)

| 기능 | 테스트 결과 | 비고 |
|------|------------|------|
| 기업 프로필 | ✅ 정상 | `company_profile2()` |
| SEC 공시 | ✅ 정상 | 무료 티어 포함 |
| **기관투자자 보유** | ❌ **403 에러** | Premium 전용 ($49+/월) |

**무료 티어 제한**:
- 60 API calls/minute
- Personal use only (상업용 불가)
- 기관투자자 데이터 접근 불가

**결론**: 보조 데이터 소스로 사용, 기관투자자 데이터는 yfinance 사용

### ✅ pandas-market-calendars (Holiday Detection)

테스트 완료된 미국 휴일:
- Martin Luther King Jr. Day (1월 셋째 월요일)
- Presidents Day (2월 셋째 월요일)
- Good Friday
- Memorial Day (5월 마지막 월요일)
- Independence Day (7월 4일)
- Labor Day (9월 첫째 월요일)
- Thanksgiving (11월 넷째 목요일)
- Christmas (12월 25일)

**결론**: `check_market_day.py`에 사용 확정

### ✅ yfinance-mcp (npm package)

- **패키지명**: `yfinance-mcp@1.0.5`
- **상태**: npm에서 설치 가능
- **용도**: MCP 서버로 yfinance 데이터 제공

**결론**: Phase 2에서 설치 및 테스트

---

## 1. 디렉토리 구조

```
prism-insight/
├── prism-us/                          # US Market Module (NEW)
│   ├── __init__.py
│   ├── orchestrator.py                # US main pipeline
│   ├── trigger_batch.py               # US surge detection
│   ├── tracking_agent.py              # US trading simulation
│   ├── check_market_day.py            # US market holiday validation
│   │
│   ├── cores/                         # US-specific core logic
│   │   ├── __init__.py
│   │   ├── analysis.py
│   │   ├── report_generation.py
│   │   ├── stock_chart.py
│   │   ├── us_data_client.py          # Unified data interface
│   │   └── agents/
│   │       ├── __init__.py
│   │       ├── stock_price_agents.py  # yfinance/polygon-based
│   │       ├── company_info_agents.py # SEC filings
│   │       ├── news_strategy_agents.py
│   │       ├── market_index_agents.py # S&P500, NASDAQ, Dow
│   │       └── trading_agents.py
│   │
│   ├── trading/                       # US Trading System (KIS 해외주식)
│   │   ├── __init__.py
│   │   ├── us_stock_trading.py        # KIS 해외주식 API wrapper
│   │   └── config/
│   │       └── kis_us.yaml.example    # 기존 kis_devlp.yaml 확장
│   │
│   ├── tracking/                      # US tracking helpers
│   │   ├── __init__.py
│   │   ├── db_schema.py               # us_* prefix tables
│   │   └── helpers.py
│   │
│   ├── tests/                         # US-specific tests
│   │   └── ...
│   │
│   ├── IMPLEMENTATION_STATUS.md       # Progress tracking
│   └── README.md
│
├── cores/                             # Korean (existing, unchanged)
├── trading/                           # Korean trading (existing)
├── pdf_converter.py                   # Shared (import from prism-us)
├── telegram_config.py                 # Shared (import from prism-us)
└── stock_analysis_orchestrator.py     # Korean orchestrator
```

---

## 2. MCP Server 전략

### 선정된 MCP 서버 (테스트 결과 반영)

| 용도 | Primary | Fallback | API Key | 비고 |
|------|---------|----------|---------|------|
| OHLCV 데이터 | **yfinance** | Polygon MCP | 무료 / 유료 | ✅ 테스트 완료 |
| 기업 재무 | **yfinance** | Finnhub | 무료 | ✅ 테스트 완료 |
| 기관투자자 보유 | **yfinance** | - | **무료** | ✅ Finnhub 유료 대체 |
| 뉴스/리서치 | Perplexity | (기존 공유) | 기존 | - |
| SEC 공시 | Firecrawl | Finnhub | 기존 / 무료 | - |
| 시장 지수 | **yfinance** | - | 무료 | ✅ 테스트 완료 |
| **웹 스크래핑** | **Firecrawl** | - | **기존** | - |

> **핵심 변경**: yfinance가 기관투자자 데이터를 무료로 제공하므로 Finnhub Premium 불필요

### Firecrawl 활용 계획

| 스크래핑 대상 | URL 패턴 | 용도 |
|-------------|----------|------|
| SEC Edgar | `sec.gov/cgi-bin/browse-edgar` | 10-K, 10-Q, 8-K 공시 |
| Yahoo Finance | `finance.yahoo.com/quote/{ticker}` | 기업 개요, 재무 요약 |
| Seeking Alpha | `seekingalpha.com/symbol/{ticker}` | 애널리스트 의견 |
| MarketWatch | `marketwatch.com/investing/stock/{ticker}` | 뉴스, 실적 |
| Finviz | `finviz.com/quote.ashx?t={ticker}` | 기술적 지표, 스크리너 |

### mcp_agent.config.yaml 추가 내용

```yaml
mcp:
  servers:
    # 기존 서버 (공유)
    firecrawl: firecrawl-mcp          # 웹 스크래핑 (기존)
    perplexity: node perplexity-ask/dist/index.js  # 리서치 (기존)

    # US Market Servers (신규)
    yfinance_us:
      command: "python3"
      args: ["-m", "yfinance_mcp_server"]

    finnhub_us:
      command: "npx"
      args: ["-y", "mcp-finnhub"]
      env:
        FINNHUB_API_KEY: "${FINNHUB_API_KEY}"

    polygon_us:  # Optional for production
      command: "npx"
      args: ["-y", "@anthropic/polygon-mcp"]
      env:
        POLYGON_API_KEY: "${POLYGON_API_KEY}"
```

---

## 3. 데이터베이스 설계

### 신규 US 테이블 (us_ prefix)

```sql
-- us_stock_holdings: 현재 보유 종목
CREATE TABLE us_stock_holdings (
    ticker TEXT PRIMARY KEY,           -- AAPL, MSFT
    company_name TEXT NOT NULL,
    buy_price REAL NOT NULL,           -- USD
    buy_date TEXT NOT NULL,
    current_price REAL,
    scenario TEXT,                     -- JSON
    target_price REAL,
    stop_loss REAL,
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT                        -- GICS sector
);

-- us_trading_history: 거래 이력
CREATE TABLE us_trading_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    buy_price REAL, buy_date TEXT,
    sell_price REAL, sell_date TEXT,
    profit_rate REAL,
    holding_days INTEGER,
    scenario TEXT,
    trigger_type TEXT, trigger_mode TEXT
);

-- us_watchlist_history: 분석했지만 미진입
CREATE TABLE us_watchlist_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT, company_name TEXT,
    analyzed_date TEXT,
    buy_score INTEGER, decision TEXT,
    skip_reason TEXT,
    scenario TEXT,
    trigger_type TEXT, trigger_mode TEXT
);
```

### 공유 테이블 수정 (market 컬럼 추가)

```sql
ALTER TABLE trading_journal ADD COLUMN market TEXT DEFAULT 'KR';
ALTER TABLE trading_principles ADD COLUMN market TEXT DEFAULT 'KR';
ALTER TABLE trading_intuitions ADD COLUMN market TEXT DEFAULT 'KR';
```

---

## 4. 8 Phase 개발 계획

### Phase 1: Foundation Setup (Day 1) ✅ COMPLETED
**목표**: 디렉토리 구조, 설정 파일

**작업**:
1. ✅ `prism-us/` 디렉토리 구조 생성
2. ✅ `prism-us/__init__.py` 생성
3. ✅ `prism-us/check_market_day.py` - NYSE/NASDAQ 휴일 체커 (pandas-market-calendars 사용)
4. ✅ `prism-us/IMPLEMENTATION_STATUS.md` 생성
5. ✅ `.env.example`에 US 섹션 추가 (FINNHUB_API_KEY)
6. ✅ `requirements.txt`에 신규 패키지 추가 (yfinance, pandas-market-calendars)
7. ✅ `docs/US_STOCK_PLAN.md` 생성

**검증**:
```bash
python prism-us/check_market_day.py
# US 휴일 감지 확인 (MLK Day, Independence Day 등)
```

---

### Phase 2: MCP Server Integration (Day 1-2) ✅ COMPLETED (2026-01-17)
**목표**: US 데이터 MCP 서버 설치 및 테스트

**완료된 작업**:
1. ✅ yfinance-mcp 서버 설치 (`npm install -g yfinance-mcp`)
2. ✅ Finnhub: finnhub-python 라이브러리로 대체 (MCP 서버 없음)
3. ✅ `prism-us/cores/us_data_client.py` 생성 - 통합 데이터 클라이언트
4. ✅ 데이터 조회 테스트 완료 (AAPL, 시장지수)
5. ✅ `mcp_agent.config.yaml`에 yfinance_us 서버 추가

**검증 완료**:
```
AAPL OHLCV: 10 records, Company: Apple Inc., $3.8T market cap
Institutional Holders: Vanguard, Blackrock, State Street 등 10개 기관
Market Indices: S&P 500 $6,949, Dow $49,424, NASDAQ $23,537
```

---

### Phase 3: Database Schema (Day 2) ✅ COMPLETED (2026-01-17)
**목표**: US 테이블 정의 및 마이그레이션

**완료된 작업**:
1. ✅ `prism-us/tracking/db_schema.py` 생성
2. ✅ US 테이블 4개 생성: us_stock_holdings, us_trading_history, us_watchlist_history, us_analysis_performance_tracker
3. ✅ US 인덱스 10개 생성
4. ✅ 공유 테이블 market 컬럼 마이그레이션 (trading_journal, trading_principles, trading_intuitions)
5. ✅ Production DB 테스트 완료

**검증 완료**:
```
US Tables: 4개 생성 완료
US Indexes: 10개 생성 완료
Shared Tables: market 컬럼 추가 완료
```

---

### Phase 4: Core Agents Adaptation (Day 2-3)
**목표**: US 시장용 에이전트 프롬프트 작성

**변경 우선순위**:
| 에이전트 | 변경량 | 주요 변경 |
|---------|-------|----------|
| stock_price_agents | HIGH | kospi_kosdaq → yfinance_us |
| company_info_agents | HIGH | WiseReport → SEC Edgar |
| market_index_agents | HIGH | KOSPI → S&P500, NASDAQ |
| trading_agents | MEDIUM | 시장 시간, 손절 기준 |
| news_strategy_agents | LOW | 프롬프트 영어화 |

**작업**:
1. `prism-us/cores/agents/stock_price_agents.py`
2. `prism-us/cores/agents/company_info_agents.py`
3. `prism-us/cores/agents/market_index_agents.py`
4. `prism-us/cores/agents/trading_agents.py`
5. `prism-us/cores/agents/news_strategy_agents.py`
6. `prism-us/cores/agents/__init__.py`

**검증**:
```python
from prism_us.cores.agents import get_agent_directory
agents = get_agent_directory("Apple Inc.", "AAPL", "20250114", ["price_volume_analysis"])
```

---

### Phase 5: Trigger Batch System (Day 3-4)
**목표**: US 급등주 탐지 시스템

**한국 vs 미국 차이**:
| 항목 | 한국 | 미국 |
|------|-----|------|
| 데이터 소스 | pykrx/KRX | yfinance |
| 시총 필터 | 5000억 KRW | $20B USD |
| 거래 시간 | 09:00-15:30 KST | 09:30-16:00 EST |
| 서킷브레이커 | ±30% | 7%/13%/20% |

**작업**:
1. `prism-us/trigger_batch.py` 생성
2. 6개 트리거 유형 적용
3. Agent fit 메트릭 계산
4. JSON 출력 형식 일치

**검증**:
```bash
python prism-us/trigger_batch.py morning INFO --output us_trigger.json
```

---

### Phase 6: Trading System (Day 4-5)
**목표**: KIS 해외주식 API 연동

**KIS 해외주식 API 특징**:
- 기존 `trading/domestic_stock_trading.py` 구조 재활용
- 해외주식 전용 엔드포인트 사용 (`/uapi/overseas-stock/`)
- 환율 처리 필요 (USD ↔ KRW)
- 미국 시장 시간 고려 (09:30-16:00 EST, 한국시간 23:30-06:00)

**작업**:
1. `prism-us/trading/us_stock_trading.py` (기존 domestic 참조)
2. `prism-us/trading/config/kis_us.yaml.example`
3. 모의투자 테스트
4. 환율 조회 및 변환 로직

**검증**:
```python
from prism_us.trading.us_stock_trading import USStockTrading

async with USStockTrading(mode="demo") as trader:
    result = await trader.buy_stock("AAPL", amount=100)  # USD
    portfolio = await trader.get_portfolio()
    print(f"Portfolio: {portfolio}")
```

---

### Phase 7: Orchestrator & Pipeline (Day 5-6)
**목표**: 메인 파이프라인 통합

**작업**:
1. `prism-us/orchestrator.py`
2. `prism-us/cores/analysis.py`
3. `prism-us/cores/report_generation.py`
4. `prism-us/tracking_agent.py`
5. End-to-end 테스트

**검증**:
```bash
python prism-us/orchestrator.py --mode morning --no-telegram
# reports/us_AAPL_*.md 생성 확인
```

---

### Phase 8: Testing & Documentation (Day 6-7)
**목표**: 테스트 및 문서화

**작업**:
1. `prism-us/tests/` 유닛 테스트
2. 통합 테스트
3. `prism-us/README.md`
4. `CLAUDE.md` US 섹션 추가

**검증**:
```bash
python -m pytest prism-us/tests/ -v
python prism-us/orchestrator.py --mode both --no-telegram
```

---

## 5. 컨텍스트 관리

### Progress Tracking: `prism-us/IMPLEMENTATION_STATUS.md`

각 세션 종료 시 업데이트:
```markdown
# PRISM-INSIGHT US Implementation Status

## Current Phase: [1-8]
## Last Updated: YYYY-MM-DD HH:MM

### Completed
- [x] Phase 1: Foundation Setup (2026-01-17)
- [ ] Phase 2: MCP Server Integration

### In Progress
- Phase 2: 50% complete
  - [x] yfinance MCP 설치
  - [ ] Finnhub MCP 설치
  - [ ] us_data_client.py

### Blockers
- None

### Next Steps
1. Finnhub MCP 설치
2. us_data_client.py 완성

### Files Modified This Session
- prism-us/cores/us_data_client.py (new)

### Notes for Next Session
- Finnhub API key 필요
```

---

## 6. 검증 체크리스트

| Phase | 테스트 | 예상 결과 |
|-------|-------|----------|
| 1 | `python prism-us/check_market_day.py` | US 휴일 감지 |
| 2 | AAPL OHLCV 조회 | DataFrame 반환 |
| 3 | 테이블 생성 쿼리 | us_* 테이블 존재 |
| 4 | Agent 초기화 | 에러 없음 |
| 5 | Trigger batch 실행 | JSON 출력 |
| 6 | Paper trade 테스트 | 주문 성공 |
| 7 | 전체 파이프라인 | 리포트 생성 |
| 8 | pytest | 전체 통과 |

---

## 7. 의존성

### 신규 Python 패키지
```
yfinance>=0.2.0              # US 주가 데이터
pandas-market-calendars      # US 시장 휴일
pytz                         # 시간대 처리
exchange-calendars           # 거래소 캘린더 (선택)
```

### 기존 패키지 (이미 설치됨)
```
requests                     # KIS API 호출
pyyaml                       # 설정 파일
aiosqlite                    # 비동기 DB
```

### 신규 MCP 서버
```
yfinance-mcp-server (npm or pip)  # US OHLCV
mcp-finnhub (npm)                 # 기업 재무, SEC
@anthropic/polygon-mcp (optional) # 프로덕션용 (유료)
```

### 기존 MCP 서버 (공유)
```
firecrawl-mcp                # 웹 스크래핑 (SEC Edgar, Yahoo Finance)
perplexity-ask               # 뉴스/리서치
```

---

## 8. 핵심 참조 파일

### 미러링 대상 (한국 버전)
| 한국 파일 | US 파일 | 비고 |
|----------|---------|------|
| `stock_analysis_orchestrator.py` | `prism-us/orchestrator.py` | 구조 동일 |
| `trigger_batch.py` | `prism-us/trigger_batch.py` | 데이터소스 변경 |
| `cores/analysis.py` | `prism-us/cores/analysis.py` | MCP 서버 변경 |
| `cores/agents/*.py` | `prism-us/cores/agents/*.py` | 프롬프트 변경 |
| `trading/domestic_stock_trading.py` | `prism-us/trading/us_stock_trading.py` | KIS 해외주식 API |
| `tracking/db_schema.py` | `prism-us/tracking/db_schema.py` | us_ prefix |

### 공유 자원 (import해서 사용)
| 파일 | 용도 |
|------|------|
| `trading/kis_auth.py` | KIS 인증 (국내/해외 공용) |
| `pdf_converter.py` | PDF 변환 |
| `telegram_config.py` | 텔레그램 설정 |
| `report_generator.py` | MCPApp 관리 |

---

## 9. 주요 차이점 요약

| 항목 | 한국 | 미국 |
|------|-----|------|
| 데이터 소스 | pykrx, kospi_kosdaq MCP | yfinance, finnhub, firecrawl MCP |
| Trading API | KIS 국내주식 API | KIS 해외주식 API (동일 인프라) |
| 시장 시간 | 09:00-15:30 KST | 09:30-16:00 EST (KST 23:30-06:00) |
| 시총 필터 | 5000억 KRW | $20B USD |
| 지수 | KOSPI, KOSDAQ | S&P500, NASDAQ, Dow |
| 투자자 수급 | 기관/외인/개인 실시간 | SEC 13F (분기별) |
| 웹 스크래핑 | WiseReport (firecrawl) | SEC Edgar, Yahoo Finance (firecrawl) |
| Telegram | 한국어 채널 + broadcast | 한국어 채널 + broadcast (동일 패턴) |
| DB 테이블 | stock_holdings | us_stock_holdings |
| 통화 | KRW | USD (환율 변환 필요) |
