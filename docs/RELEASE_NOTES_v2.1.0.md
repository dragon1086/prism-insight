# PRISM-INSIGHT v2.1.0

발표일: 2026년 1월 28일

## 개요

PRISM-INSIGHT v2.1.0은 **US Watchlist 성과 추적 기능**, **GCP Pub/Sub 미국 시장 지원**, **저널 시스템 안정화**, **다수의 버그 수정**을 포함한 마이너 버전입니다. v2.0.0에서 추가된 prism-us 모듈의 분석 성과 추적 기능이 완성되었습니다.

**주요 수치:**
- 총 33개 커밋
- 56개 파일 변경
- +4,400 / -1,800 라인

---

## 주요 변경사항

### 1. US Watchlist 성과 추적 기능 ⭐ NEW

미국 주식 분석 결과의 7/14/30일 성과를 추적하는 기능이 추가되었습니다. 한국 버전과 동일하게 미진입 종목도 watchlist에 저장하여 분석 정확도를 측정할 수 있습니다.

#### 1.1 us_watchlist_history 테이블 확장

분석 결과를 저장하는 테이블에 새로운 컬럼들이 추가되었습니다:

```sql
-- 새로운 컬럼들
min_score INTEGER,           -- 최소 요구 점수
target_price REAL,           -- 목표가 (USD)
stop_loss REAL,              -- 손절가 (USD)
investment_period TEXT,      -- 투자 기간 (short/medium/long)
portfolio_analysis TEXT,     -- 포트폴리오 분석
valuation_analysis TEXT,     -- 밸류에이션 분석
sector_outlook TEXT,         -- 섹터 전망
market_condition TEXT,       -- 시장 상황
rationale TEXT,              -- 진입/미진입 사유
risk_reward_ratio REAL,      -- 리스크/리워드 비율
was_traded INTEGER           -- 실제 매매 여부 (0=관망, 1=매매)
```

#### 1.2 _save_watchlist_item() 메서드 추가

미진입 종목을 자동으로 저장하는 메서드가 `USStockTrackingAgent`에 추가되었습니다:

```python
# prism-us/us_stock_tracking_agent.py
await self._save_watchlist_item(
    ticker=ticker,
    company_name=company_name,
    current_price=current_price,
    buy_score=buy_score,
    min_score=min_score,
    decision=normalized_decision,
    skip_reason=reason,
    scenario=scenario,
    sector=sector,
    was_traded=False
)
```

#### 1.3 성과 추적기 연동

`us_analysis_performance_tracker` 테이블과 연동하여 7/14/30일 성과를 추적합니다:

```python
# 자동으로 us_analysis_performance_tracker에도 저장
INSERT INTO us_analysis_performance_tracker (
    ticker, company_name, analysis_date, analysis_price,
    predicted_direction, target_price, stop_loss, buy_score,
    decision, skip_reason, risk_reward_ratio,
    trigger_type, trigger_mode, sector,
    tracking_status, was_traded, created_at
) VALUES (...)
```

#### 1.4 마이그레이션 함수

기존 데이터베이스에 새 컬럼을 추가하는 마이그레이션 함수가 포함되었습니다:

```python
# prism-us/tracking/db_schema.py
migrate_us_watchlist_history_columns(cursor, conn)
```

---

### 2. GCP Pub/Sub 미국 시장 지원 ⭐ NEW

GCP Pub/Sub 메시징 시스템에 미국 시장 지원을 추가했습니다.

#### 1.1 시장 구분 (market 필드)

```python
# 메시지에 market 필드 추가
{
    "action": "BUY",
    "ticker": "AAPL",
    "market": "US",  # NEW: KR 또는 US
    "price": 185.50,
    "quantity": 10
}
```

#### 1.2 US 장 시간 체크

NYSE 캘린더 기반의 정확한 장 시간 체크를 구현했습니다:

```python
# examples/messaging/gcp_pubsub_subscriber_example.py
def is_us_market_hours():
    """미국 시장 시간 체크 (EST 09:30-16:00)"""
    # 주말 체크 추가
    if now_est.weekday() >= 5:
        return False
    # 장 시간 체크
    return time(9, 30) <= now_est.time() <= time(16, 0)
```

#### 1.3 장외시간 매도 스케줄링

BUY와 동일하게 SELL 액션도 장외시간에 다음 개장 시 예약 주문으로 처리합니다:

```python
# 장외시간 SELL → 예약 주문
if action == "SELL" and not is_us_market_hours():
    schedule_for_market_open(ticker, action, quantity)
```

#### 1.4 check_market_day.py 활용

US 시장 로직을 `prism-us/check_market_day.py`로 통합하여 정확한 영업일 계산을 수행합니다:

```python
from prism_us.check_market_day import is_us_market_day, get_reference_date
```

