# US Trigger Batch 데이터 처리 상세 과정

> **문서 작성일**: 2026-01-18 (Updated: 2026-01-28)
> **대상 파일**: `prism-us/us_trigger_batch.py`, `prism-us/cores/us_surge_detector.py`

---

## 전체 처리 흐름 다이어그램

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    US TRIGGER BATCH PIPELINE                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ PHASE 1: 데이터 수집 (Data Collection)                            │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │ 1.1 기준일 결정                                                    │   │
│  │     └─ 오늘 → 가장 최근 영업일 (주말/휴일 자동 보정)                  │   │
│  │                                                                   │   │
│  │ 1.2 티커 목록 로딩                                                 │   │
│  │     └─ S&P 500 + NASDAQ-100 (Wikipedia) → 약 550개 종목            │   │
│  │                                                                   │   │
│  │ 1.3 OHLCV Snapshot 수집                                           │   │
│  │     ├─ 당일 snapshot (yfinance batch download)                    │   │
│  │     └─ 전일 snapshot (비교 분석용)                                  │   │
│  │                                                                   │   │
│  │ 1.4 시가총액 데이터 수집                                            │   │
│  │     └─ yfinance Ticker.info API (배치 50개씩)                      │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ PHASE 2: 필터링 단계 (Filtering)                                   │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │ 2.1 시총 필터 (Market Cap Filter)                                  │   │
│  │     └─ >= $5B USD (약 6.5조원)                                    │   │
│  │                                                                   │   │
│  │ 2.2 거래대금 필터 (Trading Value Filter)                           │   │
│  │     └─ >= $100M USD (약 1,300억원)                                │   │
│  │                                                                   │   │
│  │ 2.3 등락률 필터 (Change Rate Filter)                               │   │
│  │     └─ <= 20% (과열 종목 제외)                                     │   │
│  │                                                                   │   │
│  │ 2.4 트리거별 특수 필터                                              │   │
│  │     ├─ Volume Surge: 거래량 증가율 >= 30%                          │   │
│  │     ├─ Gap Up: 갭상승률 >= 1%                                      │   │
│  │     └─ Value-to-Cap: 거래대금/시총 비율 계산                         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ PHASE 3: 트리거 실행 (Trigger Execution)                           │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │ Morning Mode:                                                     │   │
│  │   ├─ Volume Surge Top (거래량 급증)                                │   │
│  │   ├─ Gap Up Momentum Top (갭상승 모멘텀)                           │   │
│  │   └─ Value-to-Cap Ratio Top (거래회전율)                           │   │
│  │                                                                   │   │
│  │ Afternoon Mode:                                                   │   │
│  │   ├─ Intraday Rise Top (당일 상승률)                               │   │
│  │   ├─ Closing Strength Top (마감 강도)                              │   │
│  │   └─ Volume Surge Sideways (거래량 급증 + 횡보)                     │   │
│  │                                                                   │   │
│  │ 각 트리거 → Composite Score 계산 → Top N 선별                      │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ PHASE 4: 하이브리드 스코어링 (Hybrid Scoring)                       │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │ 4.1 Agent Fit Score 계산 (10일 데이터 기반)                         │   │
│  │     ├─ Stop Loss Price: 현재가 × (1 - 5~7%)                       │   │
│  │     ├─ Target Price: 10일 고점 (최소 +15% 보장)                     │   │
│  │     └─ Risk/Reward Ratio: (목표가-현재가) / (현재가-손절가)           │   │
│  │                                                                   │   │
│  │ 4.2 Agent Fit Score 공식                                          │   │
│  │     └─ R/R Score × 0.6 + SL Score × 0.4                          │   │
│  │                                                                   │   │
│  │ 4.3 Final Score 계산                                              │   │
│  │     └─ Composite Score × 0.3 + Agent Fit Score × 0.7             │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ PHASE 5: 최종 선택 (Final Selection)                               │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │ 5.1 트리거별 1위 선택                                               │   │
│  │     └─ Final Score 기준 정렬 → 중복 제거 → 트리거당 1종목            │   │
│  │                                                                   │   │
│  │ 5.2 최대 3종목 제한                                                 │   │
│  │     └─ 중복 종목 제외, 부족시 추가 선정                               │   │
│  │                                                                   │   │
│  │ 5.3 JSON 출력                                                     │   │
│  │     └─ 메타데이터 + 종목별 상세 정보                                  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## PHASE 1: 데이터 수집 (Data Collection)

