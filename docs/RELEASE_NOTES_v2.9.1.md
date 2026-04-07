# PRISM-INSIGHT v2.9.1

발표일: 2026년 4월 7일

## 개요

PRISM-INSIGHT v2.10.0은 **KIS 인증 안정성 전면 개선**과 **데이터 기반 매매 전략 정제**에 초점을 맞춘 마이너 버전입니다.

다중 계좌 운용 환경에서 빈번하게 발생하던 IGW00002 인증 오류를 계좌별 독립 App Key + 토큰 파일 격리로 근본 해결했습니다. 또한 92건의 KR + 15건의 US 실거래 데이터 분석을 바탕으로 약세장·횡보장 매도 규칙을 데이터 기반으로 개선했습니다. OpenAI 오류 추적 인프라도 대폭 강화되었습니다.

**주요 수치:**
- 총 10개 PR (#241 ~ #252)
- 21개 커밋, 36개 파일 변경, +1,122 / -314 lines

---

## 주요 변경사항

### 1. KIS 계좌별 App Key + 토큰 파일 격리 (PR #246) ⭐ 주요 수정

다중 계좌 환경에서 반복되던 **IGW00002 (계좌번호 불일치)** 오류를 근본적으로 해결했습니다.

| 항목 | 설명 |
|------|------|
| **계좌별 App Key** | `kis_devlp.yaml` 각 계좌에 `app_key`/`app_secret` 개별 지정 가능 |
| **토큰 파일 격리** | `KIS_acct_{hash8}.token` 형태로 계좌별 독립 토큰 저장 |
| **동적 헤더 조회** | `_getBaseHeader()`가 `_TRENV`에서 실시간으로 appkey/appsecret 읽음 (전역 캐시 제거) |
| **글로벌 fallback** | 계좌별 키 미설정 시 `my_app`/`my_sec` 전역값으로 자동 fallback |

```yaml
# trading/config/kis_devlp.yaml — 계좌별 App Key 설정 예시
accounts:
  - id: primary
    app_key: YOUR_PRIMARY_APP_KEY    # 계좌별 등록 (KIS API는 1 app_key = 1 계좌)
    app_secret: YOUR_PRIMARY_SECRET
    account_no: XXXXXXXX-XX
  - id: secondary
    app_key: YOUR_SECONDARY_APP_KEY
    app_secret: YOUR_SECONDARY_SECRET
    account_no: YYYYYYYY-YY
```

> **주의**: KIS API는 1개의 App Key를 1개의 계좌에만 바인딩합니다. 계좌마다 별도의 App Key를 발급·등록해야 합니다.

---

### 2. US 거래 안정성 개선 (PR #245) ⭐ 주요 수정

다중 계좌 US 매매 환경에서 발생하던 3가지 버그를 수정했습니다.

| 버그 | 원인 | 수정 |
|------|------|------|
| **선물 계좌 US 팬아웃 포함** | legacy-future 계좌의 `market="all"` 설정이 US 주문 전송으로 연결 | `market="kr"`로 변경하여 US 팬아웃에서 제외 |
| **비숫자 계좌번호 등록** | 템플릿 placeholder("선물옵션계좌 8자리")가 빈 문자열 검사 통과 | `isdigit()` 검증 추가 |
| **유령 보유 종목 생성** | KIS 주문 실패 시에도 DB에 holding 레코드 잔존 | 주문 실패 시 DB insert 롤백 + `state["traded"]` 게이팅 |

---

### 3. US 추적 에이전트 KIS Auth 임포트 수정 (PR #243, #244)

`prism-us/us_stock_tracking_agent.py`에서 `from trading import kis_auth`가 루트의 `trading/kis_auth.py` 대신 빈 `prism-us/trading/__init__.py`로 해석되던 네임스페이스 충돌을 수정했습니다.

```python
# Before — 빈 prism-us/trading/__init__.py로 잘못 해석
from trading import kis_auth as ka

# After — importlib.util로 PROJECT_ROOT/trading/kis_auth.py 직접 로드
ka = importlib.util.spec_from_file_location(...)
```

---

### 4. 약세장·횡보장 매도 규칙 개선 (PR #242) ⭐ 전략 변경

**92건 KR + 15건 US 실거래 데이터 분석**을 기반으로 기존 규칙이 수익 포지션을 조기 청산하던 문제를 해결했습니다.

| 제거된 규칙 | 제거 근거 |
|-------------|-----------|
| **7일 관찰 한도** | KR 7일 경계 거래(005850 +19.9%, 009420 +18.7%)가 기계적으로 조기 청산됨 |
| **5% 수익 실현 규칙** | US 15-30일 보유 승률 100% (+5.81% 평균) vs 0-3일 보유 승률 20% (-3.34% 평균) |

> 트레일링 스톱이 추세 약화 시 자연스러운 청산을 보장하므로, 시간·수익률 기반 강제 청산은 불필요합니다. KR/US 에이전트 모두(ko/en 버전 포함) 적용됩니다.

---

### 5. 횡보장 매수 진입 프롬프트 정밀화 (PR #248)

횡보장(sideways) 국면에서 매수 진입 기준을 더 엄격하게 조정했습니다. KR/US 에이전트 모두 적용되며, 해당 규칙을 검증하는 테스트도 추가되었습니다.

| 파일 | 변경 내용 |
|------|-----------|
| `cores/agents/trading_agents.py` | 횡보장 진입 프롬프트 재작성 |
| `prism-us/cores/agents/trading_agents.py` | 동일 변경 (US) |
| `tests/test_trading_agents_prompt_rules.py` | 신규: 프롬프트 규칙 테스트 |
| `prism-us/tests/test_trading_agents_prompt_rules.py` | 신규: US 프롬프트 규칙 테스트 |

---

### 6. OpenAI 오류 추적 인프라 강화 (PR #251)

OpenAI 400 오류·할당량 오류 발생 시 **Request ID를 자동으로 로깅**하여 Anthropic 지원 요청 시 원인 추적을 돕습니다.

| 항목 | 설명 |
|------|------|
| **신규 모듈** | `cores/openai_error_logging.py` — 중앙화된 오류 로깅 헬퍼 |
| **US 미러** | `prism-us/cores/openai_error_logging.py` — US 모듈용 독립 임포트 |
| **적용 범위** | KR/US 오케스트레이터, 추적 에이전트, 텔레그램 요약 에이전트, 번역 에이전트 |
| **테스트 추가** | `tests/test_openai_error_logging.py` |

```python
# 400/할당량 오류 발생 시 자동 로깅
# [OpenAI 400] request-id: req_abc123... body: {...}
# [OpenAI Quota] request-id: req_def456...
```

---

### 7. 매매 에이전트 시간 컨텍스트 강제화 (PR #252)

LLM이 잘못된 날짜를 가정하여 OHLCV 데이터를 조회하는 문제를 방지합니다.

| 항목 | 변경 내용 |
|------|-----------|
| **시간 조회 강제화** | `create_trading_scenario_agent`, `create_sell_decision_agent` 모두 도구 사용 가이드에 `time-get_current_time` 선행 호출 지시 추가 |
| **세션 시간 레이블** | "장중(09:00~15:20)" → "오전장(09:30~10:30)", "장 마감 후(15:30 이후)" → "오후 장(14:50 이후)" |

---

### 8. 주간 리포트 트리거 성과 집계 오류 수정 (PR #250)

주간 인사이트 리포트에서 트리거 성과가 항상 0건으로 표시되던 문제를 수정했습니다.

| 버그 | 수정 |
|------|------|
| **트리거 성과 0건** | `updated_at`/`last_updated` 주 단위 날짜 필터 제거 → 누적 전체 통계로 변경 |
| **`was_traded=0` 필터 누락** | `was_traded=0` → `COALESCE(was_traded, 0)=0` (NULL 행 포함) |
| **AI 인사이트 표시 오류** | 평균 신뢰도 `0%` → `집계 중`, 신규 인사이트 `0개` → `없음` |

---

### 9. Docker 퀵스타트 로컬 빌드 수정

Docker Compose v2 문법 호환 및 로컬 빌드 안정성을 개선했습니다.

| 항목 | 변경 내용 |
|------|-----------|
| **Compose v2 문법** | `docker-compose up -d` → `docker compose up -d` (전체 README 5개 언어 반영) |
| **로컬 빌드 지원** | `docker/quickstart-entrypoint.sh` 신규 추가, `Dockerfile` + `docker-compose.quickstart.yml` 개선 |
| **`.dockerignore`** | 불필요한 파일 제외 규칙 추가 |
| **스페인어 README** | 누락된 스페인어 액센트 기호 수정 (README_es.md) |

---

### 10. US 장중 매도 주문 APBK1269 오류 수정 (PR #253)

실거래 환경에서 US 장중 매도 시 `APBK1269 - 주문구분 입력오류`가 발생하던 문제를 수정했습니다.

| 항목 | 내용 |
|------|------|
| **원인** | `TTTT1006U`(해외주식 매도)에 `ORD_DVSN "01"`(시장가) 사용 — 매도 API에는 존재하지 않는 코드 |
| **수정** | `ORD_DVSN "00"` (지정가) + `OVRS_ORD_UNPR` 현재가로 변경 |
| **동작** | 장중에는 현재가 지정가 주문으로 즉시 체결되어 시장가와 동일 효과 |

```
# KIS TTTT1006U 유효한 ORD_DVSN 값
"00" = 지정가 (limit)   ← 수정 후 사용
"31" = MOO (장개시시장가)
"32" = LOO (장개시지정가)
"33" = MOC (장마감시장가)
"34" = LOC (장마감지정가)
# "01" = 존재하지 않음 (매수 TTTT1002U에만 있는 코드)
```

---

### 11. 오후 분석 시간 레이블 수정 (PR #241)

오후 배치가 14:50 KST에 실행되도록 변경된 이후, 텔레그램 시그널 메시지의 시간 레이블이 실제 실행 시간과 불일치하던 문제를 수정했습니다.

```python
# Before
"장 마감 후"  # 15:30 이후를 의미

# After
"오후 분석"   # 14:50 실행 기준
```

---

## 변경된 주요 파일

| 파일 | PR | 변경 내용 |
|------|----|-----------|
| `trading/kis_auth.py` | #246, #245 | 계좌별 App Key, 토큰 격리, 선물 계좌 제외, 비숫자 계좌 skip |
| `prism-us/us_stock_tracking_agent.py` | #243, #245 | importlib 임포트 수정, DB 롤백 on 매수 실패 |
| `cores/agents/trading_agents.py` | #242, #248, #252 | 매도 규칙 제거, 횡보장 진입 정밀화, 시간 컨텍스트 강제화 |
| `prism-us/cores/agents/trading_agents.py` | #242, #248 | KR과 동일 변경 (US) |
| `cores/openai_error_logging.py` | #251 | 신규: OpenAI 오류 Request ID 로거 |
| `prism-us/cores/openai_error_logging.py` | #251 | 신규: US 모듈용 독립 로거 |
| `cores/openai_debug.py` | #251 | 400 에러 디버그 기능 보강 |
| `weekly_insight_report.py` | #250 | 트리거 성과 집계 쿼리 수정 |
| `stock_analysis_orchestrator.py` | #241, #251 | 오후 레이블 + OpenAI 로깅 |
| `prism-us/us_stock_analysis_orchestrator.py` | #241, #251 | 동일 (US) |
| `docker-compose.quickstart.yml` | — | Compose v2 + 로컬 빌드 개선 |
| `docker/quickstart-entrypoint.sh` | — | 신규: 퀵스타트 엔트리포인트 스크립트 |
| `trading/config/kis_devlp.yaml.example` | #246 | 계좌별 App Key 설정 예시 추가 |
| `AGENTS.md` | — | 신규: Codex용 에이전트 가이드 문서 |
| `prism-us/trading/us_stock_trading.py` | #253 | `ORD_DVSN "01"` → `"00"` + 현재가 지정가 매도 (APBK1269 수정) |

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

### 3. 다중 계좌 App Key 설정 (신규 — 선택사항)

IGW00002 오류가 발생했거나 계좌별 독립 App Key를 사용하려면 `kis_devlp.yaml`에 계좌별 `app_key`/`app_secret`을 추가하세요.

```yaml
# trading/config/kis_devlp.yaml
accounts:
  - id: primary
    app_key: YOUR_PRIMARY_APP_KEY
    app_secret: YOUR_PRIMARY_SECRET
    account_no: XXXXXXXX-XX
  - id: secondary
    app_key: YOUR_SECONDARY_APP_KEY
    app_secret: YOUR_SECONDARY_SECRET
    account_no: YYYYYYYY-YY
```

> 미설정 시 기존 전역 `my_app`/`my_sec`로 자동 fallback되므로 기존 단일 계좌 사용자는 변경 불필요합니다.

### 4. 동작 확인

```bash
# KR 전체 파이프라인 (텔레그램 없이)
python stock_analysis_orchestrator.py --mode morning --no-telegram

# US 전체 파이프라인 (텔레그램 없이)
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

---

## 알려진 제한사항

1. **계좌별 App Key**: KIS API는 1개의 App Key를 1개의 계좌에만 바인딩합니다. 다중 계좌 운용 시 각 계좌마다 별도의 App Key를 KIS 개발자 센터에서 발급받아야 합니다.
2. **횡보장 매도 규칙 변경**: 7일 한도·5% 익절 규칙 제거로 인해 보유 기간이 길어질 수 있습니다. 트레일링 스톱이 주요 청산 메커니즘이 됩니다.

---

## 텔레그램 구독자 공지 메시지

### 한국어

```
🚀 PRISM-INSIGHT v2.9.1 업데이트

이번 버전은 KIS 인증 안정성 전면 개선과 데이터 기반 매매 전략 정제가 핵심입니다.

🔑 KIS 계좌별 App Key + 토큰 격리
  • 다중 계좌 IGW00002 오류 근본 해결
  • 계좌마다 독립 토큰 파일로 인증 충돌 제거

📊 데이터 기반 매도 규칙 개선 (92 KR + 15 US 실거래 분석)
  • 약세장·횡보장 7일 한도 규칙 제거
  • 5% 익절 강제 청산 규칙 제거
  • 트레일링 스톱으로 자연스러운 청산

🛠 버그 수정
  • US 선물 계좌 US 팬아웃 포함 오류 수정
  • US 매수 실패 시 유령 보유 종목 생성 수정
  • US 장중 매도 APBK1269 (주문구분 오류) 수정
  • 주간 리포트 트리거 성과 0건 표시 오류 수정
  • OpenAI 오류 Request ID 추적 강화

🐋 Docker 퀵스타트 로컬 빌드 지원

git pull origin main 으로 업데이트하세요.
```

### English

```
🚀 PRISM-INSIGHT v2.9.1 Update

This release focuses on KIS authentication stability and data-driven trading strategy refinement.

🔑 Per-Account KIS App Key + Token Isolation
  • Root fix for IGW00002 errors in multi-account mode
  • Each account now gets its own isolated token file

📊 Data-Driven Sell Rule Improvement (92 KR + 15 US trades analyzed)
  • Removed 7-day observation limit in bear/sideways mode
  • Removed 5% profit-take forced exit rule
  • Trailing stop is now the primary exit mechanism

🛠 Bug Fixes
  • US futures accounts no longer included in US trade fan-out
  • Ghost holdings from failed US buy orders now properly rolled back
  • US market sell APBK1269 (invalid order type) fixed
  • Weekly report trigger performance no longer shows 0 entries
  • OpenAI error Request ID tracking added across all modules

🐋 Docker Quickstart local build now supported

Update with: git pull origin main
```