---

### 3. 텔레그램 봇 개선

#### 2.1 /memories 명령어 추가

저장된 기억(저널, 평가 기록)을 조회하는 명령어를 추가했습니다:

```
User: /memories
Bot:  📚 저장된 기억 (최근 10개)

      [2026-01-28] AAPL 저널
      "AI 테마로 더 갈 것 같다..."

      [2026-01-27] TSLA 평가
      "변동성 높아 관망 권장"
```

#### 2.2 저널 답장에 AI 대화 지원

저널 메시지에 답장하면 AI가 해당 종목에 대해 추가 대화를 할 수 있습니다:

```
User: [저널 메시지에 답장] 오늘 어떻게 됐어?
Bot:  📈 AAPL 현재 상황 분석...
      지난번 저널에서 "170달러까지 홀딩" 언급하셨는데,
      현재 $187로 목표가를 초과했습니다.
```

#### 2.3 사용자 기억 별도 DB 파일 분리

사용자 기억을 별도의 SQLite 파일로 분리하여 관리 효율성을 높였습니다:

```
stock_tracking_db.sqlite      # 트레이딩 데이터
user_memories.sqlite          # 사용자 기억 (별도 관리)
```

#### 2.4 저널 500자 초과 경고

긴 저널 작성 시 경고 메시지를 표시합니다:

```
User: /journal
      [500자 이상의 긴 텍스트]
Bot:  ⚠️ 저널이 500자를 초과했습니다 (현재: 723자).
      핵심 내용만 간추려 다시 작성해주세요.
```

---

### 4. prism-us 모듈 안정화

#### 3.1 midday 모드 추가

미국 장 중간 점검을 위한 midday 모드를 추가했습니다:

```bash
# 오전 분석 (개장 후)
python prism-us/us_stock_analysis_orchestrator.py --mode morning

# 중간 점검 (점심 시간)
python prism-us/us_stock_analysis_orchestrator.py --mode midday

# 오후 분석 (마감 전/후)
python prism-us/us_stock_analysis_orchestrator.py --mode afternoon
```

#### 3.2 Redis/GCP 시그널 발행

US 트래킹 에이전트에서 매수/매도 시그널을 Redis/GCP로 발행합니다:

```python
# prism-us/us_stock_tracking_agent.py
await publish_signal({
    "action": "BUY",
    "ticker": "AAPL",
    "market": "US",
    "score": 8,
    "reason": "AI 분석 기반 매수 추천"
})
```

#### 3.3 Python 3.11 호환성 수정

Python 3.11에서 발생하던 호환성 이슈를 수정했습니다:
- timezone 처리 개선
- asyncio 관련 수정

#### 3.4 포트폴리오 중복 제거

동일 티커의 중복 보유 문제를 수정했습니다:

```python
# 중복 티커 병합
portfolio = deduplicate_by_ticker(raw_portfolio)
```

---

### 5. 대시보드 개선

#### 4.1 KR/US 마켓 선택기

대시보드에서 한국/미국 시장 데이터를 전환하여 볼 수 있습니다:

```typescript
// examples/dashboard/components/market-selector.tsx
<MarketSelector
  market={market}  // "KR" | "US"
  onMarketChange={setMarket}
/>
```

#### 4.2 통화 포맷팅 (KRW/USD)

시장에 따른 자동 통화 포맷팅을 지원합니다:

```typescript
// examples/dashboard/lib/currency.ts
formatCurrency(10000, "KR")  // "10,000원"
formatCurrency(100, "US")    // "$100.00"
```

---

### 6. 버그 수정

#### 5.1 Docker 관련

| 이슈 | 수정 내용 |
|------|----------|
| DB 초기화 실패 | 컨테이너 시작 시 DB 자동 초기화 |
| 모듈 경로 문제 | sys.path 순서 조정 |
| cron 실행 경로 | 프로젝트 루트에서 실행하도록 수정 |

#### 5.2 트레이딩 관련

| 이슈 | 수정 내용 |
|------|----------|
| sector 컬럼 누락 | stock_holdings, trading_history에 sector 추가 |
| AsyncUSTradingContext 임포트 | 경로 수정 |
| trading 모듈 충돌 | sys.path 순서 조정으로 해결 |

#### 5.3 MCP 서버 관련

| 이슈 | 수정 내용 |
|------|----------|
| uvx 실행 실패 | `--from` 플래그 추가 |

```yaml
# 수정 전
yahoo_finance:
  command: "uvx"
  args: ["yahoo-finance-mcp"]

# 수정 후
yahoo_finance:
  command: "uvx"
  args: ["--from", "yahoo-finance-mcp", "yahoo-finance-mcp"]
```

#### 5.4 기타

