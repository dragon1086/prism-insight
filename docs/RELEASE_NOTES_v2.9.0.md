# PRISM-INSIGHT v2.9.0

발표일: 2026년 3월 31일

## 개요

PRISM-INSIGHT v2.9.0은 **외부 기여자 3인의 주요 기능 추가**와 **매매 안정성 버그 수정**에 초점을 맞춘 마이너 버전입니다.

다중 계좌 지원으로 주계좌·부계좌를 병렬 운용할 수 있게 되었고, US 소셜 센티먼트 분석이 추가되었습니다. 또한 KIS API 주문 오류 3종, Telegram 타임아웃, US 모듈 임포트 충돌 등 운영 중 발견된 버그를 수정했습니다.

**외부 기여자:** tkgo11, alexander-schneider, lifrary

**주요 수치:**
- 총 12개 PR (#227 ~ #239, #230 reverted)
- 약 44개 파일 변경, +6,327 / -1,135 lines

---

## 주요 변경사항

### 1. 다중 계좌 지원 (PR #228 — tkgo11) ⭐ NEW

하나의 PRISM 인스턴스로 KIS API **주계좌와 부계좌를 동시 운용**할 수 있습니다.

| 항목 | 설명 |
|------|------|
| **계좌 팬아웃** | 매수·매도 신호를 모든 등록된 계좌에 동시 전송 |
| **DB 마이그레이션** | `stock_holdings`, `trading_history` 등 테이블에 `account_id` 컬럼 추가 |
| **대표 계좌 텔레그램** | 텔레그램 보고는 대표 계좌 기준으로만 발송 |
| **세션 관리** | 계좌별 독립 KIS 세션 유지 |

```yaml
# kis_devlp.yaml — 다중 계좌 설정 예시
accounts:
  - id: primary
    app_key: ...
    app_secret: ...
    account_no: ...
  - id: secondary
    app_key: ...
    app_secret: ...
    account_no: ...
```

---

### 2. US 소셜 센티먼트 컨텍스트 (PR #229 — alexander-schneider) ⭐ NEW

Adanos API를 통해 **Reddit·Twitter 소셜 센티먼트 데이터**를 US 분석 파이프라인에 통합했습니다.

| 항목 | 설명 |
|------|------|
| **데이터 소스** | Adanos API (Reddit, Twitter 집계) |
| **활성화 조건** | `ADANOS_API_KEY` 환경변수 설정 시 자동 활성 |
| **비동기 프리패치** | 분석 시작 시 백그라운드로 센티먼트 데이터 로딩 |
| **에러 격리** | API 실패 시 분석 파이프라인에 영향 없음 (graceful degradation) |

```bash
# .env에 추가
ADANOS_API_KEY=your_api_key_here
```

---

### 3. US 모듈 네임스페이스 충돌 수정 (PR #227 — lifrary)

`prism-us/` 하위 모듈이 `cores/`를 임포트할 때 발생하던 **패키지 이름 충돌**을 해결했습니다.

| 문제 | 수정 |
|------|------|
| `prism-us/cores/`와 루트 `cores/`가 같은 패키지명으로 충돌 | `_import_from_us_cores()` 헬퍼 → `importlib.util` 기반으로 대체 |
| US 오케스트레이터 `sys.path` 삽입 순서 문제 | `PROJECT_ROOT` 우선 탐색 보장 |

---

### 4. KIS API 주문 버그 3종 수정 (PR #239)

실거래 환경에서 발견된 KIS API 주문 오류를 수정했습니다.

| 오류 코드 | 원인 | 수정 |
|-----------|------|------|
| **APTR0057** | 가격 소수점 초과 (예: `71.175`) | `round(price, 2)` 강제 적용 |
| **APBK1234** | 시장가 주문 타입 불일치 | 주문 유형 파라미터 정정 |
| **Telegram JSON sanitize** | 텔레그램 메시지 내 특수문자 미이스케이프 | JSON 직렬화 전 sanitize 추가 |
| **손절 방어 강화** | AI 에이전트의 손절선 완화 시도 | 3레이어 손절 방어 로직 추가 |

---

### 5. US 매도 ORD_DVSN 누락 수정 (PR #238)

US 시장가 매도 주문 시 `ORD_DVSN` 파라미터 누락으로 발생하던 **IGW00019 에러**를 수정했습니다.

```python
# Before — ORD_DVSN 누락으로 IGW00019 발생
params = {"PDNO": ticker, "ORD_QTY": str(qty)}

# After
params = {"PDNO": ticker, "ORD_QTY": str(qty), "ORD_DVSN": "00"}
```

---

### 6. Telegram 타임아웃 재시도 (PR #237)

네트워크 불안정으로 발생하던 Telegram 메시지 전송 실패를 **지수 백오프 재시도**로 해결했습니다.

| 항목 | 설명 |
|------|------|
| **재시도 횟수** | 최대 3회 |
| **백오프 전략** | 1s → 2s → 4s (지수 증가) |
| **대상 오류** | 타임아웃, Rate Limit (429) |
| **적용 위치** | `stock_tracking_agent._send_with_retry()` |

---

### 7. OpenAI 400 에러 디버그 로깅 (PR #232)

OpenAI API 400 에러 발생 시 **request body를 자동으로 로깅**하여 원인 진단을 돕습니다.

```python
# httpx 이벤트 훅으로 400 응답 시 요청 본문 출력
# cores/openai_debug.py
```

> PR #233~#236은 이 모듈의 US 오케스트레이터 임포트 체인을 안정화하는 후속 수정입니다.

---

## 변경된 파일

| 파일 | PR | 변경 내용 |
|------|----|-----------|
| `trading/domestic_stock_trading.py` | #228 | 다중 계좌 팬아웃, 세션 관리 |
| `trading/db_schema.py` | #228 | `account_id` 컬럼 마이그레이션 |
| `stock_tracking_agent.py` | #228, #237, #239 | 다중 계좌 + Telegram 재시도 + 손절 방어 |
| `prism-us/trading/us_stock_trading.py` | #238, #239 | ORD_DVSN 추가, 가격 반올림 |
| `prism-us/us_stock_tracking_agent.py` | #227, #228 | 네임스페이스 수정 + 다중 계좌 |
| `prism-us/us_stock_analysis_orchestrator.py` | #227, #229, #232~#236 | 임포트 체인 수정 + 소셜 센티먼트 |
| `prism-us/cores/social_sentiment_agent.py` | #229 | 신규: Adanos API 센티먼트 에이전트 |
| `prism-us/cores/data_prefetch.py` | #229 | 소셜 센티먼트 비동기 프리패치 |
| `cores/openai_debug.py` | #232 | 신규: OpenAI 400 디버그 로거 |
| `telegram_bot_agent.py` | #239 | JSON sanitize |

---

## 업데이트 방법

### 1. 코드 업데이트

```bash
git pull origin main
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. DB 마이그레이션 (다중 계좌 사용 시)

PR #228 DB 마이그레이션은 첫 실행 시 자동 적용됩니다.

### 4. 동작 확인

```bash
# KR 전체 파이프라인 (텔레그램 없이)
python stock_analysis_orchestrator.py --mode morning --no-telegram

# US 전체 파이프라인 (텔레그램 없이)
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

---

## 알려진 제한사항

1. **소셜 센티먼트**: `ADANOS_API_KEY` 미설정 시 자동으로 비활성화됩니다.
2. **다중 계좌 DB 마이그레이션**: 기존 `stock_holdings` 테이블 구조가 변경됩니다. 운영 서버 업데이트 전 DB 백업을 권장합니다.

---

## 텔레그램 구독자 공지 메시지

### 한국어

```
🚀 PRISM-INSIGHT v2.9.0 업데이트

이번 버전에서는 외부 기여자 3인의 신규 기능과 매매 안정성 개선이 포함됩니다.

✨ 신규 기능
💼 다중 계좌 지원 (외부 기여: tkgo11)
  • 주계좌·부계좌 동시 운용
  • 매수/매도 신호 모든 계좌 동시 전송

📊 US 소셜 센티먼트 분석 추가 (외부 기여: alexander-schneider)
  • Reddit·Twitter 센티먼트 데이터 통합
  • ADANOS_API_KEY 설정 시 자동 활성화

🔩 US 모듈 임포트 충돌 수정 (외부 기여: lifrary)

🛠 버그 수정
• KIS API 주문 오류 3종 수정 (APTR0057, APBK1234)
• US 매도 ORD_DVSN 누락 수정 (IGW00019)
• Telegram 타임아웃 재시도 로직 추가
• 손절 방어 강화

git pull origin main 으로 업데이트하세요.
```

### English

```
🚀 PRISM-INSIGHT v2.9.0 Update

This release includes new features from 3 external contributors and trading stability improvements.

✨ New Features
💼 Multi-Account Support (contributed by tkgo11)
  • Run primary and secondary KIS accounts in parallel
  • Trade signals fan out to all registered accounts

📊 US Social Sentiment Context (contributed by alexander-schneider)
  • Reddit & Twitter sentiment data integrated into US analysis
  • Automatically enabled when ADANOS_API_KEY is set

🔩 US Module Import Fix (contributed by lifrary)

🛠 Bug Fixes
• KIS API order errors fixed (APTR0057, APBK1234)
• US sell order ORD_DVSN missing param fixed (IGW00019)
• Telegram timeout retry with exponential backoff
• Stop-loss defense hardened against AI override attempts

Update with: git pull origin main
```