### 1.1 기준일 결정

```python
today_str = datetime.datetime.today().strftime("%Y%m%d")  # 예: "20260118"
trade_date = get_nearest_business_day(today_str, prev=True)  # → "20260116"
```

| 상황 | 입력 | 결과 |
|------|------|------|
| 평일 (영업일) | 2026-01-16 (목) | 2026-01-16 |
| 토요일 | 2026-01-18 (토) | 2026-01-16 (금) |
| 일요일 | 2026-01-18 (일) | 2026-01-16 (금) |
| 공휴일 (MLK Day) | 2026-01-19 (월) | 2026-01-17 (금) |

### 1.2 티커 목록 로딩

```
S&P 500 + NASDAQ-100 통합 (get_major_tickers)
  ├─ S&P 500: Wikipedia 파싱 → ~500개 티커
  ├─ NASDAQ-100: Wikipedia 파싱 → ~100개 티커
  └─ 중복 제거 후 합산 → 약 550개 티커
```

**S&P 500 실패시 Fallback**:
```
Failed to load S&P 500 tickers: HTTP Error 403: Forbidden
→ Fallback to 36 major stocks (AAPL, MSFT, GOOGL, AMZN, ...)
```

### 1.3 OHLCV Snapshot 수집

```python
# 당일 snapshot (기준일)
snapshot = get_snapshot(trade_date, tickers)
# → yf.download(tickers, start=start_date, end=end_date+1)

# 전일 snapshot (비교용)
prev_snapshot, prev_date = get_previous_snapshot(trade_date, tickers)
```

**yfinance API 호출**:
```
Request: Download OHLCV for ~550 tickers (S&P 500 + NASDAQ-100)
Period: 2026-01-11 ~ 2026-01-17 (5일간)
Result: MultiIndex DataFrame (5 rows × ~2750 columns)
Target: 2026-01-16 row 추출
```

**Snapshot 구조**:
```
              Open        High         Low       Close     Volume        Amount
Ticker
AAPL    257.899994  258.899994  254.929993  255.529999   72018600  1.840291e+10
MSFT    457.829987  463.190002  456.480011  459.859985   33795700  1.554129e+10
GOOGL   334.410004  334.649994  327.700012  330.000000   40250800  1.328276e+10
...
```

### 1.4 시가총액 데이터 수집

```python
cap_df = get_market_cap_df(tickers)
# → yf.Ticker(ticker).info['marketCap'] for each ticker
```

**배치 처리**:
```
Batch 1: AAPL, MSFT, GOOGL, AMZN, ... (50개)
  └─ API 호출 50회
Batch 2: (나머지)
```

---

## PHASE 2: 필터링 단계 (Filtering)

### 필터 기준값

| 필터 | 기준값 | 목적 |
|------|--------|------|
| **시가총액** | >= $5B USD | 대형주만 선별 (유동성 확보) |
| **거래대금** | >= $100M USD | 활발한 거래 종목만 선별 |
| **등락률** | <= 20% | 과열 종목 제외 (급등락 리스크 방지) |

### 필터링 과정 (Volume Surge 예시)

```
시작: ~550개 종목 (S&P 500 + NASDAQ-100)

Step 1: 시가총액 필터
  └─ snap = snap[snap["MarketCap"] >= $5B]
  └─ 결과: ~550개 → ~500개

Step 2: 거래대금 필터
  └─ snap = apply_absolute_filters(snap, min_value=$100M)
  └─ 결과: ~500개 → ~400개

Step 3: 등락률 필터
  └─ snap = snap[snap["DailyChange"] <= 20.0]
  └─ 결과: ~400개 → ~380개

Step 4: 거래량 증가율 필터 (트리거 특수 조건)
  └─ snap = snap[snap["VolumeIncreaseRate"] >= 30.0]
  └─ 결과: ~380개 → 1~10개
```

