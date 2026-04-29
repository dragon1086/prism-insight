# PRISM-INSIGHT v2.11.0

발표일: 2026년 4월 29일

## 개요

PRISM-INSIGHT v2.11.0은 **Investment Strategist 매수 결정 엔진의 근본적 재구성**이 핵심입니다.

오랜 시간 누적되며 모순이 쌓여 있던 매수 판단 프롬프트를, 윌리엄 오닐의 **CAN SLIM** 철학을 단일 축으로 재설계했습니다. "탄탄한 기업 + 모멘텀 + 시장 추세에 따른 적극성 조절"이라는 본래 의도가 명확히 드러나도록, 정체성 충돌·중복 규칙·모순된 도피처를 정리하고, 시장 체제 5단계 (`strong_bull`/`moderate_bull`/`sideways`/`moderate_bear`/`strong_bear`)별 진입 기준을 단일 매트릭스로 통일했습니다.

부수적으로 텔레그램 봇 UX 개선 3건이 함께 들어갔습니다.

**주요 수치:**
- 총 5개 PR (#264, #265, #267, #268, +매매 에이전트 모델 업그레이드)
- 14개 파일 변경
- KR/US 매수 프롬프트 -22% 압축 (2,294 → 1,794 lines)
- 프롬프트 회귀 테스트 8개 → 74개 (+825%)
- 매매 에이전트 모델 `gpt-5.4` → `gpt-5.5` (KR/US 4곳)

---

## 주요 변경사항

### 1. Investment Strategist 프롬프트 CAN SLIM 재구성 (PR #268) ⭐ 핵심 변경

KR/US 양쪽 매수 결정 에이전트(`create_trading_scenario_agent`, `create_us_trading_scenario_agent`)의 시스템 프롬프트를 단일 CAN SLIM 프레임으로 재설계했습니다.

#### 기존 프롬프트가 가진 5가지 구조적 결함

| # | 결함 | 영향 |
|---|------|------|
| 1 | "가치투자 + 모멘텀 추종" 양립 불가 정체성 | 같은 종목을 두 프레임으로 평가, 결과 일관성 부족 |
| 2 | min_score 표가 JSON 스키마에만, 본문 진입 가이드와 불일치 | PR #265의 `strong_bull=4` 변경이 실제 행동에 반영 안 될 위험 |
| 3 | "애매하면 미진입" + "강세장 진입 우선"의 무한 회색지대 | LLM이 어떤 결정도 정당화 가능 → 회피 편향 강화 |
| 4 | R/R "참고 기준" — floor 명시 없음 | R/R 1.0 미만 setup도 강세장 예외로 진입 정당화 가능 |
| 5 | 포트폴리오 7+ 보유 제약 vs 강세장 6점 진입 가이드 충돌 | 우선순위 명시 없음 |

#### CAN SLIM × 보고서 매핑 (신규)

| 요소 | 의미 | 보고서 섹션 |
|---|---|---|
| C — 분기 실적 | 최근 분기 EPS/매출 가속화 | 2-1 기업 현황 |
| A — 연간 실적 | 다년 EPS 성장, ROE | 2-1 기업 현황 |
| N — New | 신제품/신규 catalyst/신고가 | 3 뉴스, 1-1 주가 |
| S — 수급 | 거래량, 매집 흔적 | 1-1, 1-2 |
| L — 리더 | 업종 내 리더 위치 | 2-2, 4 |
| I — 기관 매수 | 외국인 + 기관 누적 순매수 | 1-2 |
| M — 시장 추세 | 시장 체제, 주도 섹터 | 4 시장 분석 |

#### 1단계 — 펀더멘털 게이트 F1~F4 (필수)

매 진입 전 4가지 이진 체크. **"탄탄한 기업"의 정량 정의**입니다.

| 체크 | 통과 기준 | 출처 |
|---|---|---|
| F1 수익성 | 최근 2개 분기 영업이익 흑자 (또는 흑자 전환 신호 명확) | 2-1 |
| F2 재무 건전성 | 부채비율 < 200% OR 업종 평균 이하 | 2-1 |
| F3 성장성 | ROE ≥ 5% OR 최근 2년 매출 성장 ≥ 10% | 2-1 |
| F4 사업 명확성 | 사업 모델 + 경쟁우위가 보고서에서 식별됨 | 2-2 |

- strong_bull / moderate_bull: 1개 미달이라도 명확한 보완 근거 + rejection_reason null이면 진입 검토
- sideways / moderate_bear / strong_bear: 1개라도 미달 → 미진입

#### 2단계 — 시장 체제별 진입 매트릭스 (단일 기준점)

KR/US 동일 수치로 통일:

| 시장 체제 | min_score | 손익비 floor | 최대 손절폭 | 모멘텀 신호 | 추가 확인 |
|---|---|---|---|---|---|
| strong_bull | 4 | 1.0 | -7% | 1개+ | 0 |
| moderate_bull | 4 | 1.2 | -7% | 1개+ | 0 |
| sideways | 5 | 1.3 | -6% | 1개+ | 0 |
| moderate_bear | 5 | 1.5 | -5% | 2개+ | 1 |
| strong_bear | 6 | 1.8 | -5% | 2개+ | 1 |

**적극화 논리**: 펀더 게이트가 1단계 quality 필터로 작동하므로, 매트릭스 자체를 기존보다 1단계씩 적극화했습니다.
- moderate_bull: 5 → **4** (탄탄한 종목 + 강세장이면 못 사는 게 손해)
- moderate_bear: 6 → **5** (강한 모멘텀이면 약세장도 진입)
- strong_bear: 7 → **6** (강한 약세장이라도 좋은 종목은 살림)
- 횡보장 손절 -5% → **-6%**
- R/R floor 전반 0.2~0.3 완화

#### 모멘텀 신호 정량 정의 (5개)

기존의 모호한 "모멘텀 가산점"을 정량 기준으로 정리:

1. 거래량 20일 평균 대비 200% 이상 (당일 또는 최근 3거래일 내)
2. 외국인 + 기관 3거래일 연속 순매수 (US: 기관, 보고서 명시 시)
3. 52주 신고가 95% 이상 근접
4. 섹터 전체 상승 추세
5. 직전 박스 상단 거래량 동반 돌파 (단순 터치 X, 박스 업그레이드 O) ← 신규

트리거 유형별 자동 가산도 명시:
- 거래량 급증 / 갭 상승 / 일중 상승률 / 마감 강도 / 시총 대비 자금 유입 / 거래량 증가 횡보주 → 모멘텀 신호 1점 자동 인정
- 매크로 섹터 리더 → 추가 확인 +1
- 역발상 가치주 → F1~F4 통과 + 일시적 하락일 때만 진입 (구조적 하락은 미진입)

#### 미진입 사유 정리 (단독 / 복합 / 금지)

**단독 사유 (한 가지만 충족):**
1. 손절 지지선 -10% 이하
2. PER ≥ 업종 평균 2.5배
3. 펀더 게이트 미달 + 시장이 sideways/bear
4. severity = "high" 리스크 이벤트 직접 피해
5. effective_score < 현재 regime의 min_score

**복합 사유 (둘 다 충족):**
6. (RSI ≥ 85 OR 20일선 괴리율 ≥ +25%) AND (외국인 + 기관 5거래일+ 순매도)

**rejection_reason 사용 금지 표현:**
- "과열 우려", "변곡 신호", "추가 확인 필요", "단기 조정 가능성", "관망이 안전"
- 시스템에 "다음 기회"가 없으므로 막연한 회피 표현은 부적절

#### 거시 보정을 buy_score에서 분리 (`macro_adjustment` 신규 필드)

기존: 주도 섹터 +1 / 소외 섹터 -1 가산점이 `buy_score`에 직접 합산
- → 6점이 "본질 6점"인지 "본질 5점 + 섹터 +1"인지 식별 불가

개정:
- `macro_adjustment` 별도 필드 (-1, 0, +1)
- `effective_score = buy_score + macro_adjustment`
- min_score 비교는 `effective_score`로
- 종목 본질 점수와 거시 보정을 분리해 후행평가/추적 가능

#### 도피처 제거

| 기존 | 개정 |
|---|---|
| "If unclear, choose No Entry" | "어떤 부분이 불확실한지 rationale에 *구체적으로* 명시한 뒤 진입/미진입 결정" |
| "R/R 1.2+ (참고)" — floor 미달도 통과 가능 | floor 미달 → 자동 미진입 (rejection_reason에 "R/R floor 미달" 명시) |
| "조건부 관망" 4회 반복 → 메시지 분산 | 시스템 제약에서 1회로 통합 |

#### 다중 계좌 환경 (v2.9.0+) 가이드 추가

`stock_holdings` / `us_stock_holdings` 조회 시 `account_id = 'primary'` 필터 가이드 추가. 이전에는 어느 계좌 기준인지 불명확.

#### KR/US 비대칭 해소

- KO 블록을 master로, EN 블록은 1:1 미러링 (이전: KO/EN이 미세하게 다른 표현)
- KR은 KOSPI 기반 regime, US는 S&P 500 + VIX 기반 regime — 시장별 차이만 자연스럽게 분기

#### 신규 JSON 응답 필드 (additive — 기존 필드 보존)

| 필드 | 설명 |
|---|---|
| `fundamental_check` | F1~F4 각 항목의 통과/미달 + 1줄 근거, `all_passed` 불리언 |
| `macro_adjustment` | 거시 보정 (-1, 0, +1) |
| `effective_score` | `buy_score + macro_adjustment` |
| `momentum_signal_count` | 충족된 모멘텀 신호 개수 (0~5) |
| `additional_confirmation_count` | 충족된 추가 확인 개수 (0~5) |

> 다운스트림 호환성: `entry_checklist_passed` 등 대시보드/번역 모듈이 사용하는 모든 기존 필드는 그대로 보존됩니다.

---

### 2. Firecrawl 명령어 응답에서 LLM 투자 경고 중복 제거 (PR #267)

`/signal`, `/theme`, `/ask` 등 Firecrawl 명령어가 LLM의 답변 끝에 종종 "본 정보는 투자 권유가 아닙니다" 같은 면책 문구를 자체적으로 붙이는데, 시스템에서 이미 동일한 문구를 footer로 추가하고 있어 같은 메시지가 두 번 표시되던 문제를 해결했습니다.

| 항목 | 설명 |
|---|---|
| **신규 모듈** | `cores/disclaimer_utils.py` — LLM이 만든 투자 경고 패턴을 탐지·제거 |
| **`telegram_ai_bot.py`** | Firecrawl 응답 sanitize 로직 추가 |
| **테스트** | `tests/test_strip_trailing_disclaimer.py` — 12개 패턴 검증 |

---

### 3. KR 강한 강세장 min_score 5→4 (US와 통일) (PR #265)

KR Investment Strategist 프롬프트의 strong_bull regime min_score를 4점으로 하향 조정해 US와 일관성을 맞췄습니다. **buy_score 4점부터 진입 가능**해져 강한 강세장에서 더 적극적인 매수 성향을 갖습니다.

> 참고: PR #268로 새 매트릭스에도 이 값이 그대로 반영되었습니다.

---

### 4. 텔레그램 봇 UX 개선 (PR #264)

- **`/us_evaluate <ticker>`**: 입력된 티커를 자동으로 대문자 변환 (예: `aapl` → `AAPL`)
- **`/cancel` 전체 핸들러 적용**: 모든 대화형 명령에서 일관되게 `/cancel`로 진행 중인 흐름을 중단할 수 있도록 통일

---

### 5. 매매 에이전트 LLM 모델 업그레이드 (gpt-5.4 → gpt-5.5)

KR/US 매매 추적 에이전트에서 매수/매도 의사결정에 사용하는 LLM 모델을 `gpt-5.4`에서 `gpt-5.5`로 상향 조정했습니다. CAN SLIM 매트릭스의 정량 평가(F1~F4 게이트, 5단계 매트릭스, 모멘텀 신호 카운트, 거시 보정)를 더 일관되게 수행할 수 있는 추론 능력 확보가 목적입니다.

| 파일 | 위치 | 용도 |
|---|---|---|
| `stock_tracking_enhanced_agent.py` | line 950 | KR 강화 추적 에이전트의 매도 결정 LLM 호출 |
| `stock_tracking_agent.py` | line 399 | KR 매매 추적 에이전트의 매수/매도 결정 LLM 호출 |
| `prism-us/us_stock_tracking_agent.py` | line 718 | US 매매 추적 에이전트의 매수/매도 결정 |
| `prism-us/us_stock_tracking_agent.py` | line 1330 | US 매매 추적 에이전트의 보조 분석 |

> 비용은 소폭 증가할 수 있습니다. 다른 모듈(report generation, telegram summary, archive query, compression 등)은 `gpt-5.4-mini` / `gpt-5.4`를 그대로 유지합니다 — 매매 결정 외 영역은 비용 효율을 우선합니다.

---

## 변경된 주요 파일

| 파일 | PR | 변경 내용 |
|------|----|-----------|
| `cores/agents/trading_agents.py` | #265, #268 | KR Investment Strategist 프롬프트 재구성, min_score 4 |
| `prism-us/cores/agents/trading_agents.py` | #265, #268 | US Investment Strategist 프롬프트 재구성 |
| `tests/test_trading_agents_prompt_rules.py` | #268 | KR 프롬프트 회귀 테스트 6 → 34개 |
| `prism-us/tests/test_trading_agents_prompt_rules.py` | #268 | US 프롬프트 회귀 테스트 2 → 40개 |
| `cores/disclaimer_utils.py` (신규) | #267 | LLM 면책 문구 탐지·제거 유틸 |
| `tests/test_strip_trailing_disclaimer.py` (신규) | #267 | 면책 문구 중복 제거 테스트 |
| `telegram_ai_bot.py` | #264, #267 | 티커 자동 대문자, /cancel 통일, 면책 sanitize |
| `docs/archive/USAGE.md` | (보조) | 아카이브 사용 문서 보강 |

---

## 업데이트 방법

### 1. 코드 업데이트

```bash
git pull origin main
```

### 2. 의존성 (변경 없음)

이번 릴리즈는 새 패키지 의존성을 추가하지 않습니다. 기존 환경 그대로 사용 가능합니다.

### 3. 동작 확인

```bash
# 프롬프트 회귀 테스트 (74개)
pytest tests/test_trading_agents_prompt_rules.py -v
pytest prism-us/tests/test_trading_agents_prompt_rules.py -v

# KR 전체 파이프라인 (텔레그램 없이) — 새 JSON 스키마 검증
python stock_analysis_orchestrator.py --mode morning --no-telegram

# US 전체 파이프라인
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# 단일 종목 분석 (스모크 테스트)
python demo.py 005930
python demo.py AAPL --market us
```

분석 결과 JSON에서 다음 신규 필드가 채워지는지 확인:
- `fundamental_check.{F1_profitability, F2_balance_sheet, F3_growth, F4_business_clarity, all_passed}`
- `macro_adjustment` (-1, 0, +1)
- `effective_score`
- `momentum_signal_count`, `additional_confirmation_count`

---

## 알려진 제한사항 / 머지 후 모니터링 권장 사항

1. **buy_score 분포 변화 모니터링**: 펀더 게이트 도입 + min_score 매트릭스 적극화로 진입/미진입 비율이 변할 수 있습니다. 첫 며칠 동안 다음을 추적하세요:
   - regime별 진입 비율
   - rejection_reason 상위 5개 (어느 매트릭스 항목에서 미달이 많은지)
   - effective_score vs buy_score 차이 분포 (거시 보정 영향 확인)

2. **다운스트림 파서 영향**: 신규 JSON 키는 모두 additive하지만, 일부 기존 서비스가 strict schema 검증을 한다면 영향이 있을 수 있습니다. 머지 후 1회 dry-run을 권장합니다.

3. **KO/EN 미러링 일관성**: 같은 종목을 KO/EN 두 언어로 호출했을 때 matrix 결정이 일치하는지 확인. 미러링이 1:1이지만 LLM 출력 차이는 별개입니다.

4. **펀더 게이트 미달 + strong_bull 진입 케이스**: 프롬프트가 "명확한 보완 근거 + rejection_reason null"을 요구하지만, LLM이 이 룰을 일관되게 지키는지 후행평가 필요.

---

## 텔레그램 구독자 공지 메시지

### 한국어 — v2.11.0 통합 공지

```
🚀 PRISM-INSIGHT v2.11.0

PRISM의 매수 결정 프롬프트를 정리했습니다.

그동안 누적된 모순과 도피처 — "애매하면 일단 미진입", "참고용 손익비",
"가치투자 + 모멘텀" 같은 양립 불가 표현들을 걷어내고,
시장 국면(강한 강세장 ~ 강한 약세장)별 진입 기준을
하나의 매트릭스로 통일했습니다.

기준은 윌리엄 오닐의 CAN SLIM 철학으로 일관성을 잡았습니다.

━━━━━━━━━━━━━━━━━━━━━━━

🧱 1단계: 펀더멘털 게이트 (F1~F4)

진입 전 반드시 4가지 정량 체크를 통과해야 합니다:
  F1 수익성 — 최근 2분기 영업이익 흑자
  F2 재무 건전성 — 부채비율 < 200%
  F3 성장성 — ROE ≥ 5% 또는 매출 성장 ≥ 10%
  F4 사업 명확성 — 사업 모델 + 경쟁우위 식별 가능

→ "탄탄한 기업"의 정량 정의. 이걸 통과한 종목만 다음 단계로.

━━━━━━━━━━━━━━━━━━━━━━━

🎯 2단계: 시장 체제 5단계 매트릭스

펀더 게이트를 통과한 종목은, 시장 체제에 따라 적극성이 자동 조절됩니다:

  강한 강세장   → 4점부터 진입, 손절 -7%
  보통 강세장   → 4점부터 진입, 손절 -7%
  횡보장        → 5점 + 모멘텀 1개+, 손절 -6%
  보통 약세장   → 5점 + 모멘텀 2개+ + 추가 확인 1개
  강한 약세장   → 6점 + 모멘텀 2개+ + 추가 확인 1개

KR/US 동일 매트릭스. 두 시장이 일관되게 동작합니다.

이전보다 1단계씩 적극화됐습니다 — 펀더 게이트가 종목 품질을
보장해주므로, "탄탄한 종목 + 강세장이면 더 적극적으로 산다"는
원래 의도가 그대로 작동합니다.

━━━━━━━━━━━━━━━━━━━━━━━

📌 미진입 사유 정형화

"과열 우려", "변곡 신호", "추가 확인 필요" 같은 막연한 표현은
단독 미진입 사유로 사용 금지입니다.

미진입은 5가지 단독 사유 + 1가지 복합 사유 중 어느 것에 해당하는지
(또는 매트릭스의 어느 항목이 미달했는지) 명시해야 합니다.

━━━━━━━━━━━━━━━━━━━━━━━

🤖 매매 에이전트 두뇌 업그레이드

  매매 결정에 쓰이는 LLM 모델을 gpt-5.4 → gpt-5.5로 상향 조정했습니다.
  새 매트릭스의 정량 평가를 더 일관되게 수행할 수 있는 추론 능력 확보가 목적.

━━━━━━━━━━━━━━━━━━━━━━━

🔧 부가 개선

  • Firecrawl 명령어(/signal, /theme, /ask) 응답에서 투자 경고 문구
    중복 표시 문제 해결
  • /us_evaluate 티커 자동 대문자 변환 (aapl → AAPL)
  • /cancel 명령어를 모든 대화형 흐름에서 일관되게 사용 가능

━━━━━━━━━━━━━━━━━━━━━━━

📊 변경 통계

  • 5개 PR (#264, #265, #267, #268 + 모델 업그레이드)
  • 매수 프롬프트 -22% 압축 (2,294 → 1,794 줄)
  • 회귀 테스트 8개 → 74개
  • 매매 에이전트 모델 gpt-5.4 → gpt-5.5

업데이트: git pull origin main
```

---

### English

```
🚀 PRISM-INSIGHT v2.11.0 Update

We cleaned up PRISM's buy-decision prompt.

Years of accumulated contradictions and escape hatches —
"if unclear → No Entry", "R/R as reference", "value investing + momentum"
identity drift — have been removed, and entry criteria across the
5 market regimes (strong_bull → strong_bear) are now unified
into a single matrix.

Reasoning is anchored on a single axis: William O'Neil's CAN SLIM.

🧱 Stage 1: Fundamental Gate (F1~F4) — New
  F1 Profitability — Op profit positive in last 2 quarters
  F2 Balance sheet — Debt ratio < 200% OR ≤ industry avg
  F3 Growth — ROE ≥ 5% OR 2-yr revenue growth ≥ 10%
  F4 Business clarity — Business model + edge identifiable
  → A quantitative definition of "fundamentally sound."

🎯 Stage 2: Market-Regime Entry Matrix (5 levels) — Unified
  strong_bull   → min_score 4, R/R 1.0+, stop -7%
  moderate_bull → min_score 4, R/R 1.2+, stop -7%
  sideways      → min_score 5, R/R 1.3+, stop -6%, momentum 1+
  moderate_bear → min_score 5, R/R 1.5+, stop -5%, momentum 2+ confirmation 1
  strong_bear   → min_score 6, R/R 1.8+, stop -5%, momentum 2+ confirmation 1

Identical for KR and US — both markets behave consistently.
Slightly more aggressive than before, because the Fundamental Gate
already pre-filters for quality.

📌 Rejection reasons formalized
  Vague expressions like "overheating concern", "needs more confirmation",
  "wait and see" are no longer accepted as standalone rejection reasons.
  Rejections must cite a specific matrix item or one of the
  5 standalone / 1 compound reasons.

🤖 Trading agent brain upgrade
  LLM model used for buy/sell decisions upgraded gpt-5.4 → gpt-5.5
  for more consistent execution of the new quantitative matrix.

🔧 Other improvements
  • Firecrawl command responses no longer show duplicate disclaimers
  • /us_evaluate auto-uppercases tickers (aapl → AAPL)
  • /cancel works consistently across all interactive flows

📊 Stats
  • 5 PRs (#264, #265, #267, #268 + model upgrade)
  • Buy-decision prompt: -22% (2,294 → 1,794 lines)
  • Regression tests: 8 → 74
  • Trading agent model: gpt-5.4 → gpt-5.5

Update: git pull origin main
```
