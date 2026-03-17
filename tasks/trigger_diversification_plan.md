# 트리거 다양화 기획안: 매크로 주도 + 역발상 가치 트리거

> **목표**: 약세/횡보장에서도 작동하는 트리거 추가
> **전략**: 기존 모멘텀 트리거(강세장용) + 신규 트리거(전 시장용)가 공존

---

## 1. 현행 파이프라인 (변경 없음)

```
trigger_batch.run_batch()
  ├── 기존 트리거 (모멘텀) → 후보 DataFrame 반환
  ├── [신규] 매크로 섹터 트리거 → 후보 DataFrame 반환
  ├── [신규] 역발상 가치 트리거 → 후보 DataFrame 반환
  └── select_final_tickers() → 전체 후보에서 최종 3종목 선정
        ↓
  orchestrator → 보고서 생성 → trading_agent 매매 판단
```

---

## 2. 신규 트리거 #1: 매크로 주도 섹터 트리거

### 컨셉
- 현행: macro_context의 leading_sectors가 기존 모멘텀 후보를 **재정렬**만 함
- 변경: leading_sectors에서 **직접 후보를 생성** (모멘텀 신호 불필요)
- 페르소나: 피터 린치 — "알고 있는 것에 투자하라, 성장을 합리적 가격에 사라"

### 로직 (함수: `trigger_macro_sector_leader`)

```python
def trigger_macro_sector_leader(trade_date, snapshot, prev_snapshot, cap_df, macro_context, top_n=10):
    """
    매크로 인텔리전스가 식별한 주도 섹터에서 최우수 종목을 선정.
    모멘텀 신호 없이도 후보 생성 가능 (약세장 대응).
    """
    1. macro_context에서 leading_sectors 추출
    2. 각 주도 섹터의 종목 리스트 확보 (yfinance 섹터 분류 or 사전 매핑)
    3. 스냅샷에서 해당 섹터 종목 필터링
    4. 스코어링:
       - 당일 상대강도 (시장 대비 수익률) × 0.3
       - 거래대금 × 0.2
       - 섹터 confidence (macro_context에서 제공) × 0.3
       - 시가총액 순위 (대형주 우선) × 0.2
    5. 상위 top_n 반환
```

### 필요 데이터
- `macro_context["leading_sectors"]` — 이미 존재
- `snapshot` (당일 OHLCV) — 이미 존재
- 섹터 매핑 — US: `get_us_sector_map()` 이미 존재 / KR: KRX 업종 분류 활용

### 진입 기준 (trading_agents.py)
- TRIGGER_CRITERIA에 추가:
  ```python
  "Macro Sector Leader": {"rr_target": 1.3, "sl_max": 0.07}
  ```
- 프롬프트 가이드: "이 종목은 거시경제 분석에서 주도 섹터로 식별된 업종의 대표주입니다. 섹터 순풍을 받고 있으므로 모멘텀 신호가 약해도 중기적 상승 가능성을 고려하세요."

---

## 3. 신규 트리거 #2: 역발상 가치 트리거

### 컨셉
- 최근 고점 대비 크게 하락했지만 펀더멘털이 건전한 종목
- 약세장에서 과매도된 우량주의 반등을 노림
- 페르소나: 하워드 막스 — "남들이 공포에 빠질 때 매수하라"

### 로직 (함수: `trigger_contrarian_value`)

```python
def trigger_contrarian_value(trade_date, snapshot, prev_snapshot, cap_df, top_n=10):
    """
    고점 대비 큰 하락 + 펀더멘털 건전 종목 발굴.
    약세/횡보장에서 바닥권 우량주 포착.
    """
    1. 스냅샷에서 거래대금 기준 필터 (유동성 확보)
    2. 개별 종목 52주 고가 대비 하락폭 계산 (yfinance)
       - 필터: -15% ~ -40% 하락 (너무 적으면 할인 아님, 너무 크면 펀더멘털 훼손)
    3. 펀더멘털 필터 (yfinance .info):
       - ROE > 10% 또는 영업이익률 > 5%
       - 부채비율 합리적 (debt_to_equity < 200%)
       - 최근 분기 매출 성장 (revenue_growth > 0)
    4. 스코어링:
       - 할인율 (고점 대비 하락폭) × 0.3
       - 펀더멘털 점수 (ROE, 이익률) × 0.4
       - 최근 3일 반등 신호 (Close > Open 2일 이상) × 0.3
    5. 상위 top_n 반환
```

### 데이터 소스
- US: yfinance (52주 고가, .info 펀더멘털)
- KR: krx_data_client (`get_market_ohlcv_by_date` + `get_market_fundamental_by_date`)

### 진입 기준 (trading_agents.py)
- TRIGGER_CRITERIA에 추가:
  ```python
  "Contrarian Value Pick": {"rr_target": 1.5, "sl_max": 0.08}
  ```
- 프롬프트 가이드: "이 종목은 최근 큰 폭 하락 후 펀더멘털 대비 저평가 상태입니다. 하락 원인이 일시적(시장 센티먼트)인지 구조적(실적 악화)인지 보고서에서 반드시 확인하세요. 구조적 문제가 있으면 미진입, 일시적이면 반등 시나리오 수립."
- 손절폭 -8%로 넉넉하게 (바닥권 종목은 변동성 큼)