---

## PHASE 3: 트리거 실행 (Trigger Execution)

### Morning 트리거 (3종류)

#### 1. Volume Surge Top (거래량 급증)

```yaml
조건:
  - 거래대금 >= $100M
  - 거래량 증가율 >= 30% (전일 대비)
  - 당일 상승 (Close > Open)
  - 등락률 <= 20%

Composite Score:
  - 거래량 증가율 × 0.6 (60%)
  - 절대 거래량 × 0.4 (40%)

선별: Top 10 by Composite Score
```

#### 2. Gap Up Momentum Top (갭상승 모멘텀)

```yaml
조건:
  - 거래대금 >= $100M
  - 갭상승률 >= 1% (시가 > 전일종가)
  - 모멘텀 유지 (Close > Open)
  - 등락률 <= 15%

Composite Score:
  - 갭상승률 × 0.5 (50%)
  - 장중 상승률 × 0.3 (30%)
  - 거래대금 × 0.2 (20%)

선별: Top 15 → Secondary filter → Top 10
```

#### 3. Value-to-Cap Ratio Top (거래회전율)

```yaml
조건:
  - 거래대금 >= $100M
  - 시가총액 >= $5B
  - 등락률 <= 20%

Value-to-Cap Ratio:
  - 거래대금 / 시가총액 (회전율)

Composite Score:
  - Value-to-Cap × 0.7 (70%)
  - 등락률 × 0.3 (30%)

선별: Top 10 by Composite Score
```

### 실제 실행 결과 (2026-01-16)

```
=== Morning Batch Execution ===

Volume Surge Top detected (1 stocks):
  - WMT (Walmart Inc.)
    └─ VolumeIncreaseRate: 1166.75%

Gap Up Momentum Top detected (3 stocks):
  - AVGO (Broadcom Inc.)     GapUpRate: 1.87%
  - NEE (NextEra Energy)     GapUpRate: 1.54%
  - AMZN (Amazon.com)        GapUpRate: 1.21%

Value-to-Cap Ratio Top detected (7 stocks):
  - WMT, AVGO, MSFT, TXN, JPM, NEE, AMZN
```

---

## PHASE 4: 하이브리드 스코어링 (Hybrid Scoring)

### 목적

**Trading Agent와의 "궁합" 계산**

- 단순히 시장 시그널만 강한 종목이 아닌
- **실제로 Trading Agent가 매수 결정을 내릴 수 있는 종목** 선별
- Risk/Reward 비율과 손절폭 기준 충족 여부 사전 평가

### Agent Fit Score 계산

#### Step 1: 10일 OHLCV 데이터 수집

```python
multi_day_df = get_multi_day_ohlcv(ticker, trade_date, lookback_days=10)
# → 과거 10일간의 OHLCV 데이터 수집
```

#### Step 2: 손절가 계산 (Fixed Stop Loss)

```python
# v1.16.6: 고정 손절폭 방식
stop_loss_price = current_price × (1 - sl_max)

# 트리거별 손절폭 기준
TRIGGER_CRITERIA = {
    "Volume Surge Top":      {"sl_max": 0.05, "rr_target": 1.2},  # 5%
    "Gap Up Momentum Top":   {"sl_max": 0.05, "rr_target": 1.2},  # 5%
    "Intraday Rise Top":     {"sl_max": 0.05, "rr_target": 1.2},  # 5%
    "Closing Strength Top":  {"sl_max": 0.05, "rr_target": 1.3},  # 5%
    "Value-to-Cap Ratio":    {"sl_max": 0.05, "rr_target": 1.3},  # 5%
    "Volume Surge Sideways": {"sl_max": 0.07, "rr_target": 1.5},  # 7%
}
```

