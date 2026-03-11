# Trigger Batch 알고리즘 문서

> **Last Updated**: 2026-03-11
> **Version**: 4.0 (v2.5.3)
> **File**: `trigger_batch.py`, `prism-us/us_trigger_batch.py`
> **Purpose**: 급등주/모멘텀 종목 자동 스크리닝 + 거시경제 연동 하이브리드 선정

---

## 목차

1. [개요](#1-개요)
2. [공통 필터](#2-공통-필터)
3. [오전 트리거 (Morning)](#3-오전-트리거-morning)
4. [오후 트리거 (Afternoon)](#4-오후-트리거-afternoon)
5. [복합 점수 계산](#5-복합-점수-계산)
6. [트리거 유형별 기준](#6-트리거-유형별-기준-v1166)
7. [에이전트 점수 계산](#7-에이전트-점수-계산-v1166)
8. [하이브리드 선별](#8-하이브리드-선별-hybrid-selection)
9. [탑다운+바텀업 하이브리드 선정](#9-탑다운바텀업-하이브리드-선정-v253)
10. [최종 선별 로직](#10-최종-선별-로직-select_final_tickers)
11. [사용법](#11-사용법)

---

## 1. 개요

### 목적

`trigger_batch.py`는 매일 오전/오후에 실행되어 **관심 종목 후보**를 자동으로 선별합니다. 선별된 종목은 이후 AI 분석 파이프라인(`stock_analysis_orchestrator.py`)으로 전달됩니다.

### 핵심 목표 (v2.5.3)

- **연평균 15% 수익** 달성을 위한 종목 선별
- **trigger_batch ↔ trading_agent 완전 정합성** 확보
- 모든 시장 상황(강세장/약세장/횡보장)에서 작동
- **거시경제 체제(regime) 연동**: 탑다운(주도섹터) + 바텀업(시그널) 하이브리드 선정

### 실행 흐름

```
macro_intelligence_agent.py (거시경제 분석: regime, leading_sectors, sector_map)
    ↓
trigger_batch.py (종목 스크리닝 + 하이브리드 탑다운/바텀업 선정)
    ↓
stock_analysis_orchestrator.py (AI 분석)
    ↓
stock_tracking_agent.py (매수/매도 결정)
    ↓
trading/domestic_stock_trading.py (실제 주문)
```

### 데이터 소스

- **kospi_kosdaq_stock_server**: KRX 정보데이터시스템 API
- **스냅샷 데이터**: OHLCV (시가, 고가, 저가, 종가, 거래량, 거래대금)
- **시가총액 데이터**: 종목별 시가총액

---

## 2. 공통 필터

모든 트리거에 적용되는 기본 필터입니다.

### 2.1 절대적 기준 필터 (`apply_absolute_filters`)

| 필터 | 기준 | 목적 |
|------|------|------|
| 최소 거래대금 | **100억원 이상** | 유동성 확보 (v1.16.6 강화) |
| 최소 거래량 | 시장 평균의 20% 이상 | 거래 활성화 종목 |

```python
def apply_absolute_filters(df, min_value=10000000000):  # 100억원
    filtered = df[df['Amount'] >= min_value]
    avg_volume = df['Volume'].mean()
    filtered = filtered[filtered['Volume'] >= avg_volume * 0.2]
    return filtered
```

### 2.2 시가총액 필터 (v1.16.6 변경)

| 필터 | 기준 | 목적 |
|------|------|------|
| 최소 시가총액 | **5000억원 이상** | 유동성 확보, 기관 관심 종목 (v1.16.6 상향) |

```python
snap = snap[snap["시가총액"] >= 500000000000]  # 5000억원
```

### 2.3 등락률 필터 (v1.16.6 신설)

| 필터 | 기준 | 목적 |
|------|------|------|
| 최대 등락률 | **20% 이하** | 상한가/과열 종목 제외 |

```python
snap = snap[snap["전일대비등락률"] <= 20.0]
```

### 2.4 저유동성 필터 (`filter_low_liquidity`)

거래량 하위 N% 종목 제외 (기본값: 20%)

---

## 3. 오전 트리거 (Morning)

오전 배치는 **장 시작 후** 실행되며, 3개 트리거에서 각 1개씩 총 3개 종목을 선별합니다.

### 3.1 거래량 급증 상위주 (`trigger_morning_volume_surge`)

**목적**: 전일 대비 거래량이 급증한 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 거래량 증가율 | 전일 대비 30% 이상 |
| 상승 여부 | 시가 대비 현재가 상승 |
| 거래대금 | 100억원 이상 |
| 시가총액 | 5000억원 이상 |
| 등락률 | 20% 이하 |

#### 복합 점수

```
복합점수 = 거래량증가율(60%) + 절대거래량(40%)
```

---

### 3.2 갭 상승 모멘텀 상위주 (`trigger_morning_gap_up_momentum`)

**목적**: 갭 상승으로 시작한 모멘텀 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 갭상승률 | 전일 종가 대비 1% 이상 |
| 상승 지속 | 현재가 > 시가 |
| 거래대금 | 100억원 이상 |
| 시가총액 | 5000억원 이상 |
| 등락률 | 20% 이하 |

#### 복합 점수

```
복합점수 = 갭상승률(50%) + 장중등락률(30%) + 거래대금(20%)
```

---

### 3.3 시총 대비 집중 자금 유입 상위주 (`trigger_morning_value_to_cap_ratio`)

**목적**: 시가총액 대비 비정상적으로 높은 거래대금이 유입된 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 거래대금비율 | 거래대금 / 시가총액 |
| 상승 여부 | 시가 대비 현재가 상승 |
| 거래대금 | 100억원 이상 |
| 시가총액 | 5000억원 이상 |
| 등락률 | 20% 이하 |

#### 복합 점수

```
복합점수 = 거래대금비율(50%) + 절대거래대금(30%) + 장중등락률(20%)
```

---

## 4. 오후 트리거 (Afternoon)

오후 배치는 **장 마감 후** 실행되며, 3개 트리거에서 각 1개씩 총 3개 종목을 선별합니다.

### 4.1 일중 상승률 상위주 (`trigger_afternoon_daily_rise_top`)

**목적**: 당일 가장 강하게 상승한 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 등락률 | 3% 이상 20% 이하 |
| 거래대금 | 100억원 이상 |
| 시가총액 | 5000억원 이상 |

#### 복합 점수

```
복합점수 = 장중등락률(60%) + 거래대금(40%)
```

---

### 4.2 마감 강도 상위주 (`trigger_afternoon_closing_strength`)

**목적**: 종가가 고가에 가까운(강한 마감) 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 마감 강도 | (종가 - 저가) / (고가 - 저가) |
| 거래량 증가 | 전일 대비 거래량 증가 |
| 상승 여부 | 시가 대비 종가 상승 |
| 거래대금 | 100억원 이상 |
| 시가총액 | 5000억원 이상 |

#### 마감 강도 계산

```python
마감강도 = (종가 - 저가) / (고가 - 저가)
# 1에 가까울수록 강한 마감 (종가 ≈ 고가)
# 0에 가까울수록 약한 마감 (종가 ≈ 저가)
```

#### 복합 점수

```
복합점수 = 마감강도(50%) + 거래량증가율(30%) + 거래대금(20%)
```

---

### 4.3 거래량 증가 상위 횡보주 (`trigger_afternoon_volume_surge_flat`)

**목적**: 거래량은 급증했지만 가격은 횡보하는 종목 포착 (세력 매집 의심)

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 거래량 증가율 | 전일 대비 50% 이상 |
| 횡보 여부 | 전일대비등락률 ±5% 이내 |
| 거래대금 | 100억원 이상 |
| 시가총액 | 5000억원 이상 |

#### 복합 점수

```
복합점수 = 거래량증가율(60%) + 거래대금(40%)
```

---

## 5. 복합 점수 계산

### 정규화 방식

모든 지표는 0~1 사이로 정규화됩니다:

```python
normalized = (value - min) / (max - min)
```

### 가중치 적용

```python
복합점수 = Σ (정규화된_지표 × 가중치)
```

---

## 6. 트리거 유형별 기준 (v1.16.6)

`trading_agents.py`와 동기화된 트리거 유형별 손익비/손절폭 기준입니다.

```python
TRIGGER_CRITERIA = {
    "거래량 급증 상위주": {"rr_target": 1.2, "sl_max": 0.05},
    "갭 상승 모멘텀 상위주": {"rr_target": 1.2, "sl_max": 0.05},
    "일중 상승률 상위주": {"rr_target": 1.2, "sl_max": 0.05},
    "마감 강도 상위주": {"rr_target": 1.3, "sl_max": 0.05},
    "시총 대비 집중 자금 유입 상위주": {"rr_target": 1.3, "sl_max": 0.05},
    "거래량 증가 상위 횡보주": {"rr_target": 1.5, "sl_max": 0.07},
    "default": {"rr_target": 1.5, "sl_max": 0.07}
}
```

| 트리거 유형 | 손익비 목표 | 손절폭 |
|------------|-----------|--------|
| 거래량 급증 상위주 | 1.2+ | 5% |
| 갭 상승 모멘텀 상위주 | 1.2+ | 5% |
| 일중 상승률 상위주 | 1.2+ | 5% |
| 마감 강도 상위주 | 1.3+ | 5% |
| 시총 대비 자금 유입 | 1.3+ | 5% |
| 거래량 증가 횡보주 | 1.5+ | 7% |

---

## 7. 에이전트 점수 계산 (v1.16.6)

### 핵심 변경: 고정 손절폭 방식

v1.16.6에서 손절가 계산 방식이 **10일 지지선 기준**에서 **현재가 기준 고정 비율**로 변경되었습니다.

#### 변경 이유

```
[이전 문제점]
- 10일 저가 기준 손절가 계산 → 급등주에서 48%+ 손절폭 발생
- 에이전트 기준(-5%~-7%)과 불일치 → agent_fit_score 급락 → 미진입

[해결]
- 현재가 × (1 - sl_max) 고정 → 항상 에이전트 기준 충족
```

### 현재 계산 방식 (v1.16.6)

```python
def calculate_agent_fit_metrics(ticker, current_price, trade_date, lookback_days=10, trigger_type=None):
    # 트리거 기준 조회
    criteria = TRIGGER_CRITERIA.get(trigger_type, TRIGGER_CRITERIA["default"])
    sl_max = criteria["sl_max"]  # 5% or 7%
    rr_target = criteria["rr_target"]  # 1.2 ~ 1.5

    # 핵심: 고정 손절폭 방식
    stop_loss_price = current_price * (1 - sl_max)
    stop_loss_pct = sl_max  # 항상 5% 또는 7%

    # 목표가: 10일 저항선 (최소 +15% 보장)
    multi_day_df = get_multi_day_ohlcv(ticker, trade_date, lookback_days)
    target_price = multi_day_df["High"].max()

    # 잔여 리스크 완화: 목표가 최소 +15% 보장
    min_target = current_price * 1.15
    if target_price < min_target:
        target_price = min_target

    # 손익비 계산
    risk_reward_ratio = (target_price - current_price) / (current_price - stop_loss_price)

    # 에이전트 점수 (간소화)
    rr_score = min(risk_reward_ratio / rr_target, 1.0)
    sl_score = 1.0  # 고정 손절폭이므로 항상 만점

    agent_fit_score = rr_score * 0.6 + sl_score * 0.4
```

### 점수 계산 공식

| 항목 | 공식 | 설명 |
|------|------|------|
| 손익비 점수 | `min(R:R / rr_target, 1.0)` | 목표 충족 시 만점 |
| 손절폭 점수 | `1.0` (고정) | 항상 기준 이내 |
| 에이전트 점수 | `rr_score × 0.6 + sl_score × 0.4` | 손익비 중심 |

### 효과

| 지표 | v1.16.5 | v1.16.6 |
|------|---------|---------|
| 손절폭 | 0% ~ 50%+ (가변) | 5% ~ 7% (고정) |
| agent_fit_score | 0.03 ~ 0.9 | 0.7 ~ 1.0 |
| 에이전트 승인율 | 낮음 | 대폭 향상 |

---

## 8. 하이브리드 선별 (Hybrid Selection)

### 목적

기존 복합점수 방식은 "오늘 가장 많이 움직인 종목"을 선별하지만, 에이전트 기준에 맞지 않을 수 있습니다. 하이브리드 선별은 **에이전트 기준에 더 잘 맞는 종목**을 선별합니다.

### 최종 점수 계산 (v1.16.6)

```python
최종점수 = 복합점수(정규화) × 0.3 + 에이전트점수 × 0.7
```

> 에이전트 점수 비중이 60% → 70%로 상향 (v1.16.6)

### 선별 흐름

```
1. 각 트리거에서 상위 10개 후보 선별
2. 각 후보에 대해 10일간 OHLCV 데이터 조회
3. 에이전트 점수 계산 (고정 손절폭 방식)
4. 최종 점수 = 복합점수(30%) + 에이전트점수(70%)
5. 각 트리거에서 최종 점수 1위 선택
```

### 효과 예시

| 종목 | 복합점수 | 에이전트점수 | 최종점수 | 손익비 | 손절폭 |
|------|---------|------------|---------|-------|-------|
| 한화시스템 | 0.82 | 1.00 | **1.00** | 3.0 | 5.0% |
| 기가비스 | 0.76 | 1.00 | **1.00** | 3.0 | 5.0% |
| SK텔레콤 | 0.14 | 1.00 | **1.00** | 2.1 | 7.0% |

→ 고정 손절폭으로 모든 종목이 에이전트 기준 충족

---

## 9. 탑다운+바텀업 하이브리드 선정 (v2.5.3)

### 배경

기존 `select_final_tickers`는 순수 바텀업 방식이었습니다. 트리거 시그널(급등, 거래량)로 후보를 감지하고 composite_score/final_score 순위로 Top 3를 선정했습니다. 유일한 매크로 연동은 leading/lagging sector에 대한 ±0.1 점수 보정이었으나, 이는 기존 점수 대비 미미하여 실질적 효과가 없었습니다.

v2.5.3에서 **탑다운 채널**을 추가하여, 거시경제 분석(macro_intelligence_agent)의 주도섹터 정보를 종목 선정에 직접 반영합니다.

### 아키텍처

```
macro_context (거시 분석 결과)
├── market_regime: "strong_bull" | "moderate_bull" | "sideways" | "moderate_bear" | "strong_bear"
├── leading_sectors: [{"sector": "전기·전자", "confidence": 0.9}, ...]
├── lagging_sectors: [{"sector": "건설", "confidence": 0.7}, ...]
└── sector_map: {"005930": "전기·전자", "051910": "화학", ...}

                    ┌──────────────────┐
                    │  트리거 후보 풀   │
                    │  (각 트리거 Top10) │
                    └────────┬─────────┘
                             │
               ┌─────────────┴─────────────┐
               ▼                           ▼
    ┌──────────────────┐        ┌──────────────────┐
    │   탑다운 풀       │        │   바텀업 풀       │
    │ leading_sectors  │        │ (기존 로직 유지)   │
    │ 매칭 종목 필터    │        │ 트리거별 Top1     │
    │ + confidence 가중│        │ + 점수순 충원     │
    └────────┬─────────┘        └────────┬─────────┘
             │                           │
             └─────────┬─────────────────┘
                       ▼
              ┌──────────────────┐
              │  Regime 슬롯 배분 │
              │  (탑다운 N + 바텀업 M = 3) │
              └────────┬─────────┘
                       ▼
              ┌──────────────────┐
              │  최종 3종목 선정  │
              └──────────────────┘
```

### 탑다운 풀 구성 (`_build_topdown_pool`)

트리거 후보 중 `leading_sectors`에 해당하는 종목을 필터하고, 섹터 신뢰도(confidence)로 점수를 증폭합니다.

```
topdown_score = base_score × (1 + sector_confidence × 0.3)
```

- `base_score`: 기존 final_score 또는 composite_score
- `sector_confidence`: macro_context의 leading_sectors에서 해당 섹터의 confidence (0.0~1.0)
- 기존 점수를 기반으로 증폭하므로, 새로운 점수 체계를 만들지 않음

**섹터 매칭**: 정확 매칭 우선, fuzzy substring 매칭을 방어 수단으로 사용. LLM이 sector_taxonomy에서 벗어나는 경우를 대비한 안전장치.

```python
# KR 섹터: KRX 표준 26개 업종 (전기·전자, 화학, 제약 등)
# US 섹터: yfinance GICS 11개 업종 (Technology, Healthcare 등)
```

### Regime별 슬롯 배분 (`_get_regime_slots`)

| Regime | 탑다운 | 바텀업 | 근거 |
|--------|--------|--------|------|
| `strong_bull` | **2** | 1 | 강세장에서는 **섹터 로테이션이 수익의 핵심 드라이버**. 주도섹터 내 종목이 비주도섹터 종목보다 구조적으로 유리하므로 탑다운 2종목으로 섹터 베팅 강화 |
| `moderate_bull` | 1 | **2** | 상승세이나 확신 부족. 섹터 1종목으로 매크로 시그널 반영하되, 바텀업 2종목으로 개별 모멘텀 헤지 |
| `sideways` | 1 | **2** | 횡보장에서는 섹터 전체가 오르기보다 **개별 종목의 급등/거래량 시그널이 더 신뢰도 높음**. 탑다운 1종목만 유지 |
| `moderate_bear` | 1 | **2** | 약세장에서 매크로 에이전트의 leading_sectors는 자연스럽게 **방어섹터(유틸리티, 헬스케어, 필수소비재)로 전환**됨. 방어적 섹터 로테이션 1종목은 하락 리스크 관리에 유리 |
| `strong_bear` | 0 | **3** | 강한 하락장에서는 **어떤 섹터든 하락 가능**. 섹터 베팅 자체가 위험하므로 순수 바텀업으로 개별 종목의 기술적 시그널(반전, 거래량 이상)에만 의존 |
| (미인식) | 1 | **2** | 알 수 없는 regime은 `sideways` 기본값 적용 |

> **설계 결정(ADR)**: 점수 멀티플라이어 방식(Option B)도 검토했으나, max_selections=3에서 "왜 이 종목이 선정됐나"를 명확히 추적할 수 있는 슬롯 방식이 운영/디버깅에 유리하여 채택. ±0.1 보정의 확대판인 멀티플라이어는 같은 문제(불투명성)를 반복할 우려가 있었음.

### 3단계 선정 프로세스

**Phase 1 — 탑다운 슬롯 채우기**
1. 탑다운 풀에서 topdown_score 내림차순으로 정렬
2. regime가 부여한 탑다운 슬롯 수만큼 선정
3. 선정된 종목에 `SelectionChannel = "top-down"` 태깅

**Phase 2 — 바텀업 슬롯 채우기**
1. 기존 로직 그대로: 트리거별 최고점수 1종목씩
2. Phase 1에서 이미 선정된 종목은 제외
3. 선정된 종목에 `SelectionChannel = "bottom-up"` 태깅

**Phase 3 — 잔여 충원**
1. 아직 3종목 미달이면 전체 후보 점수순으로 채움
2. 탑다운 풀에 후보가 부족한 경우, 남은 슬롯은 자동으로 바텀업에 할당

### Fallback (안전장치)

| 상황 | 동작 |
|------|------|
| `macro_context = None` | 100% 바텀업 (기존 동작과 동일, 무회귀) |
| `leading_sectors` 비어있음 | 100% 바텀업 |
| `sector_map` 비어있음 | 100% 바텀업 |
| 탑다운 후보 < 배분 슬롯 | 남은 슬롯을 바텀업으로 자동 충원 |
| `market_regime` 미인식 | `sideways` 기본값 (1 탑다운 + 2 바텀업) |

### 관측성 (Observability)

**로그 출력**:
```
[TOP-DOWN] 005930 selected (sector=전기·전자, score=1.016, trigger=거래량 급증 상위주)
[BOTTOM-UP] 051910 selected (trigger=갭 상승 모멘텀 상위주)
[BOTTOM-UP] 003490 selected (fill, trigger=일중 상승률 상위주)
Selection summary: 1 top-down + 2 bottom-up = 3 total (regime=moderate_bull, strategy=hybrid_topdown_bottomup)
```

**JSON metadata**:
```json
{
  "selection_strategy": "hybrid_topdown_bottomup",
  "market_regime": "moderate_bull",
  "topdown_slots": 1,
  "bottomup_slots": 2,
  "topdown_count": 1,
  "bottomup_count": 2
}
```

**종목별 `selection_channel`**: 각 종목의 JSON 출력에 `"selection_channel": "top-down"` 또는 `"bottom-up"` 포함.

### KR vs US 차이점

| 항목 | KR (`trigger_batch.py`) | US (`us_trigger_batch.py`) |
|------|------------------------|---------------------------|
| 점수 컬럼 | `composite_score`, `final_score` | `CompositeScore`, `FinalScore` |
| 섹터 소스 | `macro_context["sector_map"]` (KRX 26개 업종) | `get_us_sector_map()` via yfinance (GICS 11개 업종) |
| 종목명 컬럼 | `stock_name` | `CompanyName` |
| 섹터 예시 | 전기·전자, 화학, 제약, 운송장비·부품 | Technology, Healthcare, Financial Services |

---

## 10. 최종 선별 로직 (`select_final_tickers`)

1. 각 트리거에서 상위 10개 후보 수집
2. 하이브리드 모드: 10일 데이터로 에이전트 점수 계산
3. **탑다운 풀 구성**: leading_sectors 매칭 + confidence 가중 (v2.5.3)
4. **Regime별 슬롯 배분**: 탑다운 N + 바텀업 M = 3 (v2.5.3)
5. Phase 1: 탑다운 슬롯 채우기
6. Phase 2: 바텀업 슬롯 채우기 (트리거별 Top 1)
7. Phase 3: 잔여 충원 (전체 점수순)
8. 중복 종목 제거, SelectionChannel 태깅

---

## 11. 사용법

### 기본 실행

```bash
# 오전 배치
python trigger_batch.py morning INFO

# 오후 배치
python trigger_batch.py afternoon INFO
```

### 옵션

```bash
# JSON 결과 저장
python trigger_batch.py afternoon INFO --output result.json

# 디버그 모드
python trigger_batch.py afternoon DEBUG
```

### 출력 예시 (JSON, v2.5.3)

```json
{
  "일중 상승률 상위주": [
    {
      "code": "272210",
      "name": "한화시스템",
      "current_price": 88700.0,
      "change_rate": 14.16,
      "volume": 14393649,
      "trade_value": 1220949944400.0,
      "agent_fit_score": 1.0,
      "risk_reward_ratio": 3.0,
      "stop_loss_pct": 5.0,
      "stop_loss_price": 84265.0,
      "target_price": 102005.0,
      "final_score": 1.0,
      "selection_channel": "top-down"
    }
  ],
  "마감 강도 상위주": [
    {
      "code": "420770",
      "name": "기가비스",
      "current_price": 40050.0,
      "change_rate": 18.49,
      "closing_strength": 0.93,
      "agent_fit_score": 1.0,
      "risk_reward_ratio": 3.0,
      "stop_loss_pct": 5.0,
      "final_score": 1.0,
      "selection_channel": "bottom-up"
    }
  ],
  "거래량 증가 상위 횡보주": [
    {
      "code": "017670",
      "name": "SK텔레콤",
      "current_price": 54300.0,
      "change_rate": 2.45,
      "agent_fit_score": 1.0,
      "risk_reward_ratio": 2.14,
      "stop_loss_pct": 7.0,
      "final_score": 1.0,
      "selection_channel": "bottom-up"
    }
  ],
  "metadata": {
    "run_time": "2026-03-11T09:30:00",
    "trigger_mode": "afternoon",
    "trade_date": "20260311",
    "selection_mode": "hybrid",
    "lookback_days": 10,
    "selection_strategy": "hybrid_topdown_bottomup",
    "market_regime": "moderate_bull",
    "topdown_slots": 1,
    "bottomup_slots": 2,
    "topdown_count": 1,
    "bottomup_count": 2
  }
}
```

---

## 부록: 함수 목록

| 함수명 | 카테고리 | 설명 |
|--------|----------|------|
| `get_snapshot` | 데이터 | 당일 OHLCV 조회 |
| `get_previous_snapshot` | 데이터 | 전일 OHLCV 조회 |
| `get_multi_day_ohlcv` | 데이터 | 종목별 N일간 OHLCV 조회 |
| `get_market_cap_df` | 데이터 | 시가총액 조회 |
| `apply_absolute_filters` | 필터 | 절대적 기준 필터 (100억+) |
| `filter_low_liquidity` | 필터 | 저유동성 필터 |
| `normalize_and_score` | 점수 | 정규화 및 복합점수 |
| `calculate_agent_fit_metrics` | 점수 | 에이전트 기준 점수 (v1.16.6 고정 손절폭) |
| `score_candidates_by_agent_criteria` | 점수 | 후보 종목 에이전트 점수 일괄 계산 |
| `enhance_dataframe` | 유틸 | 종목명/업종 추가 |
| `_get_regime_slots` | 선별 | regime → (탑다운, 바텀업) 슬롯 매핑 (v2.5.3) |
| `_build_topdown_pool` | 선별 | leading_sectors 매칭 + confidence 가중 탑다운 풀 구성 (v2.5.3) |
| `trigger_morning_volume_surge` | 오전 | 거래량 급증 |
| `trigger_morning_gap_up_momentum` | 오전 | 갭상승 모멘텀 |
| `trigger_morning_value_to_cap_ratio` | 오전 | 시총 대비 자금유입 |
| `trigger_afternoon_daily_rise_top` | 오후 | 일중 상승률 |
| `trigger_afternoon_closing_strength` | 오후 | 마감 강도 |
| `trigger_afternoon_volume_surge_flat` | 오후 | 거래량 증가 횡보 |
| `select_final_tickers` | 선별 | 탑다운+바텀업 하이브리드 최종 종목 선택 (v2.5.3) |
| `get_us_sector_map` | 데이터 | US 종목 → GICS 섹터 매핑 (US only) |
| `run_batch` | 실행 | 배치 실행 |

---

**Document Version**: 4.0
**Last Updated**: 2026-03-11
**Author**: PRISM-INSIGHT Development Team