### 성능 고려사항
- 52주 고가 계산 + 펀더멘털 조회는 개별 API 호출 필요
- 최적화: 스냅샷에서 1차 필터(거래대금, 당일 변동률) 후 상위 30~50개만 상세 조회
- yfinance 호출 병렬화 가능

---

## 4. 시장 체제별 트리거 활성화 전략

| 시장 체제 | 모멘텀 트리거 | 매크로 섹터 | 역발상 가치 |
|-----------|-------------|------------|------------|
| 강한 강세장 | **활성** (주력) | 활성 | 비활성 |
| 보통 강세장 | **활성** (주력) | 활성 | 비활성 |
| 횡보장 | 활성 | **활성** (주력) | 활성 |
| 보통 약세장 | 활성 (축소) | **활성** (주력) | **활성** (주력) |
| 강한 약세장 | 비활성 | 활성 (축소) | **활성** (주력) |

→ `run_batch()` 내에서 `macro_context["regime"]` 기반으로 분기

---

## 5. trading_agents.py 프롬프트 변경

### 트리거 유형별 분석 가이드 추가 (기존 구조에 삽입)

```
### 트리거 유형별 분석 포인트

**모멘텀 트리거** (Volume Surge, Gap Up, Intraday Rise, Closing Strength):
- 현재 접근법 유지 (윌리엄 오닐 / CAN SLIM)
- 모멘텀 지속성과 추세 방향 우선 판단

**매크로 섹터 트리거** (Macro Sector Leader):
- 섹터 순풍 확인 → 해당 종목이 섹터 내 리더인지 평가
- 단기 모멘텀보다 중기 성장성과 포지셔닝에 집중
- 보고서의 '시장 분석' 섹션에서 섹터 전망 비중 높게

**역발상 가치 트리거** (Contrarian Value Pick):
- 하락 원인 분석이 핵심 (일시적 vs 구조적)
- 보고서의 '기업 현황 분석'에서 재무 건전성 비중 높게
- 기술적 반등 신호(거래량 증가, 캔들 패턴) 확인
- 손절폭 넓게 (-8%), 목표가 높게 (손익비 1.5+)
```

---

## 6. 구현 순서

| 순서 | 작업 | 파일 | 비고 |
|------|------|------|------|
| 1 | TRIGGER_CRITERIA 추가 | us_trigger_batch.py | 2줄 추가 |
| 2 | `trigger_macro_sector_leader()` 구현 | us_trigger_batch.py | 스냅샷+macro_context 활용 |
| 3 | `trigger_contrarian_value()` 구현 | us_trigger_batch.py | yfinance 개별 조회 필요 |
| 4 | `run_batch()`에 신규 트리거 통합 | us_trigger_batch.py | 체제별 활성화 분기 |
| 5 | trading_agents.py 프롬프트 보강 | trading_agents.py | 트리거별 분석 가이드 |
| 6 | KR 포팅 | trigger_batch.py | krx_data_client 기반 |

---

## 7. 리스크 및 검증

- **역발상 트리거 오탐**: 구조적 하락 종목(실적 악화)을 잡을 수 있음 → 펀더멘털 필터가 핵심
- **매크로 트리거 지연**: macro_intelligence가 실시간이 아님 → 하루 전 분석 기반
- **API 호출 증가**: 역발상 트리거에서 개별 종목 yfinance 호출 → 30~50건으로 제한
- **검증**: 2주간 모의 운영 후 performance_tracker로 후행 성과 비교

---

## 8. KR 시장 포팅 검토

### 8-1. 데이터 소스 비교

| 데이터 | US (yfinance) | KR (krx_data_client) | KR 포팅 가능? |
|--------|--------------|---------------------|--------------|
| 당일 OHLCV 스냅샷 | `get_snapshot()` | `get_market_ohlcv_by_ticker()` | **동일** |
| 시가총액 | yfinance | `get_market_cap_by_ticker()` | **동일** |
| 섹터 분류 | `yfinance .info["sector"]` | `get_sector_info("KOSPI"/"KOSDAQ")` | **동일** (KRX 업종) |
| 과거 OHLCV | `yfinance download()` | `get_market_ohlcv_by_date()` | **동일** |
| PER/PBR | yfinance | `get_market_fundamental_by_date()` | **동일** |
| ROE/부채비율 | yfinance `.info` | **없음** | **차이점** |
| 섹터 매핑 | `get_us_sector_map()` | `prefetch_macro_intelligence_data()["sector_map"]` | **동일** |
| 주도/소외 섹터 | `macro_context["leading_sectors"]` | `macro_context["leading_sectors"]` | **동일** |

### 8-2. 트리거별 KR 포팅 가능성

#### 매크로 주도 섹터 트리거: **즉시 구현 가능**

KR에 이미 모든 인프라가 존재:
- `get_sector_info()` → 종목별 업종 매핑 (data_prefetch.py:176-189)
- `_build_topdown_pool()` → 주도 섹터 후보 빌드 (trigger_batch.py:840-890)
- `macro_context["leading_sectors"]` → 매크로 에이전트 출력
- `select_final_tickers()` → 하이브리드 선정에서 이미 top-down 로직 사용 중