#### Step 3: 목표가 계산

```python
# 10일 고점 기반 저항선
target_price = max(10일간 High)

# v1.16.6: 최소 +15% 보장
min_target = current_price × 1.15
if target_price < min_target:
    target_price = min_target
```

#### Step 4: Risk/Reward Ratio 계산

```python
potential_gain = target_price - current_price   # 예상 수익
potential_loss = current_price - stop_loss_price  # 예상 손실

risk_reward_ratio = potential_gain / potential_loss

# 예시: WMT
# current_price = $119.70
# stop_loss_price = $113.71 (5% 손절)
# target_price = $137.65 (15% 목표)
# R/R = ($137.65 - $119.70) / ($119.70 - $113.71) = 3.0
```

#### Step 5: Agent Fit Score 계산

```python
# R/R Score: 목표 R/R 대비 달성률 (최대 1.0)
rr_score = min(risk_reward_ratio / rr_target, 1.0)

# SL Score: 고정 손절이므로 항상 1.0
sl_score = 1.0

# Agent Fit Score = R/R × 60% + SL × 40%
agent_fit_score = rr_score × 0.6 + sl_score × 0.4
```

### Final Score 계산

```python
# Composite Score 정규화
composite_norm = (score - min) / (max - min)

# Final Score = Composite 30% + Agent Fit 70%
final_score = composite_norm × 0.3 + agent_fit_score × 0.7
```

### 실제 계산 결과

```
[Volume Surge Top] Hybrid scoring complete:
  - WMT: Composite=1.000, Agent=1.000, Final=0.700, R/R=3.00, SL%=5.0%

[Gap Up Momentum Top] Hybrid scoring complete:
  - AVGO: Composite=0.550, Agent=1.000, Final=1.000, R/R=3.00, SL%=5.0%
  - NEE:  Composite=0.269, Agent=1.000, Final=0.755, R/R=3.00, SL%=5.0%
  - AMZN: Composite=0.206, Agent=1.000, Final=0.700, R/R=3.00, SL%=5.0%

[Value-to-Cap Ratio Top] Hybrid scoring complete:
  - WMT:  Composite=0.957, Agent=1.000, Final=1.000, R/R=3.00, SL%=5.0%
  - AVGO: Composite=0.295, Agent=1.000, Final=0.746, R/R=3.00, SL%=5.0%
  - MSFT: Composite=0.233, Agent=1.000, Final=0.722, R/R=3.00, SL%=5.0%
```

---

## PHASE 5: 최종 선택 (Final Selection)

### 선택 로직

```python
# 트리거별 Final Score 1위 선택 (중복 제거)
selected = set()
for trigger in triggers:
    for ticker in sorted_by_final_score(trigger):
        if ticker not in selected:
            final_result[trigger] = ticker
            selected.add(ticker)
            break

# 최대 3종목 제한
if len(selected) < 3:
    # 전체 candidates에서 추가 선정
```

### 최종 결과

```
[Volume Surge Top] Final selection: WMT
[Gap Up Momentum Top] Final selection: AVGO
[Value-to-Cap Ratio Top] Final selection: MSFT
```

**최종 선정 3종목**:
| Trigger | Ticker | Company | Final Score | R/R |
|---------|--------|---------|-------------|-----|
| Volume Surge | WMT | Walmart Inc. | 0.700 | 3.00 |
| Gap Up Momentum | AVGO | Broadcom Inc. | 1.000 | 3.00 |
| Value-to-Cap | MSFT | Microsoft | 0.722 | 3.00 |

---

## 시간순 데이터 처리 흐름

