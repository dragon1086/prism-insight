# PRISM-INSIGHT v2.0.0

발표일: 2026년 1월 28일

## 개요

PRISM-INSIGHT v2.0.0은 **미국 주식 분석 시스템(prism-us)**을 추가한 메이저 버전입니다. 한국 주식과 동일한 AI 기반 분석 파이프라인을 미국 시장(NYSE, NASDAQ)에 적용하여 S&P 500 및 NASDAQ-100 종목의 급등주 탐지, 심층 분석 리포트 생성, 자동 매매를 지원합니다.

**주요 수치:**
- 총 26개 커밋
- 79개 파일 변경
- +24,098 / -742 라인
- prism-us 모듈: ~22,000 LOC
- 221개 테스트 (97% pass rate)

---

## 주요 변경사항

### 1. 미국 주식 분석 시스템 (prism-us)

한국 주식 분석 시스템과 동일한 워크플로우를 가진 **미국 주식 버전**을 완전히 새롭게 구현했습니다.

#### 1.1 시스템 아키텍처

```
prism-us/
├── cores/                          # 분석 엔진
│   ├── agents/                    # 6개 분석 에이전트 + 2개 트레이딩 에이전트
│   ├── us_analysis.py             # 메인 분석 오케스트레이션
│   ├── us_data_client.py          # 통합 데이터 클라이언트
│   ├── us_stock_chart.py          # 차트 생성 모듈
│   └── us_surge_detector.py       # 급등주 탐지 모듈
├── trading/
│   └── us_stock_trading.py        # KIS 해외주식 API 래퍼
├── tracking/
│   ├── db_schema.py               # US 테이블 스키마
│   ├── journal.py                 # 트레이딩 저널 매니저
│   └── compression.py             # 메모리 압축 매니저
├── us_stock_analysis_orchestrator.py  # 메인 파이프라인
├── us_stock_tracking_agent.py     # 트레이딩 시뮬레이션
├── us_telegram_summary_agent.py   # 텔레그램 요약 생성
├── us_trigger_batch.py            # 급등주 탐지 배치
└── us_performance_tracker_batch.py # 성과 추적 배치
```

#### 1.2 MCP 서버 통합

| MCP 서버 | 용도 | 비용 |
|----------|------|------|
| `yahoo-finance-mcp` | OHLCV, 회사정보, 재무제표, 기관 보유 | 무료 (PyPI) |
| `sec-edgar-mcp` | SEC 공시, XBRL 재무제표, 내부자 거래 | 무료 (PyPI) |
| `firecrawl` | 웹 스크래핑 (Yahoo Finance 페이지) | API 키 필요 |
| `perplexity` | AI 검색 (뉴스, 산업 분석) | API 키 필요 |

**uvx 원격 실행 방식**으로 변경하여 로컬 설치가 불필요합니다:

```yaml
# mcp_agent.config.yaml
yahoo_finance:
  command: "uvx"
  args: ["--from", "yahoo-finance-mcp", "yahoo-finance-mcp"]

sec_edgar:
  command: "uvx"
  args: ["--from", "sec-edgar-mcp", "sec-edgar-mcp"]
```

#### 1.3 급등주 탐지 기준

| 조건 | 기준 |
|------|------|
| 시가총액 | $5B USD 이상 |
| 일일 거래량 | 20일 평균 대비 200%+ |
| 일중 상승률 | 5%+ |
| 갭 상승 | 3%+ |
| 대상 종목 | S&P 500 + NASDAQ-100 (약 550개) |

#### 1.4 트레이딩 시스템

- **KIS 해외주식 API** 연동 (데모/실거래 모드)
- **예약 주문 지원**: 장외 시간(10:00-23:20 KST)에 다음 개장 시 주문 예약
- **스마트 주문**: 장중/장외 자동 판별하여 적절한 주문 방식 선택
- **포트폴리오 관리**: 최대 10종목, 섹터 집중도 30% 제한

```python
# 예약 주문 예시
await trader.smart_buy("AAPL", 50000)  # 장중: 즉시 주문, 장외: 예약 주문
```

#### 1.5 성과 추적 시스템

분석된 종목의 7/14/30일 후 성과를 자동 추적합니다:

```python
# prism-us/us_performance_tracker_batch.py
python prism-us/us_performance_tracker_batch.py --verbose
```