| 이슈 | 수정 내용 |
|------|----------|
| 저널 텍스트 제한 | 1000자 → 2000자로 확장 |
| None cap_df 오류 | value-to-cap ratio 트리거에서 None 체크 추가 |
| 티커 추출 우선순위 | 한국 주식 티커 우선 추출 |
| lxml 누락 | pandas read_html 의존성 추가 |

---

## 변경된 파일

### 신규 파일

| 파일 | 설명 |
|------|------|
| `examples/dashboard/components/market-selector.tsx` | KR/US 마켓 선택 컴포넌트 |
| `examples/dashboard/lib/currency.ts` | 통화 포맷팅 유틸리티 |

### 주요 수정 파일

| 파일 | 주요 변경 |
|------|----------|
| `prism-us/us_stock_tracking_agent.py` | **_save_watchlist_item() 추가**, Redis/GCP 시그널 발행 |
| `prism-us/tracking/db_schema.py` | **us_watchlist_history 컬럼 확장**, 마이그레이션 함수 추가 |
| `examples/messaging/gcp_pubsub_subscriber_example.py` | US 시장 지원, SELL 스케줄링 |
| `telegram_ai_bot.py` | /memories 명령어, 저널 답장 AI 대화 |
| `prism-us/us_trigger_batch.py` | midday 모드 지원 |
| `prism-us/us_stock_analysis_orchestrator.py` | midday 모드 지원 |
| `prism-us/check_market_day.py` | US 시장 영업일 체크 통합 |
| `tracking/db_schema.py` | sector 컬럼 추가 |
| `mcp_agent.config.yaml.example` | uvx --from 플래그 |

---

## 업데이트 방법

```bash
# 1. 코드 업데이트
git pull origin main

# 2. 의존성 설치 (lxml 추가됨)
pip install -r requirements.txt

# 3. 대시보드 재빌드 (선택)
cd examples/dashboard && npm install && npm run build

# 4. MCP 설정 업데이트
# mcp_agent.config.yaml에서 uvx 서버에 --from 플래그 추가
```

---

## 테스트

```bash
# GCP 시그널 테스트
python tests/test_gcp_pubsub_signal.py

# US 트리거 배치 테스트
python prism-us/us_trigger_batch.py morning INFO --output test.json

# US midday 모드 테스트
python prism-us/us_stock_analysis_orchestrator.py --mode midday --no-telegram

# 사용자 기억 테스트
python -c "
from tracking.user_memory import UserMemoryManager
mgr = UserMemoryManager()
print(mgr.get_memories(user_id=123, limit=10))
"
```

---

## 알려진 제한사항

1. **저널 길이**: 500자 초과 시 경고만 표시, 강제 제한 없음
2. **midday 모드**: 급등주가 없으면 빈 리포트 생성
3. **기억 DB 분리**: 기존 사용자 기억은 자동 마이그레이션되지 않음

---

## 기여자

- PRISM-INSIGHT Development Team
- Claude Opus 4.5 (AI Pair Programmer)

---

**Document Version**: 2.1.0
**Last Updated**: 2026-01-28

---

## 📢 텔레그램 구독자용 요약

> 아래 내용을 텔레그램 채널에 공유할 수 있습니다.

---

### 📢 PRISM-INSIGHT v2.1.0 업데이트 안내

**발표일**: 2026년 1월 28일

안녕하세요, 프리즘 인사이트 구독자 여러분!

v2.1.0 마이너 버전이 출시되었습니다. 🎉

---

#### 🆕 신규 기능

**1. 📈 US 분석 성과 추적 (NEW!)**
- 미국 주식 분석 결과의 7/14/30일 성과를 자동 추적
- 분석 정확도 측정으로 AI 분석 품질 검증 가능
- 대시보드에서 성과 확인 가능

**2. /memories 명령어**
- 저장된 투자 일기, 평가 기록을 한눈에 조회
- `/memories` 입력으로 최근 기록 확인!

**3. 저널 답장 AI 대화**
- 저널 메시지에 답장하면 AI가 해당 종목 추가 상담
- "오늘 어떻게 됐어?" → AI가 현재 상황 분석 제공

**4. 미국 시장 시그널 개선**
- 장외시간 매도 주문도 예약 주문으로 처리
- 더 정확한 영업일 계산

---

#### ✨ 개선 사항

- 📊 **대시보드 KR/US 선택** - 한국/미국 시장 전환 가능
- 🕐 **midday 모드** - 미국장 중간 점검 추가
- 📋 **Watchlist DB 확장** - 더 상세한 분석 데이터 저장

---

#### 🐛 버그 수정

- Docker 컨테이너 DB 초기화 문제 해결
- 저널 텍스트 길이 제한 확장 (2000자)
- MCP 서버 연결 안정화

---

문의사항은 언제든 봇에게 메시지 남겨주세요! 🙏