```
T+0.0s  시작
  │
T+0.1s  기준일 결정: 20260118(일) → 20260116(금)
  │
T+0.2s  티커 로딩 (S&P 500 + NASDAQ-100)
  │       └─ 약 550개 종목 (중복 제거)
  │
T+30s   Snapshot 다운로드 (yfinance batch)
  │       └─ ~550 tickers × 5 days = ~2750 data points
  │
T+45s   Previous Snapshot 다운로드
  │       └─ 20260115 데이터
  │
T+60s   시가총액 데이터 수집
  │       └─ ~550 API calls (batch 50개씩)
  │
T+65s   === Morning Batch Execution ===
  │
T+65s   Volume Surge Trigger 실행
  │       ├─ 시총 필터: ~550 → ~500
  │       ├─ 거래대금 필터: ~500 → ~400
  │       ├─ 등락률 필터: ~400 → ~380
  │       ├─ 거래량증가 필터: ~380 → 1~10
  │       └─ 결과: Top 10 (예: WMT 등)
  │
T+66s   Gap Up Momentum Trigger 실행
  │       ├─ 시총 필터: ~550 → ~500
  │       ├─ 거래대금 필터: ~500 → ~400
  │       ├─ 갭상승 필터 (>=1%): ~400 → ~50
  │       ├─ 모멘텀 유지 필터: ~50 → ~20
  │       └─ 결과: Top 10 (예: AVGO, NEE, AMZN 등)
  │
T+67s   Value-to-Cap Trigger 실행
  │       ├─ 시총 필터: ~550 → ~500
  │       ├─ 거래대금 필터: ~500 → ~400
  │       ├─ 회전율 계산 및 정렬
  │       └─ 결과: Top 10 (예: WMT, AVGO, MSFT 등)
  │
T+70s   Hybrid Scoring 시작 (10일 데이터)
  │       ├─ Volume Surge: 각 종목 점수 계산
  │       ├─ Gap Up: 각 종목 점수 계산
  │       └─ Value-to-Cap: 각 종목 점수 계산
  │
T+75s   Final Selection
  │       ├─ Volume Surge 1위: 선정
  │       ├─ Gap Up 1위: 선정
  │       └─ Value-to-Cap 1위: 선정 (중복 제외)
  │
T+76s   JSON 출력
  │
T+76s   완료
```

---

## 요약: 데이터 건수 변화

```
┌────────────────────────────────────────────────────────────┐
│ 단계                    │ 종목수 │ 감소율  │ 누적률       │
├────────────────────────────────────────────────────────────┤
│ 1. 초기 티커 (S&P+NQ)   │  ~550  │   -     │   100%       │
│ 2. 시총 필터 ($5B)      │  ~500  │   9%    │    91%       │
│ 3. 거래대금 ($100M)     │  ~400  │  20%    │    73%       │
│ 4. 등락률 필터 (20%)    │  ~380  │   5%    │    69%       │
│ 5. 트리거별 특수조건    │  1~20  │ 95~99%  │   0.2~4%     │
│ 6. Hybrid 스코어링     │  1~20  │   0%    │    -         │
│ 7. 최종 선정 (Top 1)   │    3   │   -     │   0.5%       │
└────────────────────────────────────────────────────────────┘
```

---

## 핵심 설계 원칙

### 1. Agent 친화적 선별

- **목적**: Trading Agent가 실제로 매수 결정을 내릴 종목만 추천
- **방법**: Risk/Reward 비율 사전 검증 (R/R >= 1.2~1.5)
- **결과**: Agent 거부율 최소화

### 2. 고정 손절폭 방식 (v1.16.6)

- **이전**: 10일 저점 기반 → 급등주의 경우 손절폭 48%+ → Agent 거부
- **변경**: 현재가 기준 고정 5~7% → 모든 종목 Agent 기준 충족
- **이점**: 급등주도 Agent 평가 가능

### 3. Hybrid Scoring

- **Composite Score (30%)**: 시장 시그널 강도
- **Agent Fit Score (70%)**: 트레이딩 적합도
- **효과**: 시그널 + 실행가능성 균형

---

**문서 버전**: 1.1
**작성자**: Claude
**기준 코드 버전**: v1.16.6+
**변경 이력**:
- v1.1 (2026-01-28): S&P 500 + NASDAQ-100 통합 스캔으로 변경 (~550개 종목)
