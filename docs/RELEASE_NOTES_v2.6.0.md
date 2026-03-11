# PRISM-INSIGHT v2.6.0

발표일: 2026년 3월 11일

## 개요

PRISM-INSIGHT v2.6.0은 **거시경제 인텔리전스(Macro Intelligence)** 를 핵심 분석 파이프라인에 통합하고, 종목 선정 방식을 **탑다운+바텀업 하이브리드**로 전면 개편한 마이너 버전입니다.

기존에는 급등·거래량 등 개별 종목 시그널(바텀업)만으로 종목을 선정했으나, 이번 버전부터 **시장 체제(bull/bear) 판단 → 주도 섹터 식별 → 섹터 내 유망주 선별**이라는 탑다운 분석이 추가되어, 시장 흐름에 맞는 보다 정교한 종목 선정이 가능해졌습니다.

**주요 수치:**
- 총 13개 PR (#192 ~ #205)
- 28개 파일 변경
- +4,200 / -290 라인

---

## 주요 변경사항

### 1. 거시경제 인텔리전스 에이전트 (Macro Intelligence) ⭐ NEW

AI 에이전트가 **실시간 거시경제 데이터를 분석**하여 시장 체제(regime)를 판단하고, 주도/낙후 섹터를 식별합니다. 한국 시장과 미국 시장 모두 지원합니다.

#### 1.1 분석 항목

| 항목 | 설명 |
|------|------|
| **시장 체제 판단** | S&P 500, NASDAQ, VIX 등 지수 데이터를 기반으로 5단계 체제 분류 (strong_bull / moderate_bull / sideways / moderate_bear / strong_bear) |
| **주도 섹터 식별** | Perplexity AI 검색을 통해 현재 시장을 이끄는 섹터와 뒤처지는 섹터를 실시간 파악 |
| **리스크 이벤트** | 금리 결정, 실적 시즌 등 단기 영향 이벤트 요약 |
| **시장 보고서** | 분석 리포트에 거시경제 요약 섹션 자동 삽입 |

#### 1.2 체제 분류 방식

프로그래밍 기반으로 체제를 먼저 계산한 뒤, LLM 에이전트가 정성적 분석을 보완하는 **하이브리드 방식**입니다.

```
지수 데이터 (S&P 500, NASDAQ, VIX)
  → 이동평균·변동성 기반 프로그래밍 체제 판별
  → Perplexity AI로 정성적 섹터·리스크 분석
  → 최종 macro_context 생성
```

> LLM 토큰 비용을 최소화하기 위해, 정량적 판단은 코드로 처리하고 LLM은 검색·요약에만 집중합니다.

#### 1.3 적용 파일

| 파일 | 역할 |
|------|------|
| `cores/agents/macro_intelligence_agent.py` | KR 거시경제 에이전트 |
| `prism-us/cores/agents/macro_intelligence_agent.py` | US 거시경제 에이전트 |
| `cores/data_prefetch.py` | KR 지수 데이터 프리페치 + 프로그래밍 체제 계산 |
| `prism-us/cores/data_prefetch.py` | US 지수 데이터 프리페치 + 프로그래밍 체제 계산 |

---

### 2. 탑다운+바텀업 하이브리드 종목 선정 ⭐ NEW

기존의 바텀업(급등·거래량 시그널) 선정에 탑다운(거시경제 → 주도섹터 → 섹터 내 유망주) 채널을 추가했습니다.

#### 2.1 선정 방식

```
[탑다운 채널]                    [바텀업 채널]
 거시경제 체제 판단                 급등 감지
 → 주도 섹터 식별                  거래량 급증 감지
 → 섹터 내 종목 풀 생성            → composite_score 산출
 → topdown_score 산출

         ↓                           ↓
    ┌──────────────────────────────────┐
    │   시장 체제(regime)별 슬롯 배분   │
    │   → 최종 3종목 선정              │
    └──────────────────────────────────┘
```

#### 2.2 시장 체제별 슬롯 배분

시장 상황에 따라 탑다운과 바텀업의 비중을 자동 조절합니다.

| 시장 체제 | 탑다운 슬롯 | 바텀업 슬롯 | 근거 |
|-----------|:---------:|:---------:|------|
| **강세장** (strong_bull) | 2 | 1 | 강한 상승장에서는 섹터 로테이션이 명확 — 주도 섹터의 대형주가 지수를 견인 |
| **온건 강세** (moderate_bull) | 1 | 2 | 섹터 로테이션이 불안정, 개별 모멘텀 종목이 아웃퍼폼할 수 있음 |
| **횡보** (sideways) | 1 | 2 | 뚜렷한 주도 섹터 부재, 개별 이벤트(실적·신약·계약) 기반 종목이 유리 |
| **온건 약세** (moderate_bear) | 1 | 2 | 방어 섹터 1종목 확보 + 역발상 반등주 포착 |
| **강한 약세** (strong_bear) | 0 | 3 | 섹터 전반 하락 시 탑다운 무의미, 기술적 반등 시그널에만 집중 |

#### 2.3 선정 예시

```
시장 체제: moderate_bear (온건 약세)
→ 탑다운 1슬롯, 바텀업 2슬롯

[TOP-DOWN]  HON (Honeywell) — 주도섹터 Industrials, topdown_score=0.839
[BOTTOM-UP] VRTX (Vertex Pharma) — Volume Surge Top-1
[BOTTOM-UP] SNDK (Sandisk) — Gap Up Momentum Top-1
```

#### 2.4 적용 범위

KR(`trigger_batch.py`)과 US(`prism-us/us_trigger_batch.py`) **모두 동일하게 적용**됩니다. 기존 트리거 감지 알고리즘(급등·거래량·밸류)은 변경 없이 유지하고, 최종 선정 단계(`select_final_tickers`)만 하이브리드로 개선했습니다.

---

### 3. 매매 에이전트 거시경제 연동 ⭐ NEW

매수·매도 판단 에이전트에 시장 체제(regime) 정보가 전달되어, **시장 상황을 고려한 매매 판단**이 가능해졌습니다.

#### 3.1 변경 내용

| 항목 | Before | After |
|------|--------|-------|
| **매수 판단** | 종목 데이터만 참조 | 종목 데이터 + 시장 체제 + 주도/낙후 섹터 |
| **매도 판단** | 종목 데이터만 참조 | 종목 데이터 + 시장 체제 |
| **프레이밍 바이어스** | "매수를 고려하라" 유도 | 중립적 표현으로 수정 ("매수 여부를 판단하라") |
| **섹터명** | 에이전트별 하드코딩 | 동적 주입 (KR: `get_sector_info()`, US: GICS 표준) |

#### 3.2 프레이밍 바이어스 제거

기존 매수 에이전트 프롬프트에 "이 종목의 매수를 고려하라"라는 편향된 지시가 있어, AI가 매수 쪽으로 치우치는 경향이 있었습니다. 이를 **"이 종목의 매수 여부를 판단하라"**로 수정하여 중립적 분석을 유도합니다.

---

### 4. US Orchestrator --date 옵션 추가

미국 시장이 닫혀 있을 때도 과거 날짜로 전체 파이프라인을 테스트할 수 있습니다.

```bash
# 어제(2026-03-10) 날짜로 US 전체 파이프라인 실행
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram --date 20260310
```

---

### 5. US 대시보드 매매 인사이트 수정 (#201)

| 문제 | 수정 |
|------|------|
| 매매 인사이트 조회 시 `us_trading_history`만 참조 | `us_holding_decisions` 테이블도 통합 조회 |
| 누적수익률 계산 오류 | 실현 손익 기반 정확한 누적수익률 산출 |

---

### 6. Trading Agent 프롬프트 개선 (#202)

매매 에이전트가 SQLite 데이터베이스 컬럼명을 추측하여 쿼리 오류가 발생하던 문제를 수정했습니다.

```
Before: SELECT quantity FROM stock_holdings  → "no such column: quantity" 에러
After:  테이블 조회 전 반드시 describe_table 실행 → 실제 컬럼명 확인 후 쿼리
```

**적용 범위:** KR/US 매수·매도 에이전트 프롬프트 4곳 (한국어/영어)

---

### 7. 텔레그램 시그널 얼럿 강화 (#205)

하이브리드 종목선정 도입에 맞춰 KR/US 텔레그램 시그널 얼럿 메시지를 강화했습니다.

| 항목 | Before | After |
|------|--------|-------|
| **헤더** | 종목 목록만 표시 | 🧭 시장국면 + 탑다운/바텀업 선정 수 요약 |
| **종목별 정보** | 가격, 등락률, 트리거 지표만 | + 📌 선정 채널 (탑다운 주도섹터 / 바텀업 개별종목) |
| **스코어링** | 없음 | + 📊 하이브리드 점수 \| R/R 비율 \| 손절폭 |
| **PDF 날짜** | 재무 데이터 날짜가 추출됨 | 발행일(Publication Date) 우선 + 마크다운 볼드 처리 |

US 얼럿은 다국어(ko/en) 모두 지원합니다.

---

### 8. US score-decision override 버그 수정 (#203)

US 매수 에이전트가 "미진입"으로 판단했는데 점수가 기준 이상이면 강제로 "진입"으로 뒤집는 버그를 수정했습니다. KR 로직과 동일하게 AI 결정을 존중하도록 변경했습니다.

---

### 9. US trigger results 파일 경로 통일 (#204)

orchestrator가 trigger results JSON을 프로젝트 루트(CWD)에 저장하고, telegram_summary_agent가 `prism-us/` 디렉토리에서 찾아 trigger type 감지에 실패하던 문제를 수정했습니다. 모든 경로를 `PRISM_US_DIR` 절대경로로 통일했습니다.

---

### 10. 기타 버그 수정

| PR | 문제 | 수정 |
|----|------|------|
| #192 | FCM 푸시 알림 `lang` 필터링 및 payload 필드 누락 | 필터 로직 수정 + payload 필드 추가 |
| #193 | Firebase 메시지 타입/마켓 오감지 | `detect_type()`, `detect_market()` 로직 정밀화 |
| #194 | `tabulate` 의존성 누락 | `requirements.txt`에 추가 |
| #195 | FCM `lang` 미설정 디바이스 알림 누락 | `lang` 없는 디바이스도 발송 대상에 포함 |
| #196 | FCM `NOT_FOUND` 에러코드 미처리 → 만료 토큰 반복 실패 | `_INVALID_TOKEN_CODES`에 `NOT_FOUND` 추가 |
| #197 | GPT-5 reasoning 모델 다중 JSON 출력으로 evaluator 파싱 실패 | `_RobustEvaluatorLLM` 래퍼 + `generate_str()` fallback |
| #185 | `quickstart.sh`에서 pip 없을 때 설치 실패 | `uv` fallback 추가 |
| #187 | 포트폴리오 조정 메시지에 방향(상향/하향) 미표시 | 방향 라벨 추가 (**첫 외부 기여** 🌟) |

---

## 변경된 파일

| 파일 | 주요 PR | 변경 내용 |
|------|---------|-----------|
| `cores/agents/macro_intelligence_agent.py` | #202 | KR 거시경제 에이전트 신규 |
| `prism-us/cores/agents/macro_intelligence_agent.py` | #202 | US 거시경제 에이전트 신규 |
| `cores/data_prefetch.py` | #202 | KR 지수 프리페치 + 프로그래밍 체제 계산 추가 |
| `prism-us/cores/data_prefetch.py` | #202 | US 지수 프리페치 + 프로그래밍 체제 계산 추가 |
| `trigger_batch.py` | #202 | 하이브리드 탑다운+바텀업 선정 구현 |
| `prism-us/us_trigger_batch.py` | #202 | 하이브리드 탑다운+바텀업 선정 구현 (US) |
| `cores/agents/trading_agents.py` | #202 | 매매 에이전트 macro regime 연동 + describe_table 지시 추가 |
| `prism-us/cores/agents/trading_agents.py` | #202 | 매매 에이전트 macro regime 연동 + describe_table 지시 추가 (US) |
| `cores/analysis.py` | #202 | 분석 파이프라인에 macro_context 삽입 |
| `prism-us/cores/us_analysis.py` | #202 | 분석 파이프라인에 macro_context 삽입 (US) |
| `stock_analysis_orchestrator.py` | #202, #205 | macro intelligence 실행 + 파이프라인 연동 + 시그널 얼럿 강화 |
| `prism-us/us_stock_analysis_orchestrator.py` | #202, #204, #205 | macro intelligence 실행 + --date 옵션 + 파이프라인 연동 + 경로 수정 + 시그널 얼럿 강화 |
| `stock_tracking_agent.py` | #202 | sector_names 동적 전달 |
| `stock_tracking_enhanced_agent.py` | #202 | sector_names 파라미터 추가 |
| `prism-us/us_stock_tracking_agent.py` | #202, #203 | sector_names 동적 전달 (US) + score-decision override 버그 수정 |
| `docs/TRIGGER_BATCH_ALGORITHMS.md` | #202 | v4.0 현행화 (하이브리드 선정 문서) |
| `docs/MACRO_INTELLIGENCE_PLAN.md` | #202 | 설계 문서 |
| `pdf_converter.py` | #205 | 발행일 날짜 추출 regex 수정 (마크다운 볼드 처리) |
| `examples/generate_us_dashboard_json.py` | #201 | 매매 인사이트 통합 조회 + 누적수익률 수정 |
| `firebase_bridge.py` | #193, #195, #196 | FCM 토큰 정리, lang 필터 수정 |
| `telegram_summary_agent.py` | #197 | 다중 JSON 파싱 robust 처리 |
| `telegram_bot_agent.py` | #193 | 메시지 타입 감지 정밀화 |
| `quickstart.sh` | #185 🌟 | uv fallback 추가 (**외부 기여**) |
| `requirements.txt` | #194 | tabulate 의존성 추가 |

---

## 업데이트 방법

### 1. 코드 업데이트

```bash
git pull origin main
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
# 또는
pip install tabulate pandas_market_calendars lxml
```

### 3. 동작 확인

```bash
# KR 트리거 배치 테스트
python trigger_batch.py morning INFO

# US 트리거 배치 테스트 (과거 날짜)
python prism-us/us_trigger_batch.py morning INFO

# KR 전체 파이프라인 (텔레그램 없이)
python stock_analysis_orchestrator.py --mode morning --no-telegram

# US 전체 파이프라인 (과거 날짜, 텔레그램 없이)
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram --date 20260310
```

---

## 알려진 제한사항

1. **탑다운 후보 부족 시**: 주도 섹터에 해당하는 트리거 종목이 없으면 탑다운 슬롯이 바텀업으로 자동 보충됩니다. 이는 의도된 동작입니다.
2. **Perplexity API 의존**: 거시경제 에이전트는 Perplexity MCP 서버를 사용합니다. API 키가 없으면 macro intelligence 없이 기존 바텀업 방식으로 자동 fallback됩니다.
3. **Value-to-Cap 트리거**: US 시장에서 시가총액 데이터 미제공 시 해당 트리거가 스킵됩니다 (기존과 동일).

---

## 텔레그램 구독자 공지 메시지

### 한국어

```
🚀 PRISM-INSIGHT v2.6.0 업데이트

안녕하세요, 구독자 여러분.
v2.6.0이 배포되었습니다. 주요 변경사항을 안내드립니다.

✅ 주요 업데이트

🧠 거시경제 인텔리전스 도입
AI가 시장 전체 흐름(강세/약세/횡보)을 실시간으로 판단하고,
현재 주도하는 섹터를 자동으로 식별합니다.
분석 리포트에 거시경제 요약이 함께 포함됩니다.

📊 종목 선정 방식 전면 개편
기존: 급등·거래량 시그널만으로 종목 선정 (바텀업)
변경: 시장 흐름 분석(탑다운) + 개별 시그널(바텀업) 하이브리드 선정

강세장에서는 주도 섹터 중심으로,
약세장에서는 기술적 반등 시그널 중심으로 종목을 선정합니다.

🎯 매매 판단 고도화
매수·매도 판단 시 AI가 거시경제 체제(강세/약세)와 주도·낙후 섹터
정보를 함께 참고합니다. 예를 들어 약세장에서 낙후 섹터 종목은
매수 기준이 엄격해지고, 주도 섹터 종목은 상대적으로 유리하게
평가됩니다. 또한 기존 매수 에이전트의 "매수를 고려하라"는
편향된 지시를 "매수 여부를 판단하라"로 수정하여
중립적 분석을 유도합니다.

📱 텔레그램 시그널 얼럿 강화
시그널 얼럿 메시지에 다음 정보가 추가됩니다:
- 시장국면 (강세/약세/횡보) 및 탑다운/바텀업 선정 요약
- 종목별 선정 채널 (주도섹터 탑다운 / 개별종목 바텀업)
- 하이브리드 점수, 리스크/리워드 비율, 손절폭

🔧 기타 개선
- US 매수 시 AI 결정 무시 버그 수정 (점수만으로 강제 진입 방지)
- US 대시보드 매매 인사이트 조회 및 누적수익률 계산 수정
- FCM 푸시 알림 안정성 개선 (만료 토큰 처리, 언어 필터 수정)
- AI 모델 응답 파싱 안정성 강화

감사합니다.
좋은 하루 되세요~
```

### English

```
🚀 PRISM-INSIGHT v2.6.0 is now live!

Hello, subscribers.
We've just released v2.6.0. Here's a summary of the key updates.

✅ Key Updates

🧠 Macro Intelligence Integration
AI now analyzes the overall market regime (bull/bear/sideways) in real-time
and automatically identifies leading and lagging sectors.
A macro economy summary is now included in every analysis report.

📊 Hybrid Stock Selection — Complete Overhaul
Before: Stocks selected only by surge/volume signals (bottom-up)
After:  Market trend analysis (top-down) + individual signals (bottom-up)

In bull markets, selection favors leading sector stocks.
In bear markets, selection focuses on technical bounce signals.

🎯 Smarter Buy/Sell Decisions
Buy and sell agents now reference macro regime (bull/bear) and
leading/lagging sector data alongside individual stock analysis.
For example, lagging-sector stocks face stricter buy criteria in
a bear market, while leading-sector stocks are evaluated more
favorably. The buy agent prompt was also corrected from
"consider buying this stock" to "evaluate whether to buy"
to eliminate directional bias.

📱 Enhanced Telegram Signal Alerts
Signal alert messages now include:
- Market regime (bull/bear/sideways) and top-down/bottom-up selection summary
- Per-stock selection channel (leading sector top-down / individual bottom-up)
- Hybrid score, risk/reward ratio, and stop-loss percentage

🔧 Other Improvements
- Fixed US buy agent overriding AI decisions based on score alone
- Fixed US dashboard trade insights and cumulative return calculation
- Improved FCM push notification stability (expired token handling, language filter)
- Enhanced AI model response parsing reliability

Thank you.
Have a great day~
```
