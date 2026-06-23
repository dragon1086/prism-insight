# PRISM-INSIGHT 기능 게이트 레지스트리 (LIVE / SHADOW / OFF)

> **단일 진실원(intended state).** 릴리즈가 늘어도 "무엇이 실거래에 적용 중인지" 한눈에 보기 위한 문서.
> 실제 런타임 상태(서버 .env·crontab 기준)는 `tools/feature_status.py`로 대조 — 이 문서와 어긋나면 그 도구가 진실.
> 관리 주체 = 코딩 에이전트(cokac-bot). 매 릴리즈/승격 시 갱신.
> 최종 갱신: 2026-06-24.

## 상태 정의
- **LIVE** = 실거래/실발행에 실제 영향. **SHADOW** = 코드 동작하나 로그/관측만(영향 0). **OFF** = 미실행(코드만 존재). **N/A** = 미구현.

## 현황 한눈에

| 기능 | 상태 | 게이트 | 승격 기준 | 비고 |
|---|---|---|---|---|
| OAuth LLM 백엔드(ChatGPT 구독) | **LIVE** | crontab `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth` | 카나리 검증 완료 | 전 배치 적용 |
| TIER0 이벤트 강제청산(뉴스 자율매도 + KIS 51 관리종목) | **LIVE** | 코드 상시 | 더존 등 실증 | KR+US 매도 프롬프트 핵심-0 |
| Loop A — 고빈도 하드스톱(−7%/시나리오손절) | **LIVE** | `.env LOOP_A_LIVE=true` + cron 10분 | SHADOW 관측 후 승격(06-20) | KR 9–15 / US 9–16. 킬: `LOOP_A_ENABLED=false` |
| Loop B — 50MA 종가확인 추세이탈 | **SHADOW/미스케줄** | (cron·env 없음) | **cadence-aware 백테스트 순효과(휩쏘 vs 드로다운) 검증** | 코드: `tools/loop_b_trend_exit.py` |
| Loop C — 미체결 추격 + KIS TR 래퍼 | **SHADOW/미스케줄** | (cron·env 없음) | **신규 KIS 정정/취소 TR 소액 왕복 실주문 검증** | 코드: `tools/loop_c_fill_chaser.py` |
| 비전 배관(S1) / 렌더QA(S2) | **ON(log-only)** | `PRISM_FEATURE_VISION=on` | 무손상 인프라 | 렌더QA 비차단 경고만 |
| 비전 매수 품질검사(S3 + S3.5 오닐 일/주봉·RS) | **SHADOW** | `PRISM_FEATURE_VISION=on` + `PRISM_VISION_SHADOW=true` | **A/B 홀드아웃 측정(승률·손절률·MDD 순효과)** → 미정 | 관측 로그 `[BUY_QUALITY][SHADOW]`. 매매영향 0 |
| 비전 인사이트 이미지 발행(S6) | **OFF(기본)** | `.env PRISM_FEATURE_INSIGHT_IMAGE=on` **AND** `vision_available()`(`PRISM_FEATURE_VISION=on` + 실 API 키) | **샘플 사용자 승인 → `PRISM_FEATURE_INSIGHT_IMAGE=on`** | 발행 배선 구현됨(`cores/llm/features/insight_broadcast.py`, KR/US 오케스트레이터 배선). 둘 다 참일 때만 LIVE. 구독자 대상 = 발행 전 승인 필수 |

## 자동 승격 정책 (에이전트가 따른다)
SHADOW→LIVE **자동 승격**은 아래를 **모두** 충족할 때만:
1. 이 문서에 적힌 **승격 기준이 증거와 함께 충족**(백테스트 통과 / N일 무사고 SHADOW / 소액 실주문 검증 등).
2. **즉시 롤백 가능한 킬스위치(env 게이트)** 존재.
3. **되돌릴 수 있는 변경**(one-way door 아님).
→ 승격 시: 게이트 전환 + 이 문서에 **날짜·근거 기록** + **텔레그램으로 사용자에게 통지**(자동이되 투명).

**자동 승격하지 않고 반드시 먼저 묻는다**:
- **구독자/외부 대상 발행**(예: 인사이트 이미지 채널 송출) — 브랜드·구독자 영향.
- 깨끗한 롤백이 없거나, 기존 단위 사이징을 넘는 **자본 리스크 확대**.
- one-way door(되돌리기 어려운) 변경.

## 승격 대기열 (다음 LIVE 후보)
- **Loop B**: cadence-aware 백테스트 작성·실행 → 순효과 양수면 자동 승격 후보.
- **Loop C**: 신규 KIS TR 소액 왕복 1회 검증 → 통과 시 후보.
- **비전 매수게이트(S3)**: A/B 측정 설계 확정·데이터 축적 후 — **수익영향이라 사용자 확인 후**.

## 변경 이력
- 2026-06-23: 레지스트리 신설. 현황 기록(Loop A LIVE / B·C SHADOW미스케줄 / 비전 SHADOW관측).
- 2026-06-24: S6 발행 게이트 갱신 — 배선 구현 완료 반영. 게이트 `PRISM_FEATURE_INSIGHT_IMAGE=on` + `vision_available()`(이전 "발행 배선 미구현" 기재 정정). `feature_status.py`도 동일 로직으로 LIVE/OFF 보고.
