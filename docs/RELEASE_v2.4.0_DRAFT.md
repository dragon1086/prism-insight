# PRISM-INSIGHT v2.4.0

발표일: 2026년 2월 10일

## 개요

PRISM-INSIGHT v2.4.0은 **MCP tool call을 직접 API prefetch로 전환**, **트리거 신뢰도 카드**, **Firebase Bridge (PRISM-Mobile 연동)** 를 포함한 마이너 버전입니다. pykrx(KR)/yfinance(US) 직접 호출로 데이터를 prefetch하여 MCP·Firecrawl·Perplexity 호출을 대폭 절감, **OpenAI API 비용을 KR ~50%, US ~30% 절감**했습니다. 트리거별 성과를 A/B/C/D 등급으로 시각화하는 신뢰도 카드도 추가되었습니다.

**주요 수치:**
- 총 13개 커밋
- 44개 파일 변경
- +3,851 / -627 라인
- **OpenAI API 비용 30~50% 절감** (KR ~50%, US ~30%)

---

## 주요 변경사항

### 1. MCP Tool Call → 직접 API Prefetch ⭐ CORE

종목 분석 시 MCP 서버를 경유하던 데이터 조회를 Python API 직접 호출(pykrx/yfinance)로 전환했습니다. Prefetch된 데이터는 마크다운 형태로 에이전트 instruction에 주입됩니다.

#### 비용 절감 효과

| 시장 | API 비용 절감 | 주요 요인 |
|------|-------------|----------|
| **KR** | **~50%** | MCP 호출 제거 + Firecrawl/Perplexity 호출 대폭 감소 |
| **US** | **~30%** | MCP 호출 제거 (yfinance 직접 전환) |

KR 시장에서 절감 효과가 더 큰 이유는 MCP 호출 제거뿐만 아니라 Firecrawl(웹 스크래핑)과 Perplexity(검색) 호출 횟수까지 줄어든 것이 크게 작용했습니다. Prefetch로 에이전트에 데이터를 사전 주입하면 에이전트가 추가 tool call을 할 필요가 없어지므로 연쇄적으로 외부 API 호출이 감소합니다.

#### 1.1 KR 시장 (pykrx)

| 항목 | Before (MCP) | After (Direct) |
|------|-------------|----------------|
| OHLCV 데이터 | pykrx MCP tool call | `cores/data_prefetch.py` → pykrx 직접 호출 |
| 거래량 데이터 | pykrx MCP tool call | `cores/data_prefetch.py` → pykrx 직접 호출 |
| KOSPI/KOSDAQ 지수 | pykrx MCP tool call | `cores/data_prefetch.py` → pykrx 직접 호출 |
| **절감량** | **종목당 ~4회 MCP 호출 제거** | |

#### 1.2 US 시장 (yfinance)

| 항목 | Before (MCP) | After (Direct) |
|------|-------------|----------------|
| OHLCV 데이터 | yahoo_finance MCP tool call | `prism-us/cores/data_prefetch.py` → yfinance 직접 호출 |
| 주주 정보 | yahoo_finance MCP tool call | `prism-us/cores/data_prefetch.py` → yfinance 직접 호출 |
| S&P500/NASDAQ/Dow/Russell/VIX | yahoo_finance MCP tool call | `prism-us/cores/data_prefetch.py` → yfinance 직접 호출 |
| **절감량** | **종목당 ~9회 MCP 호출 제거** | |

#### 1.3 Fallback 전략

Prefetch 실패 시 기존 MCP 경로로 자동 fallback됩니다. 완전 하위호환이며 설정 변경 없이 적용됩니다.

```python
# cores/analysis.py
try:
    prefetched = await prefetch_stock_data(ticker)
except Exception:
    prefetched = {}  # fallback → MCP 경로 유지
```

#### 1.4 수정 파일

**신규 파일:**

| 파일 | 설명 |
|------|------|
| `cores/data_prefetch.py` | KR 데이터 prefetch (pykrx) |
| `prism-us/cores/data_prefetch.py` | US 데이터 prefetch (yfinance) |

**수정 파일 (KR):**

| 파일 | 변경 |
|------|------|
| `cores/analysis.py` | 에이전트 생성 전 prefetch 호출 |
| `cores/agents/__init__.py` | prefetched 데이터를 에이전트에 전달 |
| `cores/agents/stock_price_agents.py` | OHLCV/거래량 데이터 주입 |
| `cores/agents/market_index_agents.py` | KOSPI/KOSDAQ 지수 데이터 주입 |

**수정 파일 (US):**