현재는 top-down이 기존 모멘텀 후보를 **재정렬**만 하는데,
**독립 트리거로 분리**하면 모멘텀 없이도 주도 섹터 대표주를 직접 후보로 생성 가능.

구현 작업:
- `trigger_macro_sector_leader()` 함수를 US와 동일 로직으로 작성
- KRX 업종명 사용 (반도체, 건설, 금융 등 — KRX_STANDARD_SECTORS 활용)
- TRIGGER_CRITERIA에 `"매크로 섹터 리더": {"rr_target": 1.3, "sl_max": 0.07}` 추가

#### 역발상 가치 트리거: **간소화 버전 구현 가능**

**차이점**: KR에는 ROE/부채비율 개별 조회 API가 없음.
**대안**: krx_data_client의 PER/PBR 데이터로 간소화된 가치 필터 적용.

```
US 버전 (Full):
  52주 고가 대비 하락폭 + ROE + 부채비율 + 매출성장

KR 버전 (Simplified):
  52주 고가 대비 하락폭 + PBR < 섹터 평균 + PER 양수(흑자) + 반등 신호
```

KR 구현 로직:
```python
def trigger_contrarian_value(trade_date, snapshot, prev_snapshot, cap_df, top_n=10):
    1. 스냅샷에서 거래대금 기준 필터 (최소 100억원, 기존 모멘텀 트리거와 동일)
    2. get_market_ohlcv_by_date()로 52주 고가 계산 (상위 50개만)
       - 필터: 고점 대비 -15% ~ -40% 하락
    3. get_market_fundamental_by_date()로 PER/PBR 조회
       - PER > 0 (흑자 기업만)
       - PBR < 전체 중앙값 (상대적 저평가)
    4. 최근 3일 반등 신호 (Close > Open 2일 이상)
    5. 스코어링 후 상위 top_n 반환
```

**장점**: ROE 없이도 PER 양수(흑자) + PBR 저평가로 **구조적 하락 종목을 상당수 필터링** 가능.
PER 음수(적자) = 구조적 문제 가능성 높음 → 자동 제외.

### 8-3. KR 추가 고려사항

#### 성능
- KR `get_market_fundamental_by_date()`는 개별 종목 호출 → 50건 제한 필요
- US yfinance와 동일한 병목, 동일한 최적화 전략 적용

#### 트리거 네이밍 (한국어)
```python
# KR TRIGGER_CRITERIA 추가
"매크로 섹터 리더": {"rr_target": 1.3, "sl_max": 0.07},
"역발상 가치주": {"rr_target": 1.5, "sl_max": 0.08},
```

#### trading_agents.py 프롬프트 (KR)
- 기존 KR 프롬프트에 동일한 트리거별 분석 가이드 삽입
- 한국어 합쇼체 유지 (CLAUDE.md 규칙)

### 8-4. 수정된 구현 순서 (US → KR)

| 순서 | 작업 | 파일 | 비고 |
|------|------|------|------|
| **Phase 1: US 구현** | | | |
| 1 | TRIGGER_CRITERIA 추가 | prism-us/us_trigger_batch.py | 2줄 추가 |
| 2 | `trigger_macro_sector_leader()` | prism-us/us_trigger_batch.py | macro_context + yfinance |
| 3 | `trigger_contrarian_value()` | prism-us/us_trigger_batch.py | yfinance 펀더멘털 |
| 4 | `run_batch()` 통합 | prism-us/us_trigger_batch.py | 체제별 분기 |
| 5 | 프롬프트 보강 | prism-us/cores/agents/trading_agents.py | 트리거별 가이드 |
| 6 | 2주 모의 운영 | - | performance_tracker 확인 |
| **Phase 2: KR 포팅** | | | |
| 7 | TRIGGER_CRITERIA 추가 | trigger_batch.py | 한국어 트리거명 |
| 8 | `trigger_macro_sector_leader()` | trigger_batch.py | krx_data_client + sector_map |
| 9 | `trigger_contrarian_value()` | trigger_batch.py | PER/PBR 간소화 버전 |
| 10 | `run_batch()` 통합 | trigger_batch.py | 체제별 분기 |
| 11 | 프롬프트 보강 | cores/agents/trading_agents.py | KR 프롬프트 동기화 |

### 8-5. KR 포팅 결론

| 트리거 | KR 포팅 | 난이도 | 데이터 차이 |
|--------|---------|--------|------------|
| 매크로 섹터 리더 | **즉시 가능** | 낮음 | 없음 (모든 인프라 존재) |
| 역발상 가치주 | **간소화 버전** | 중간 | ROE 없음 → PER/PBR로 대체 |

KR의 PER/PBR 기반 간소화 버전이 오히려 더 실용적일 수 있음:
- ROE 단독보다 PBR(주가순자산비율)이 한국 시장에서 가치 판단에 더 널리 사용됨
- PER 양수 필터만으로도 구조적 하락(적자 기업) 제거 효과 충분
