# PRISM-INSIGHT v2.5.0

발표일: 2026년 2월 25일

## 개요

PRISM-INSIGHT v2.5.0은 **PRISM-Mobile 연동(Firebase Bridge)**, **다국어 Broadcast 시스템**, **Telegram 봇 안정성 개선**, **US 트레이딩 신호 정합성**, **AI 모델 업그레이드(Claude Sonnet 4.6)** 를 포함한 마이너 버전입니다.

특히 이번 버전부터 텔레그램 분석 메시지가 **영어·일본어·중국어·스페인어**로도 동시 발송되며, `/report` 명령 실행 중 서버 오류 발생 시 일일 사용 횟수가 **자동 환급**됩니다. 매수 분석 시 동일 종목의 이전 매도 이유와 시장 상황을 함께 참조하여 **성급한 재매수 오판을 방지**하는 기능도 추가되었습니다.

**주요 수치:**
- 총 28개 PR (#162 ~ #191)
- 38개 파일 변경
- +2,992 / -1,007 라인

---

## 주요 변경사항

### 1. Firebase Bridge — PRISM-Mobile 앱 연동 ⭐ NEW

PRISM-Mobile 앱에 주식 분석·트리거·매매 알림을 **FCM 푸시 알림**으로 전송하는 브릿지 모듈입니다. 텔레그램 채널과 동시에 동작하며, 기존 텔레그램 발송 흐름에 영향을 주지 않습니다.

#### 1.1 주요 기능

| 기능 | 설명 |
|------|------|
| **자동 타입 감지** | 시장(kr/us), 메시지 타입(trigger/analysis/portfolio/pdf) 자동 분류 |
| **Firestore 저장** | 메시지 메타데이터(제목, 미리보기, 종목 정보) Firestore에 기록 |
| **FCM 푸시 알림** | 등록된 디바이스에 실시간 푸시 전송 |
| **lang 필드 지원** | 다국어 채널 라우팅을 위한 언어 코드 포함 |
| **만료 토큰 자동 정리** | FCM 전송 실패(`registration-token-not-registered`) 시 Firestore에서 해당 토큰 자동 삭제 |
| **비동기 처리** | fire-and-forget 방식 — 트래킹 파이프라인 지연 없음 |

#### 1.2 연동 포인트

| 파일 | 연동 위치 |
|------|-----------|
| `telegram_bot_agent.py` | send_message, plain text retry, send_document (3곳) |
| `tracking/telegram.py` | _send_single_message (1곳) |
| `stock_tracking_agent.py` | KR 트래킹 에이전트 |
| `stock_tracking_enhanced_agent.py` | KR 강화 트래킹 에이전트 |
| `prism-us/us_stock_tracking_agent.py` | US 트래킹 에이전트 |

#### 1.3 설정 방법

```bash
# .env에 추가
FIREBASE_BRIDGE_ENABLED=true
GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-service-account.json
TELEGRAM_CHANNEL_USERNAME=your_telegram_channel_username

# 의존성 설치
pip install firebase-admin>=6.0.0
```

> 기본값은 **비활성화**입니다. `FIREBASE_BRIDGE_ENABLED=true` 설정 시에만 동작합니다.
> 모든 Firebase 호출은 try/except로 보호되어 있어 오류가 발생해도 텔레그램 발송에 영향을 주지 않습니다.

#### 1.4 버그 수정 이력

| PR | 수정 내용 |
|----|-----------|
| #162, #166 | Firebase Bridge 초기 구현 + `lang` 필드 추가 |
| #171, #172 | KR/US 트래킹 에이전트 연동 + 비동기 처리 전환 |
| #186 | `msg_type` 인자 누락으로 타입 감지 오동작 — 명시적 전달로 수정 |
| #188 | `detect_market()` 날짜 문자열(`20260224`) 내 6자리가 한국 종목코드로 오감지되던 버그 수정 |
| #188 | FCM `registration-token-not-registered` 만료 토큰 자동 정리 |
| #195 | FCM `lang` 필터 버그 수정 — `lang` 미설정 디바이스 알림 누락 해결 |
| #196 | FCM `NOT_FOUND` 에러코드 처리 추가 — 만료 토큰 0/8 실패 반복 현상 완전 해결 |

---

### 2. 다국어 Broadcast 시스템 ⭐ NEW

분석 리포트를 **영어·일본어·중국어·스페인어**로 번역하여 언어별 채널에 동시 발송합니다. 메인 분석 파이프라인을 블로킹하지 않는 비동기 구조입니다.

#### 2.1 사용 방법

```bash
# 주간 리포트 다국어 발송
python weekly_insight_report.py --broadcast-languages en,ja,zh,es

# 특정 언어만
python weekly_insight_report.py --broadcast-languages en,ja
```

#### 2.2 처리 방식

- **번역**: fire-and-forget 방식으로 메인 파이프라인과 병렬 실행
- **PDF 생성**: 파일 충돌 방지를 위해 순차(sequential) 처리
- **언어별 채널**: `.env`의 채널 링크 설정 기반으로 라우팅

#### 2.3 다국어 README 추가

| 파일 | 언어 |
|------|------|
| `README_ja.md` | 일본어 |
| `README_zh.md` | 중국어 |
| `README_es.md` | 스페인어 |

#### 2.4 버그 수정

| 항목 | 수정 |
|------|------|
| 브로드캐스트 PDF 병렬 처리 충돌 | 순차 처리로 전환 |
| 포트폴리오 리포터 번역 awaiting 누락 | `portfolio_telegram_reporter.py` 수정 |
| 트래킹 broadcast 태스크 await 누락 | 컨텍스트 종료 전 완료 보장 |
| ja/zh/es 채널 링크 오탈자 | 수정 |

---

### 3. 매수 분석 시 동일 종목 매도 컨텍스트 강화 ⭐ NEW

동일 종목을 이전에 매도했던 경우, 재매수 판단 시 **매도 이유·당시 시장 상황·놓친 신호**를 AI에 함께 전달합니다.

#### 3.1 문제 배경

기존에는 수익률과 한줄 요약만 전달되어, LLM이 "+25% ✅"만 보고 성급하게 재매수를 결정하는 오판이 발생했습니다.

```
Before: 수익률: +25.3% | 요약: 목표가 달성으로 청산
After:  수익률: +25.3%
        매도 이유: 실적 발표 후 피크아웃 우려
        당시 상황: 섹터 전반 조정 + 금리 상승 우려
        판단 평가: 적정 청산, 다만 이후 10% 추가 상승 — 목표가 설정 재검토 필요
```

#### 3.2 효과

- 동일 종목 즉시 재진입 여부를 더 정확히 판단
- 과거 매도 실수 패턴을 AI가 학습에 반영

---

### 4. 주간 리포트 강화 ⭐ NEW

주간 인사이트 리포트에 **4개 섹션**이 추가되어 단순 성과 요약을 넘어 학습 기반 분석을 제공합니다.

| 추가 섹션 | 내용 |
|----------|------|
| **주간 매매 요약** | 진입/청산 내역, 수익률 집계 |
| **매도 후 평가** | 매도 시점 전후 비교 분석 |
| **AI 장기 학습 인사이트** | 주간 성과 기반 패턴 추출 |
| **L1→L2 압축 후행 교훈** | 압축 완료 후 학습 포인트 자동 생성 |

```bash
# dry-run으로 먼저 확인
python weekly_insight_report.py --dry-run

# 다국어 브로드캐스트 포함 발송
python weekly_insight_report.py --broadcast-languages en,ja
```

---

### 5. AI 모델 업그레이드 — Claude Sonnet 4.6

| 항목 | Before | After |
|------|--------|-------|
| **모델** | `claude-sonnet-4-5-20250929` | `claude-sonnet-4-6` |
| **Knowledge Cutoff** | 2025년 1월 | **2025년 8월** |
| **evaluate maxTokens** | 3,000 | **8,000** (긴 분석 응답 잘림 방지) |

**적용 파일:** `report_generator.py` (5곳)

---

### 6. Telegram /report 일일 사용 환급

`/report` 또는 `/us_report` 실행 중 서버 내부 오류(서브프로세스 타임아웃, AI 에이전트 오류 등) 가 발생하면, 소모된 일일 사용 횟수를 **자동으로 환급**합니다. 사용자 입력 오류(잘못된 종목 코드 등)는 환급 대상에서 제외됩니다.

```
사용자가 직접 유발한 오류 → 환급 없음
서버 내부 오류 (타임아웃, AI 에이전트 오류) → 자동 환급 후 재시도 가능
```

#### 6.1 한국어 메시지 복원

이전 리팩토링으로 영어로 변경되었던 Telegram 봇 사용자 대면 메시지를 **한국어 템플릿으로 복원**했습니다.

---

### 7. JSON 파싱 유틸 리팩토링 🌟 첫 기여

> **이 PR은 외부 기여자분의 첫 번째 기여입니다. 소중한 참여에 진심으로 감사드립니다! 🎉**

코드베이스 전반에 흩어진 중복 JSON 파싱 로직을 `cores/utils.py`로 통합했습니다. 사용자 관점에서는 에이전트 응답 파싱 실패 시 **원본 응답이 로그에 기록**되어 디버깅이 개선됩니다.

```python
# Before: 에이전트마다 중복 구현 (6곳)
try:
    result = json.loads(response.strip("```json\n```"))
except json.JSONDecodeError:
    ...

# After: 단일 유틸 함수 (cores/utils.py)
from cores.utils import parse_json_response
result = parse_json_response(response)
```

**적용 파일:** `prism-us/cores/agents/` 하위 5개 파일 + `prism-us/cores/us_analysis.py`

---

### 8. US 트레이딩 신호 정합성 개선

US 트레이딩 에이전트가 실제로 **접근 불가능한 데이터**를 참조하여 분석 품질이 저하되던 문제를 수정했습니다.

#### 8.1 수정 내용

| 에이전트 | Before | After |
|---------|--------|-------|
| **매수 에이전트** | Form 4 내부자 거래, 애널리스트 목표주가 (직접 참조 불가) | `get_recommendations()` 기반 접근 가능 데이터만 사용 |
| **매도 에이전트** | 기관 포지션, 내부자 거래 등 접근 불가 신호 | yahoo_finance로 접근 가능한 모멘텀·기술적 지표·밸류에이션만 사용 |
| **언어 기본값** | `"en"` (US 모듈 전체) | `"ko"` (KR 채널 기본 정책 일관성 유지) |

---

### 9. US 매수 가격 조회 + GCP 인증 버그 수정

| # | 문제 | 수정 |
|---|------|------|
| 1 | KIS `last` 필드 빈 문자열 시 가격 조회 실패 | `base`(전일종가) fallback 추가 |
| 2 | `async_buy_stock()` 가격 조회 실패 시 예약주문 불가 | `limit_price` fallback으로 예약주문 보장 |
| 3 | GCP Pub/Sub 401 인증 오류 | `service_account.Credentials` 명시적 인증으로 전환 |

```python
# Before: 빈 문자열에서 float 변환 실패
price = float(data['last'])  # '' → ValueError

# After: fallback chain
price = _safe_float(data.get('last')) or _safe_float(data.get('base'))
```

---

### 10. Telegram Evaluator 다중 JSON 파싱 수정

`gpt-5.x` reasoning 모델이 `EvaluationResult` JSON 응답 앞에 빈 `{}` thinking 토큰을 출력하여 `EvaluatorOptimizerLLM`의 evaluator 단계에서 `ValidationError: trailing characters`가 발생, 보고서 전체 처리가 중단되던 문제를 수정했습니다. (#197)

#### 10.1 수정 내용

| 추가 항목 | 설명 |
|----------|------|
| `_extract_last_valid_json(text)` | 중괄호 깊이 추적으로 텍스트에서 마지막 완전한 JSON 객체만 추출 |
| `_RobustEvaluatorLLM` | `evaluator_llm.generate_structured()` 래퍼 — 파싱 실패 시 `generate_str()` fallback 후 JSON 재추출 |

**적용 파일:** `telegram_summary_agent.py`

---

### 11. US 분석 버그 5종 수정

| # | 문제 | 수정 |
|---|------|------|
| 1 | `data_prefetch._df_to_markdown` tabulate 의존성 오류 | 직접 마크다운 테이블 생성으로 대체 |
| 2 | `us_telegram_summary_agent` evaluator Pydantic 오류 | JSON 스키마 명세 추가, 평가 등급 0–3으로 정정 |
| 3 | US holding 매도 판단 규칙 기반 고정 | `create_us_sell_decision_agent` AI 에이전트 연결 (fallback 유지) |
| 4 | Redis 신호 로그 KRW 하드코딩 | `market` 필드 기반 USD/KRW 동적 출력 |
| 5 | GCP Pub/Sub credentials 경로 로그 누락 | `GCP_CREDENTIALS_PATH` 미설정 시 경고 추가 |

---

### 11. prism-us cores.utils 네임스페이스 충돌 수정

`prism-us/cores/` 디렉토리가 메인 프로젝트의 `cores/`를 `sys.path`에서 가려 `ModuleNotFoundError`가 발생하던 문제를 수정했습니다.

```python
# Before: 네임스페이스 충돌로 ModuleNotFoundError
from cores.utils import parse_llm_json

# After: _import_from_main_cores 헬퍼 사용
parse_llm_json = _import_from_main_cores('utils', 'parse_llm_json')
```

**수정 파일:** `prism-us/us_stock_tracking_agent.py`, `prism-us/tracking/journal.py`

---

## 변경된 파일

| 파일 | 주요 PR | 변경 내용 |
|------|---------|-----------|
| `telegram_ai_bot.py` | #181 | 환급 로직, 한국어 메시지 복원 |
| `telegram_bot_agent.py` | #186 | msg_type 명시적 전달 |
| `firebase_bridge.py` | #162, #166, #188 | Firebase Bridge 구현, FCM 토큰 정리, detect_market 수정 |
| `weekly_insight_report.py` | #176 | 주간 리포트 섹션 4개 추가 |
| `report_generator.py` | #183, #184 | Claude Sonnet 4.6 업그레이드, maxTokens 8,000 |
| `cores/utils.py` | #179 🌟 | `parse_json_response()` 신규 추가 — **첫 기여** |
| `stock_analysis_orchestrator.py` | #163-#167, #186 | 브로드캐스트 병렬화, msg_type 명시 |
| `prism-us/us_stock_analysis_orchestrator.py` | #163-#167, #173, #186 | 브로드캐스트 병렬화, 언어 기본값 ko, msg_type 명시 |
| `stock_tracking_agent.py` | #171, #186 | Firebase Bridge 연동, msg_type 명시 |
| `stock_tracking_enhanced_agent.py` | #171 | Firebase Bridge 연동 |
| `prism-us/us_stock_tracking_agent.py` | #171, #173, #175, #180, #186, #189 | Firebase 연동, 신호 정합성, AI 매도, msg_type 명시, 네임스페이스 수정 |
| `prism-us/cores/agents/trading_agents.py` | #174, #175 | 신호 소스 교체 |
| `prism-us/us_telegram_summary_agent.py` | #180 | evaluator 프롬프트 수정 |
| `prism-us/cores/data_prefetch.py` | #180 | tabulate 제거, 마크다운 직접 생성 |
| `prism-us/trading/us_stock_trading.py` | #177 | 가격 fallback |
| `messaging/gcp_pubsub_signal_publisher.py` | #177, #180 | GCP 인증, 로깅 개선 |
| `messaging/redis_signal_publisher.py` | #180 | 통화 동적 출력 |
| `tracking/compression.py` | #176 | 후행 교훈 추출 로직 |
| `prism-us/tracking/compression.py` | #176 | 동일 (US 미러) |
| `tracking/journal.py` | #176, #191 | 주간 집계 쿼리, 매도 컨텍스트 강화 |
| `prism-us/tracking/journal.py` | #176, #190 | 주간 집계 쿼리, 네임스페이스 수정 |
| `trading/portfolio_telegram_reporter.py` | #170 | broadcast await 수정 |
| `tracking/telegram.py` | #186 | msg_type 명시적 전달 |
| `demo.py` | #181 | user_id 필드 추가 |
| `README_ja.md`, `README_zh.md`, `README_es.md` | #168, #169 | 신규 생성 |
| `prism-us/cores/agents/*.py` (5개) | #179 🌟 | JSON 파싱 유틸 적용 — **첫 기여** |
| `prism-us/cores/us_analysis.py` | #179 🌟 | JSON 파싱 유틸 적용 — **첫 기여** |

---

## 업데이트 방법

### 1. 코드 업데이트

```bash
git pull origin main
```

### 2. 의존성 설치

```bash
# Firebase Bridge 사용 시 (선택)
pip install firebase-admin>=6.0.0
```

> Firebase Bridge를 사용하지 않는 경우 추가 설치가 필요 없습니다.

### 3. Firebase Bridge 설정 (선택)

```bash
# .env에 추가
FIREBASE_BRIDGE_ENABLED=true
GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-service-account.json
TELEGRAM_CHANNEL_USERNAME=your_telegram_channel_username
```

### 4. GCP Pub/Sub 인증 확인 (GCP 사용 시)

GCP Pub/Sub 401 오류가 발생한 적 있다면 서비스 계정 JSON 경로를 확인하세요.

```bash
# .env 또는 환경변수
GCP_CREDENTIALS_PATH=/path/to/gcp-service-account.json
```

### 5. Claude Sonnet 4.6 접근 권한 확인

`mcp_agent.secrets.yaml`의 Anthropic API 키가 `claude-sonnet-4-6` 모델에 접근 가능한지 확인하세요.

---

## 테스트

```bash
# KR 단일 종목 분석 (prefetch + Claude 4.6 동작 확인)
python demo.py 005930

# US 단일 종목 분석
python demo.py AAPL --market us

# 주간 리포트 dry-run
python weekly_insight_report.py --dry-run

# 다국어 브로드캐스트 dry-run
python weekly_insight_report.py --broadcast-languages en,ja --dry-run
```

---

## 알려진 제한사항

1. **Firebase Bridge end-to-end 테스트**: 실제 Firebase 프로젝트 없이는 푸시 알림 수신 확인 불가. 로컬에서는 bridge 코드 경로만 검증됩니다.
2. **Claude Sonnet 4.6 API 접근**: 일부 구 API 키에서 모델 접근 오류가 발생할 수 있습니다. Anthropic 콘솔에서 접근 권한을 확인하세요.
3. **다국어 번역 비용**: `--broadcast-languages` 플래그 사용 시 언어당 1회 추가 API 호출이 발생합니다.

---

## 텔레그램 구독자 공지 메시지

### 한국어

```
🚀 PRISM-INSIGHT v2.5.0 업데이트

안녕하세요, 구독자 여러분.
오늘 v2.5.0이 배포되었습니다. 주요 변경사항을 안내드립니다.

✅ 주요 업데이트

📱 PRISM-Mobile 앱(베타테스트 중) 푸시 알림 연동
텔레그램 알림과 동시에 앱 푸시 알림을 받을 수 있습니다.
만료된 디바이스 토큰도 자동으로 정리됩니다.

🌏 다국어 리포트 발송
분석 리포트가 영어·일본어·중국어·스페인어로도 동시 발송됩니다.
언어별 채널 구독으로 원하는 언어로 받아보세요.
(영어 : @prism_insight_global_en, 중국어 : @prism_insight_zh, 일본어 : @prism_insight_ja, 스페인어 : @prism_insight_es)

📊 재매수 판단 고도화
이전에 매도한 종목을 다시 매수할 때, AI가 당시 매도 이유와
시장 상황까지 함께 참고하여 성급한 재진입을 방지합니다.

🇺🇸 US 트레이딩 신호 개선
실제로 접근 불가능한 데이터를 참조하던 US 매수·매도 에이전트 신호를
정확한 데이터 소스 기반으로 교체했습니다.

🔄 (토론방 봇) /report 오류 시 자동 환급
서버 내부 오류 발생 시 일일 사용 횟수가 자동 환급됩니다.
오류가 나도 당일 재시도가 가능합니다.

🧠 (토론방 봇) AI 모델 업그레이드 (Claude Sonnet 4.6)
분석에 사용되는 AI 모델이 업그레이드되었습니다.
Knowledge cutoff가 2025년 8월로 늘어나 더 최신 정보를 반영합니다.

감사합니다
좋은 밤 되세요~
```

---

### English

```
🚀 PRISM-INSIGHT v2.5.0 is now live!

Hello, subscribers.
We've just released v2.5.0. Here's a summary of the key updates.

✅ Key Updates

📱 PRISM-Mobile App (beta) Push Notification Support
Receive push alerts on the app at the same time as your Telegram notifications.
Expired device tokens are now cleaned up automatically.

🌏 Multilingual Report Broadcasting
Analysis reports are now delivered simultaneously in English, Japanese, Chinese, and Spanish.
Subscribe to your preferred language channel below.
(English: @prism_insight_global_en, Chinese: @prism_insight_zh, Japanese: @prism_insight_ja, Spanish: @prism_insight_es)

📊 Smarter Re-entry Decisions
When considering re-buying a stock you previously sold, the AI now references
the original sell reason and market context to prevent impulsive re-entries.

🇺🇸 US Trading Signal Accuracy Improved
US buy/sell agent signals have been updated to use only data that is
actually accessible — replacing previously referenced unavailable data sources.

🔄 (Discussion Bot) Auto-Refund for /report Errors
If a server-side error occurs, your daily usage count is automatically refunded.
You can retry the same day without losing your limit.

🧠 (Discussion Bot) AI Model Upgrade — Claude Sonnet 4.6
The AI model used for analysis has been upgraded to Claude Sonnet 4.6.
Knowledge cutoff extended to August 2025 for more up-to-date insights.

Thank you.
Have a good night~
```