| 파일 | 변경 |
|------|------|
| `prism-us/cores/us_analysis.py` | 에이전트 생성 전 prefetch 호출 |
| `prism-us/cores/agents/__init__.py` | prefetched 데이터를 에이전트에 전달 |
| `prism-us/cores/agents/stock_price_agents.py` | OHLCV/주주 정보 주입 |
| `prism-us/cores/agents/market_index_agents.py` | S&P500/NASDAQ/Dow/Russell/VIX 주입 |
| `prism-us/cores/us_data_client.py` | finnhub 의존성 제거 |

---

### 2. 트리거 신뢰도 카드 ⭐ NEW

3개 데이터 소스(분석 트래커, 매매 이력, 매매 원칙)를 트리거 타입별로 교차 분석하여 A/B/C/D 등급으로 신뢰도를 표시합니다. KR/US 양 시장을 모두 지원합니다.

#### 2.1 등급 기준

| 등급 | 조건 |
|------|------|
| **A** | 분석 승률 ≥60% + 매매 승률 ≥60% + 매매 횟수 ≥5 |
| **B** | 분석 승률 ≥50% + 매매 승률 ≥50% |
| **C** | 위 기준 미달 |
| **D** | 데이터 부족 |

#### 2.2 Phase별 구현

| Phase | 내용 | 주요 파일 |
|-------|------|----------|
| Phase 1 | 백엔드 데이터 레이어 | `generate_dashboard_json.py`, `generate_us_dashboard_json.py` |
| Phase 2 | 대시보드 Insights 탭 | `trigger-reliability-card.tsx`, `trading-insights-page.tsx` |
| Phase 3 | 메인 탭 미니 배지 | `trigger-reliability-badge.tsx` |
| Phase 4 | 텔레그램 봇 `/triggers` 명령 | `telegram_ai_bot.py` |
| Phase 5 | 주간 인사이트 리포트 + Crontab | `weekly_insight_report.py`, `OPERATION_GUIDE.md` |
| Phase 6 | 매매 알림에 트리거 승률 표시 | `stock_tracking_agent.py`, `us_stock_tracking_agent.py` |

#### 2.3 테스트

- `tests/test_trigger_reliability.py` — 15개 단위 테스트

---

### 3. Firebase Bridge (PRISM-Mobile 연동) ⭐ NEW

텔레그램 메시지 메타데이터를 Firestore에 저장하고 FCM 푸시 알림을 전송하는 opt-in 모듈입니다. PRISM-Mobile 앱과의 연동을 위한 기반입니다.

#### 3.1 특징

- **기본 비활성화**: `FIREBASE_BRIDGE_ENABLED=true`로 설정해야 활성화
- 시장(kr/us), 타입(trigger/analysis/portfolio/pdf) 자동 감지
- 제목/미리보기/종목 정보 자동 추출
- 모든 bridge 호출은 try/except로 감싸 — 텔레그램 전송에 영향 없음

#### 3.2 연동 포인트

| 파일 | 연동 |
|------|------|
| `telegram_bot_agent.py` | send_message, plain text retry, send_document (3곳) |
| `tracking/telegram.py` | _send_single_message (1곳) |

#### 3.3 설정 방법

```bash
# .env
FIREBASE_BRIDGE_ENABLED=true
GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-service-account.json
TELEGRAM_CHANNEL_USERNAME=your_telegram_channel_username

pip install firebase-admin>=6.0.0
```

#### 3.4 테스트

- `tests/test_firebase_bridge.py` — 12개 단위 테스트

---

### 4. 주간 인사이트 리포트

트리거 신뢰도 데이터를 주간 단위로 요약하여 텔레그램으로 전송하는 자동 리포트입니다.

| 항목 | 내용 |
|------|------|
| 파일 | `weekly_insight_report.py` |
| 모드 | `--dry-run` 지원 |
| 스케줄 | Crontab 설정 가이드 제공 (`OPERATION_GUIDE.md`) |

---

### 5. 매매 알림 트리거 승률 표시

매수/매도/스킵 알림 메시지에 해당 트리거의 과거 승률을 한 줄로 표시합니다.

```python
# _get_trigger_win_rate() 헬퍼 추가
# KR: stock_tracking_agent.py
# US: us_stock_tracking_agent.py
```

---

### 6. 기타 변경