| 필드 | 설명 |
|------|------|
| `day7_return` | 분석 7일 후 수익률 |
| `day14_return` | 분석 14일 후 수익률 |
| `day30_return` | 분석 30일 후 수익률 |
| `tracking_status` | 추적 상태 (pending/partial/complete) |
| `was_traded` | 실제 매매 여부 |

---

### 2. 텔레그램 봇 기능 확장

#### 2.1 미국 주식 명령어 추가

| 명령어 | 설명 |
|--------|------|
| `/us_evaluate` | 미국 주식 보유 종목 평가 |
| `/us_report` | 미국 주식 분석 보고서 요청 |

**사용 예시:**
```
User: /us_evaluate
Bot:  미국 주식 티커를 입력해주세요 (예: AAPL, MSFT)

User: AAPL
Bot:  매수 평균가를 달러로 입력해주세요 (예: 150.50)

User: 175
Bot:  [AI 분석 결과]
      📊 Apple Inc. (AAPL) 평가
      ...
```

#### 2.2 투자 일기 시스템 (/journal)

사용자별 투자 생각을 기록하고, 이후 평가 시 컨텍스트로 활용합니다:

```
User: /journal
Bot:  📝 투자 일기를 작성해주세요.

User: AAPL AI 테마로 더 갈 것 같다. 170달러까지 홀딩 예정

Bot:  ✅ 저널에 기록했습니다!
      📝 종목: Apple Inc. (AAPL)
      💭 "AI 테마로 더 갈 것 같다..."
      💡 이 메시지에 답장하여 추가 기록 가능!
```

**기억 시스템 특징:**
- **3단계 압축**: 상세(0-7일) → 요약(8-30일) → 한줄(31일+)
- **토큰 예산 관리**: 최대 2000 토큰
- **종목별 우선순위**: 해당 종목 기록 우선 로드
- **야간 배치 압축**: 매일 새벽 3시 자동 실행

#### 2.3 /cancel 명령어 개선

모든 대화 상태에서 `/cancel` 명령어가 정상 작동하도록 수정했습니다.

---

### 3. PDF 보고서 Prism Light 테마

독창적인 **스펙트럼 컬러 테마**를 적용한 PDF 보고서 디자인:

| 요소 | 스타일 |
|------|--------|
| H1 제목 | 그라데이션 배경 (#667eea → #764ba2) |
| H2 섹션 | 좌측 보라색 보더 (#8B5CF6) |
| H3 소제목 | 인디고 텍스트 (#6366F1) |
| 박스 | 그라데이션 보더 (투명 배경) |
| 표 헤더 | 그라데이션 배경 |
| 차트 | 보라-인디고 계열 색상 |

**마크다운 제목 계층 구조 통일:**
- KR/US 모듈 동일한 H1 → H2 → H3 구조
- HTML 템플릿 자동 매핑

---

### 4. 트레이딩 저널 시스템

매매 결정의 회고와 학습을 위한 저널 시스템:

#### 4.1 저널 구조

```python
trading_journal (
    ticker, trade_type, trade_date,
    situation_analysis,    # 상황 분석
    judgment_evaluation,   # 판단 평가
    lessons,               # 교훈
    pattern_tags,          # 패턴 태그
    one_line_summary,      # 한줄 요약
    confidence_score       # 신뢰도 점수
)
```

#### 4.2 원칙 추출

저널에서 반복되는 패턴을 **트레이딩 원칙**으로 추출:

```python
trading_principles (
    scope,           # universal/market/sector
    condition,       # 조건
    action,          # 행동
    reason,          # 이유
    confidence,      # 신뢰도
    supporting_trades  # 근거 거래 수
)
```

#### 4.3 점수 조정

과거 교훈을 바탕으로 매수 점수를 자동 조정:

```python
# 에이전트 점수 7점 + 과거 교훈 보정 +1점 = 최종 8점
final_score = agent_score + lesson_adjustment
```

---

### 5. 예약 주문 지원 (Reserved Order)

장외 시간에도 다음 개장 시 주문을 예약할 수 있습니다:

#### 5.1 한국 주식

```python
# trading/domestic_stock_trading.py
await trader.buy_reserved_order("005930", 50000, limit_price=75000)
await trader.sell_all_reserved_order("005930", limit_price=80000)
```

#### 5.2 미국 주식

```python
# prism-us/trading/us_stock_trading.py
await trader.buy_reserved_order("AAPL", 100000)  # USD 기준
await trader.sell_reserved_order("AAPL", 10, order_type="MOO")  # Market On Open
```

**예약 주문 가능 시간:**
- 한국: 08:00-15:20 (익일 주문), 18:00-다음날 08:00 (당일 주문)
- 미국: 10:00-23:20 KST (다음 개장 시 체결)

---

### 6. Docker 내장 Cron 지원

Docker 컨테이너 내에서 직접 스케줄링을 실행할 수 있습니다:

```bash
# docker-compose.yml
services:
  prism:
    environment:
      - ENABLE_CRON=true

# 스케줄 (docker/crontab)
# 한국 주식: 09:10, 15:40
# 미국 주식: 23:40, 02:10, 06:10 KST
```

---

### 7. 통합 포트폴리오 리포터

한국/미국 주식 포트폴리오를 통합 관리:

```python
# trading/portfolio_telegram_reporter.py
reporter = IntegratedPortfolioReporter(
    kr_trader=kr_trader,
    us_trader=us_trader
)
await reporter.send_daily_report()
```

---

### 8. 기타 개선사항

#### 8.1 텔레그램 요약 에이전트 GPT-5.2 업그레이드

```python
# reasoning_effort: none (속도 최적화)
llm = OpenAIAugmentedLLM(
    model="gpt-5.2",
    reasoning_effort="none"
)
```

#### 8.2 PDF 파일명 회사명 번역

다국어 브로드캐스트 시 PDF 파일명에 번역된 회사명 사용:

```
[KO] 삼성전자_급등주_분석_20260128.pdf
[EN] Samsung_Electronics_Surge_Analysis_20260128.pdf
[JA] サムスン電子_急騰株_分析_20260128.pdf
```

#### 8.3 L2 저널 버그 수정

`insights.priority.undefined` 오류 수정 - 우선순위 필드 누락 시 기본값 적용

---

## 변경된 파일

### 신규 파일 (prism-us 모듈)

| 파일 | 설명 | LOC |
|------|------|-----|
| `prism-us/us_stock_analysis_orchestrator.py` | 메인 파이프라인 | 913 |
| `prism-us/us_stock_tracking_agent.py` | 트레이딩 에이전트 | 1,688 |
| `prism-us/us_telegram_summary_agent.py` | 텔레그램 요약 | 767 |
| `prism-us/us_trigger_batch.py` | 급등주 탐지 | 904 |
| `prism-us/us_performance_tracker_batch.py` | 성과 추적 | 655 |
| `prism-us/trading/us_stock_trading.py` | KIS API 래퍼 | 1,387 |
| `prism-us/cores/us_analysis.py` | 분석 엔진 | 435 |
| `prism-us/cores/us_data_client.py` | 데이터 클라이언트 | 772 |
| `prism-us/cores/us_surge_detector.py` | 급등주 탐지 | 502 |
| `prism-us/cores/us_stock_chart.py` | 차트 생성 | 768 |
| `prism-us/cores/agents/*.py` | 분석 에이전트 (6개) | 2,337 |
| `prism-us/tracking/*.py` | 저널/압축 시스템 | 1,398 |
| `prism-us/tests/*.py` | 테스트 스위트 | 2,953 |

### 신규 파일 (기타)

| 파일 | 설명 |
|------|------|
| `tracking/user_memory.py` | 사용자 기억 관리자 |
| `cores/company_name_translator.py` | 회사명 번역 모듈 |
| `examples/generate_us_dashboard_json.py` | US 대시보드 JSON 생성 |
| `docker/entrypoint.sh` | Docker 엔트리포인트 |
| `docker/crontab` | Docker 내장 크론 설정 |
| `utils/setup_us_crontab.sh` | US 크론 설정 스크립트 |

### 수정된 파일

| 파일 | 주요 변경 |
|------|----------|
| `telegram_ai_bot.py` | /us_evaluate, /us_report, /journal 추가 (+819 lines) |
| `report_generator.py` | US 평가 응답, memory_context 파라미터 (+692 lines) |
| `pdf_converter.py` | Prism Light 테마, 마크다운 구조 개선 (+738 lines) |
| `trading/domestic_stock_trading.py` | 예약 주문 limit_price 지원 |
| `trading/portfolio_telegram_reporter.py` | KR/US 통합 리포팅 |
| `tracking/db_schema.py` | user_memories, user_preferences 테이블 추가 |
| `Dockerfile` | 크론, 멀티스테이지 빌드 개선 |

---

## 데이터베이스 스키마 변경

### 신규 테이블

```sql
-- 사용자 기억 저장
CREATE TABLE user_memories (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    memory_type TEXT NOT NULL,      -- journal/evaluation/report
    content TEXT NOT NULL,          -- JSON
    summary TEXT,
    ticker TEXT,
    market_type TEXT DEFAULT 'kr',  -- kr/us
    compression_layer INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

-- 사용자 선호 설정
CREATE TABLE user_preferences (
    user_id INTEGER PRIMARY KEY,
    preferred_tone TEXT,
    investment_style TEXT,
    favorite_tickers TEXT,          -- JSON array
    total_evaluations INTEGER DEFAULT 0,
    total_journals INTEGER DEFAULT 0
);
```

### US 테이블 (prism-us/tracking/db_schema.py)

```sql
-- US 보유 종목
CREATE TABLE us_stock_holdings (...);

-- US 매매 이력
CREATE TABLE us_trading_history (...);

-- US 관심 종목
CREATE TABLE us_watchlist_history (
    ...
    tracking_status TEXT DEFAULT 'pending',  -- 신규
    was_traded INTEGER DEFAULT 0,            -- 신규
    risk_reward_ratio REAL                   -- 신규
);

-- US 시장 상황
CREATE TABLE us_market_condition (...);

-- US 매도 결정
CREATE TABLE us_holding_decisions (...);

-- US 트레이딩 저널
CREATE TABLE us_trading_journal (...);

-- US 성과 추적
CREATE TABLE us_analysis_performance_tracker (...);
```

---

## 환경 변수

### 신규 환경 변수

```bash
# .env
# 미국 주식 텔레그램 채널 (선택)
TELEGRAM_CHANNEL_ID_US="-100..."
TELEGRAM_CHANNEL_ID_US_EN="-100..."

# Finnhub API (보조 데이터)
FINNHUB_API_KEY="your-key"

# Docker 크론 활성화
ENABLE_CRON=true
```

---

## 업데이트 방법

```bash
# 1. 코드 업데이트
git pull origin feature/prism-us

# 2. 의존성 설치
pip install -r requirements.txt

# 3. US 데이터베이스 초기화
python -c "
import sys
sys.path.insert(0, 'prism-us')
from tracking.db_schema import initialize_us_database
initialize_us_database()
"

# 4. MCP 서버 설정 (mcp_agent.config.yaml)
# yahoo_finance, sec_edgar 서버 추가

# 5. 대시보드 재빌드 (선택)
cd examples/dashboard && npm install && npm run build
```

---

## 테스트

```bash
# US 모듈 전체 테스트
cd prism-us && python -m pytest tests/ -v

# 개별 파이프라인 테스트
python prism-us/us_trigger_batch.py morning INFO --output test.json
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# 사용자 기억 시스템 테스트
python -c "
from tracking.user_memory import UserMemoryManager
mgr = UserMemoryManager('stock_tracking_db.sqlite')
mgr.save_journal(user_id=123, text='AAPL 테스트')
print(mgr.get_journals(user_id=123))
"
```

---

## 알려진 제한사항

1. **SEC EDGAR 데이터**: 최근 공시만 조회 가능 (과거 데이터 제한)
2. **예약 주문**: 장외 시간에만 가능, 장중에는 즉시 주문으로 전환
3. **성과 추적**: 분석 후 7/14/30일이 지나야 데이터 수집
4. **기억 압축**: LLM 호출 필요 (야간 배치로 비용 최적화)

---

## 향후 계획

- [ ] 일본 주식 모듈 (prism-jp)
- [ ] 실시간 WebSocket 알림
- [ ] 포트폴리오 리밸런싱 자동화
- [ ] 모바일 앱 (React Native)

---

## 기여자

- PRISM-INSIGHT Development Team
- Claude Opus 4.5 (AI Pair Programmer)

---

**Document Version**: 2.0.0
**Last Updated**: 2026-01-28
