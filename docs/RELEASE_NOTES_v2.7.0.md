# PRISM-INSIGHT v2.7.0

발표일: 2026년 3월 18일

## 개요

PRISM-INSIGHT v2.7.0은 **GPT-5.4 하이브리드 모델 업그레이드**와 **운영 안정성 개선**에 초점을 맞춘 마이너 버전입니다.

보고서·요약 등 텍스트 생성 작업에는 비용 효율적인 gpt-5.4-mini를, 매매 판단 등 고도화된 추론이 필요한 작업에는 gpt-5.4를 적용하는 하이브리드 전략을 도입했습니다. 또한 US 예약주문 시간외 실패 문제를 해결하는 큐잉 시스템을 추가하고, 다수의 운영 에러를 수정했습니다.

**주요 수치:**
- 총 6개 PR (#207 ~ #212)
- 약 25개 파일 변경

---

## 주요 변경사항

### 1. GPT-5.4 하이브리드 모델 업그레이드 ⭐ NEW

용도별로 최적의 모델을 배정하여 **비용 3.3배 절감, 속도 2배 향상**을 달성했습니다.

| 용도 | Before | After | 이유 |
|------|--------|-------|------|
| 보고서 생성 (4곳) | gpt-5.2 | **gpt-5.4-mini** | reasoning 미사용, 텍스트 생성 위주 |
| 오케스트레이터 (2곳) | gpt-5.2 | **gpt-5.4-mini** | 거시경제 분석 JSON 출력 |
| 텔레그램 요약 (2곳) | gpt-5.2 | **gpt-5.4-mini** | 요약 평가, 경량 작업 |
| 로그 압축 (2곳) | gpt-5.2 | **gpt-5.4-mini** | 저널 엔트리 요약/압축 |
| 저널 기록 (2곳) | gpt-5.2 | **gpt-5.4-mini** | 매매 저널 생성 |
| 매매 판단 tracking (4곳) | gpt-5.2 | **gpt-5.4** | reasoning 필요, 보유종목 매도 판단 |
| 회사명 번역 (1곳) | gpt-4o-mini | **gpt-5-nano** | 다른 번역과 모델 통일, 최저 비용 |

#### mcp-agent 프레임워크 패치

gpt-5.4 계열은 `/v1/chat/completions`에서 function tools + `reasoning_effort` 조합 시 400 에러가 발생합니다. mcp-agent 프레임워크가 `reasoning_effort`를 항상 전송하는 문제를 fork에서 패치했습니다.

- upstream PR: [lastmile-ai/mcp-agent#648](https://github.com/lastmile-ai/mcp-agent/pull/648)
- 패치 머지 전까지 `requirements.txt`에서 fork 브랜치를 참조합니다.

---

### 2. US 예약주문 큐잉 시스템 (#210) ⭐ NEW

US 시장은 KIS 예약주문이 10:00 KST 이후에만 가능합니다. 이전에는 시간외 주문 시 API 에러가 발생했으나, 이제 자동으로 큐잉되어 배치 처리됩니다.

| 항목 | 설명 |
|------|------|
| **큐잉** | 10시 이전 주문은 `us_pending_orders` 테이블에 저장 |
| **배치 실행** | `us_pending_order_batch.py`가 10:05 KST에 cron으로 실행 |
| **dry-run** | `--dry-run` 옵션으로 실제 주문 없이 확인 가능 |

```bash
# 배치 스크립트 테스트
python prism-us/us_pending_order_batch.py --dry-run
```

---

### 3. 매크로 섹터 리더 + 역발상 가치주 트리거 (#209) ⭐ NEW

약세/횡보장에서도 후보 종목을 생성하는 신규 트리거 2종을 KR/US 동시 추가했습니다.

| 트리거 | 설명 | 활성 체제 |
|--------|------|-----------|
| **매크로 섹터 리더** | macro_context의 leading_sectors에서 직접 후보 생성 | 횡보장 주력 |
| **역발상 가치주** | 52주 고가 대비 -15~40% 하락 + 펀더멘털 건전 종목 | 약세장 주력 |

시장 체제별 활성화: 강세장=모멘텀 주력, 횡보=매크로 주력, 약세=역발상 주력

검증 결과 (moderate_bear 체제): 기존 모멘텀 트리거 0건인 상황에서 신규 트리거가 20건 후보를 생성했습니다.

---

### 4. 운영 에러 수정 (#208, #210)

| 문제 | 수정 |
|------|------|
| pykrx 직접 호출 시 KRX JSON 파싱 에러 | krx_data_client(kospi-kosdaq MCP 서버)로 교체 |
| 빈 OHLCV 데이터 종목 차트 생성 시 zero-size array 에러 | OHLC 전부 0인 행 필터링 추가 |
| Playwright 매 PDF 변환마다 브라우저 미설치 경고 반복 | 캐시 플래그 + `--dry-run` 체크 |
| Telegram PDF 전송 타임아웃 (30s) | 60s로 확장 |
| KIS 가격조회 NYSE → NYS 매핑 누락 | `PRICE_EXCHANGE_CODES` 분리 + 매핑 추가 |

---

### 5. US 매수보류 얼럿 포맷 통일 (#208)

US 매수보류 텔레그램 얼럿을 KR과 동일한 한국어 포맷으로 통일했습니다.

| 항목 | Before | After |
|------|--------|-------|
| 라벨 | 영어 (Skip, Current Price) | 한국어 (매수 보류, 현재가) |
| Buy Score | `{score}/{min_score}` | `{score}/10` 고정 |
| 시장 체제 | `moderate_bear` (영문 raw) | `보통 약세장` (한국어) |

---

### 6. US 트리거 배치 시가총액 필터 제거 (#208)

US 트리거 배치에서 시가총액 $20B 필터가 대부분의 모멘텀 종목을 차단하고 있었습니다. KR 트리거 배치에는 시가총액 필터가 없으므로 동일하게 제거했습니다.

---

### 7. KR/US 보고서 거시경제 섹션 소제목 추가 (#207)

분석 보고서의 거시경제 섹션에 소제목이 누락되어 있던 문제를 수정했습니다.

- KR: `### 거시경제 환경`
- US: `### Macroeconomic Environment`

---

## 변경된 파일

| 파일 | 주요 PR | 변경 내용 |
|------|---------|-----------|
| `cores/report_generation.py` | #207, #212 | 거시경제 소제목 추가 + gpt-5.4-mini 적용 |
| `cores/stock_chart.py` | #208 | 빈 OHLCV 데이터 방어 코드 |
| `cores/company_name_translator.py` | #212 | gpt-4o-mini → gpt-5-nano |
| `stock_analysis_orchestrator.py` | #208, #212 | krx_data_client 교체 + gpt-5.4-mini |
| `stock_tracking_agent.py` | #212 | gpt-5.4 적용 |
| `stock_tracking_enhanced_agent.py` | #212 | gpt-5.4 적용 |
| `telegram_summary_agent.py` | #212 | gpt-5.4-mini 적용 |
| `tracking/compression.py` | #212 | gpt-5.4-mini 적용 |
| `tracking/journal.py` | #212 | gpt-5.4-mini 적용 |
| `prism-us/us_stock_analysis_orchestrator.py` | #212 | gpt-5.4-mini 적용 |
| `prism-us/us_stock_tracking_agent.py` | #208, #212 | 얼럿 포맷 통일 + gpt-5.4 적용 |
| `prism-us/us_telegram_summary_agent.py` | #212 | gpt-5.4-mini 적용 |
| `prism-us/us_trigger_batch.py` | #208, #209 | 시가총액 필터 제거 + 신규 트리거 추가 |
| `prism-us/tracking/journal.py` | #212 | gpt-5.4-mini 적용 |
| `prism-us/tracking/db_schema.py` | #210 | `us_pending_orders` 테이블 추가 |
| `prism-us/trading/us_stock_trading.py` | #210 | PRICE_EXCHANGE_CODES + 큐잉 로직 |
| `prism-us/us_pending_order_batch.py` | #210 | 신규 배치 스크립트 |
| `trigger_batch.py` | #209 | 매크로 섹터 리더 + 역발상 가치주 트리거 추가 |
| `pdf_converter.py` | #210 | Playwright 캐시 + 타임아웃 확장 |
| `telegram_bot_agent.py` | #210 | send_document 타임아웃 60s |
| `docker/crontab` | #210 | 10:05 KST 배치 스케줄 추가 |
| `requirements.txt` | #212 | mcp-agent fork 참조 (gpt-5.4 패치) |

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

> ⚠️ mcp-agent가 fork 브랜치에서 설치됩니다. upstream PR 머지 후 정식 버전으로 복원 예정.

### 3. 동작 확인

```bash
# KR 전체 파이프라인 (텔레그램 없이)
python stock_analysis_orchestrator.py --mode morning --no-telegram

# US 전체 파이프라인 (텔레그램 없이)
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# US 예약주문 배치 테스트
python prism-us/us_pending_order_batch.py --dry-run
```

---

## 알려진 제한사항

1. **mcp-agent fork 의존**: gpt-5.4 + function tools 호환 패치가 upstream에 머지될 때까지 fork 브랜치를 참조합니다. upstream [PR #648](https://github.com/lastmile-ai/mcp-agent/pull/648) 머지 후 `requirements.txt`를 `mcp-agent>=0.2.7`로 복원해야 합니다.
2. **gpt-5.4-mini context window**: 400K 토큰으로 gpt-5.4(1M)보다 작지만, 현재 사용량으로는 충분합니다.
3. **역발상 가치주 트리거**: 2주간 performance_tracker로 후행 성과 모니터링 중입니다.