| 항목 | 변경 |
|------|------|
| `.env.example` | Firebase Bridge 설정 예시 추가 |
| `requirements.txt` | `firebase-admin>=6.0.0` 추가 |
| `CLAUDE.md` | v2.4.0 버전 히스토리 추가 |
| `docker-compose.quickstart.yml` | 업데이트 |
| `docs/SETUP.md`, `docs/SETUP_ko.md` | 설정 가이드 업데이트 |
| `docs/US_STOCK_PLAN.md` | US 모듈 문서 업데이트 |
| `prism-us/cores/us_data_client.py` | 미사용 finnhub 의존성 제거 |
| `prism-us/tests/test_phase2_data_client.py` | 테스트 업데이트 |

---

## 변경된 파일

### 신규 파일

| 파일 | 설명 |
|------|------|
| `cores/data_prefetch.py` | KR 데이터 prefetch 모듈 (pykrx) |
| `prism-us/cores/data_prefetch.py` | US 데이터 prefetch 모듈 (yfinance) |
| `firebase_bridge.py` | Firebase Bridge 모듈 |
| `tests/test_firebase_bridge.py` | Firebase Bridge 단위 테스트 (12개) |
| `tests/test_trigger_reliability.py` | 트리거 신뢰도 단위 테스트 (15개) |
| `weekly_insight_report.py` | 주간 인사이트 리포트 |
| `examples/dashboard/components/trigger-reliability-badge.tsx` | 트리거 신뢰도 미니 배지 |
| `examples/dashboard/components/trigger-reliability-card.tsx` | 트리거 신뢰도 카드 |
| `docs/trigger-reliability/DATA_LINEAGE.md` | 데이터 리니지 문서 |
| `docs/trigger-reliability/OPERATION_GUIDE.md` | 운영 가이드 |
| `docs/trigger-reliability/PLAN.md` | 구현 계획 문서 |

### 주요 수정 파일

| 파일 | 주요 변경 |
|------|----------|
| `cores/analysis.py` | **에이전트 생성 전 prefetch 호출** |
| `cores/agents/__init__.py` | **prefetched 데이터 에이전트 전달** |
| `cores/agents/stock_price_agents.py` | **OHLCV/거래량 데이터 주입** |
| `cores/agents/market_index_agents.py` | **KOSPI/KOSDAQ 지수 데이터 주입** |
| `prism-us/cores/us_analysis.py` | **에이전트 생성 전 prefetch 호출** |
| `prism-us/cores/agents/__init__.py` | **prefetched 데이터 에이전트 전달** |
| `prism-us/cores/agents/stock_price_agents.py` | **OHLCV/주주 정보 주입** |
| `prism-us/cores/agents/market_index_agents.py` | **S&P500/NASDAQ/Dow/Russell/VIX 주입** |
| `telegram_bot_agent.py` | Firebase Bridge 연동 (3곳) |
| `tracking/telegram.py` | Firebase Bridge 연동 (1곳) |
| `telegram_ai_bot.py` | `/triggers` 명령 추가 |
| `stock_tracking_agent.py` | 트리거 승률 알림 추가 |
| `prism-us/us_stock_tracking_agent.py` | 트리거 승률 알림 추가 |
| `examples/generate_dashboard_json.py` | 트리거 신뢰도 데이터 생성 |
| `examples/generate_us_dashboard_json.py` | 트리거 신뢰도 데이터 생성 (US) |
| `examples/dashboard/components/trading-insights-page.tsx` | Insights 탭에 신뢰도 카드 추가 |
| `examples/dashboard/types/dashboard.ts` | 신뢰도 TypeScript 타입 추가 |
| `examples/dashboard/components/language-provider.tsx` | i18n 키 추가 (20개) |

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

> Prefetch(pykrx/yfinance)는 기존 의존성만으로 동작합니다. 추가 설치 불필요.

### 3. 대시보드 빌드 (대시보드 사용 시)

```bash
cd examples/dashboard && npm run build
```

### 4. 주간 인사이트 리포트 crontab 설정 (선택)

매주 일요일 10:00 KST에 트리거 신뢰도 주간 리포트를 텔레그램에 자동 발송합니다.

```bash
# crontab 편집
crontab -e

# 아래 줄 추가 (경로를 실제 프로젝트 경로로 변경)
0 10 * * 0 cd /path/to/prism-insight && /path/to/python3 weekly_insight_report.py >> /path/to/prism-insight/logs/weekly_report.log 2>&1
```

서버 시간대가 UTC인 경우 (GCP VM 등):
```bash
# UTC 01:00 = KST 10:00
0 1 * * 0 cd /path/to/prism-insight && python3 weekly_insight_report.py >> logs/weekly_report.log 2>&1
```

먼저 dry-run으로 동작을 확인하세요:
```bash
python3 weekly_insight_report.py --dry-run
```

### 5. Firebase Bridge 설정 (선택)

PRISM-Mobile 앱 연동이 필요한 경우에만 설정합니다.

```bash
# .env에 추가
FIREBASE_BRIDGE_ENABLED=true
GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-service-account.json
TELEGRAM_CHANNEL_USERNAME=your_telegram_channel_username
```

### 6. 텔레그램 봇 /triggers 명령어

별도 설정 없이 자동 활성화됩니다. 텔레그램에서 봇에게 `/triggers`를 입력하면 KR/US 트리거 신뢰도 리포트를 실시간 조회할 수 있습니다.

**참고:**
- DB 마이그레이션 없음
- 기존 설정 변경 없음 (Firebase Bridge, 주간 리포트는 opt-in)
- Prefetch는 자동 적용, 실패 시 MCP fallback
- `/triggers` 명령은 별도 설정 없이 즉시 사용 가능

---

## 테스트

```bash
# 트리거 신뢰도 테스트 (15 tests)
pytest tests/test_trigger_reliability.py -v

# Firebase Bridge 테스트 (12 tests)
python3 tests/test_firebase_bridge.py

# KR 분석 테스트 (prefetch 동작 확인)
python demo.py 005930

# US 분석 테스트 (prefetch 동작 확인)
python demo.py AAPL --market us

# 대시보드 빌드 확인
cd examples/dashboard && npm run build

# 주간 리포트 dry-run
python3 weekly_insight_report.py --dry-run
```

---

## 알려진 제한사항

1. **Prefetch 데이터 범위**: 예측 불가능한 데이터(뉴스, 실시간 시세 등)는 여전히 MCP 경유
2. **Firebase Bridge**: 라이브 Firebase 프로젝트로의 end-to-end 테스트는 별도 필요
3. **트리거 신뢰도 등급**: 매매 횟수 5회 미만일 경우 D등급 (데이터 축적 필요)

---

## 기여자

- PRISM-INSIGHT Development Team
- Claude Opus 4.6 (AI Pair Programmer)

---

**Document Version**: 2.4.0
**Last Updated**: 2026-02-10

---

## 📢 텔레그램 구독자용 요약

> 아래 내용을 텔레그램 채널에 공유할 수 있습니다.

---

PRISM-INSIGHT v2.4.0 업데이트 안내

발표일: 2026년 2월 10일

안녕하세요, 프리즘 인사이트 구독자 여러분!
v2.4.0 버전이 출시되었습니다.

[핵심 업데이트 1: 분석 속도 개선]

종목 분석 시 데이터를 직접 조회하도록 변경하여 분석 속도가 크게 개선되었습니다.

기존에는 AI 에이전트가 MCP 서버를 거쳐 주가/거래량/지수 데이터를 조회했는데, 이제 Python에서 직접 조회한 뒤 에이전트에 전달합니다. 이로 인해 MCP 호출뿐 아니라 Firecrawl, Perplexity 등 외부 API 호출도 연쇄적으로 감소하여, OpenAI API 비용이 한국 약 50%, 미국 약 30% 절감되었습니다.

기존 방식은 fallback으로 유지되므로, 문제 발생 시 자동으로 이전 방식으로 전환됩니다.

[핵심 업데이트 2: 트리거 신뢰도 카드]

각 트리거(장중 급등, 거래량 급증 등)의 과거 성과를 A/B/C/D 등급으로 한눈에 확인할 수 있습니다.

분석 트래커, 매매 이력, 매매 원칙 3가지 데이터를 교차 분석하여 등급을 매깁니다:
- A등급: 분석+매매 승률 모두 60% 이상, 매매 5회 이상
- B등급: 양쪽 승률 50% 이상
- C/D등급: 기준 미달 또는 데이터 부족

대시보드 Insights 탭, 텔레그램 /triggers 명령, 주간 리포트에서 확인 가능합니다.

[매매 알림 개선]

매수/매도/스킵 알림에 해당 트리거의 과거 승률이 함께 표시됩니다. "이 트리거로 과거에 얼마나 성공했는지"를 바로 확인할 수 있습니다.

[PRISM-Mobile 준비]

Firebase Bridge 모듈이 추가되었습니다 (기본 비활성화). 향후 PRISM-Mobile 앱에서 푸시 알림을 받을 수 있는 기반이 마련되었습니다.

[대시보드에서 확인하세요]

https://analysis.stocksimulation.kr/?tab=insights

트리거별 신뢰도 등급을 확인해보세요!

문의사항은 언제든 봇에게 메시지 남겨주세요!
