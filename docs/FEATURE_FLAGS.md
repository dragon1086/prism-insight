# PRISM-INSIGHT 기능 게이트 레지스트리 (LIVE / SHADOW / OFF)

> **단일 진실원(intended state).** 릴리즈가 늘어도 "무엇이 실거래에 적용 중인지" 한눈에 보기 위한 문서.
> 실제 런타임 상태(서버 .env·crontab 기준)는 `tools/feature_status.py`로 대조 — 이 문서와 어긋나면 그 도구가 진실.
> 관리 주체 = 코딩 에이전트(cokac-bot). 매 릴리즈/승격 시 갱신.
> 최종 갱신: 2026-07-12.

## 상태 정의
- **LIVE** = 실거래/실발행에 실제 영향. **SHADOW** = 코드 동작하나 로그/관측만(영향 0). **OFF** = 미실행(코드만 존재). **N/A** = 미구현.

## 네이밍 용어집 (loop_a/b/c → descriptive rename)

암호적이던 `loop_a/loop_b/loop_c` 이름을 자기설명적 이름으로 리네임했다. **구 스크립트 경로는 deprecation shim으로 그대로 동작**하므로 기존 prod/구독자 crontab은 수정 없이 유지된다.

| 레거시 이름 | 새 이름 (descriptive) | env prefix (canonical) | 스크립트 경로 (신규) | 구 경로 (deprecated shim, 여전히 동작) |
|---|---|---|---|---|
| Loop A | Hardstop — 고빈도 손절 | `HARDSTOP_` (구 `LOOP_A_` alias 유효) | `tools/hardstop_seller.py` | `tools/loop_a_hardstop.py` |
| Loop B | Trend-exit — 50MA 추세이탈 매도 | `TREND_EXIT_` (구 `LOOP_B_` alias 유효) | `tools/trend_exit_seller.py` | `tools/loop_b_trend_exit.py` |
| Loop C | Fill-chaser — 미체결 추격 | `FILL_CHASER_` (구 `LOOP_C_` alias 유효) | `tools/fill_chaser.py` | `tools/loop_c_fill_chaser.py` |
| loop_publish | sell_broadcast | — | `sell_broadcast.py` | `loop_publish.py` (re-export shim) |

> **DB 테이블 이름은 불변**: `loop_a_position_state`, `loop_a_inflight_orders`, `loop_b_position_state`, `loop_b_inflight_orders`, `loop_c_chase_log` 는 라이브 상태·크로스루프 락을 담고 있어 **상태 연속성**을 위해 레거시 이름을 유지한다. Pub/Sub 페이로드/프로토콜 값도 불변.

## 현황 한눈에

| 기능 | 상태 | 게이트 | 승격 기준 | 비고 |
|---|---|---|---|---|
| OAuth LLM 백엔드(ChatGPT 구독) | **LIVE** | crontab `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth` | 카나리 검증 완료 | 전 배치 적용 |
| Market Pulse 배치 정책 | **LIVE** | `.env MARKET_PULSE_MODE=live` | 정책 단위테스트 + 정규장 관측 | KR/US 모두 오전·오후 2회. `UNDER_PRESSURE`는 두 배치 유지, `CORRECTION`은 오후만 실행. 10분 hardstop/trend-exit 및 2분 fill-chaser는 모든 상태에서 유지 |
| TIER0 이벤트 강제청산(뉴스 자율매도 + KIS 51 관리종목) | **LIVE** | 코드 상시 | 더존 등 실증 | KR+US 매도 프롬프트 핵심-0 |
| Loop A — 고빈도 하드스톱(−7%/시나리오손절) | **LIVE** | `.env HARDSTOP_LIVE=true` (구 `LOOP_A_LIVE`, alias 유효) + cron 10분 | SHADOW 관측 후 승격(06-20) | KR 9–15 / US 9–16. 킬: `HARDSTOP_ENABLED=false` |
| Loop B — 50MA 종가확인 추세이탈 | **LIVE** | `.env TREND_EXIT_LIVE=true` (구 `LOOP_B_LIVE`) + cron(KR 9–15 / US 9–16) | 백테스트 KR/US 순효과(휩쏘0·추가DD0) + 사용자 승인(06-24) | 코드: `tools/trend_exit_seller.py` (구 `tools/loop_b_trend_exit.py` shim 유효). 킬: `TREND_EXIT_ENABLED=false` |
| Loop C — 미체결 추격 + KIS TR 래퍼 | **SHADOW** | cron(KR/US */2분, `FILL_CHASER_LIVE` 미설정; 구 `LOOP_C_LIVE` alias) | **신규 KIS 정정/취소 TR 실 KIS 수락 검증**(dry-run/`--selftest`로 페이로드 필드는 검증됨) | 코드: `tools/fill_chaser.py` (구 `tools/loop_c_fill_chaser.py` shim 유효). 매수=체결우선 cross(예산 `FILL_CHASER_BUY_MAX_PREMIUM_PCT`=3%, `FILL_CHASER_BUY_CROSS`=on). 상세로깅 `[LOOP_C][SHADOW]` |
| 재진입 쿨다운 게이트(매수측) | **SHADOW** | `REENTRY_COOLDOWN_LIVE` 미설정 | SHADOW 며칠 관측(`[REENTRY_COOLDOWN][SHADOW] WOULD_BLOCK` ↔ 실매수 대조) → LIVE 승격 | 코드: `reentry_cooldown.py` (KR/US 매수 caller 훅). 손실매도 후 24h 재매수 차단(승리후 0h). prod 이력검증=리벤지 3건 차단·오탐0 |
| 비전 배관(S1) / 렌더QA(S2) | **ON(log-only)** | `PRISM_FEATURE_VISION=on` | 무손상 인프라 | 렌더QA 비차단 경고만 |
| 비전 매수 품질검사(S3 + S3.5 오닐 일/주봉·RS) | **SHADOW** | `PRISM_FEATURE_VISION=on` + `PRISM_VISION_SHADOW=true` | **A/B 홀드아웃 측정(승률·손절률·MDD 순효과)** → 미정 | 관측 로그 `[BUY_QUALITY][SHADOW]`. 매매영향 0 |
| 비전 인사이트 이미지 발행(S6) | **LIVE** | `.env PRISM_FEATURE_INSIGHT_IMAGE=on` **AND** `vision_available()`(`PRISM_FEATURE_VISION=on` + 실 API 키) | 샘플 사용자 승인 후 활성화(06-24) | KR(₩)/US($) 발송 중. 차트에 매수▲/매도▼ 마커·용어설명 포함. 끄기: `PRISM_FEATURE_INSIGHT_IMAGE=off` |
| Post-FTD 파일럿 재진입(정찰 신규진입 스로틀) | **OFF** | `.env PULSE_PILOT_REEXPOSURE=true` (기본 off) | 파일럿 윈도우 실관측 후 | 조정(CORRECTION) 종료 후 5거래일간 **신규 진입 배치당 1종목(top-down 주도주 우선) + 중복매수(피라미딩) 동결**. **금액은 항상 100% 정상**(all-in/all-out per position 계약 유지, fractional sizing 미사용 → sim/real parity). 시뮬레이터·실주문 공통 결정 레이어에서 적용. fail-open. 구 금액 절반매수(`PULSE_PILOT_FACTOR`)는 sim/real 괴리 결함으로 **제거**. 코드: `cores/regime_policy.py`, `trigger_batch`/`us_trigger_batch._get_regime_slots`, KR/US tracking agents 중복매수 동결 |

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
- ✅ **Loop B**: LIVE 승격 완료(06-24, 백테스트 KR/US 통과 + 사용자 승인).
- **Loop C**: 실 KIS 수락 검증(소액 왕복 1회) → 통과 시 후보. (SHADOW 상세로깅·`--selftest`로 페이로드는 검증됨.)
- **비전 매수게이트(S3)**: A/B 측정 설계 확정·데이터 축적 후 — **수익영향이라 사용자 확인 후**.

## 변경 이력
- 2026-06-23: 레지스트리 신설. 현황 기록(Loop A LIVE / B·C SHADOW미스케줄 / 비전 SHADOW관측).
- 2026-06-24: S6 발행 게이트 갱신 — 배선 구현 완료 반영. 게이트 `PRISM_FEATURE_INSIGHT_IMAGE=on` + `vision_available()`(이전 "발행 배선 미구현" 기재 정정). `feature_status.py`도 동일 로직으로 LIVE/OFF 보고.
- 2026-06-24: **승격·활성화 반영** — Loop B → **LIVE**(`LOOP_B_LIVE=true`+cron, 백테스트 KR/US 통과+승인). Loop C → **SHADOW**(cron 설치, `LOOP_C_LIVE` 미설정; 상세로깅+selftest 추가). S6 발행 → **LIVE**(`PRISM_FEATURE_INSIGHT_IMAGE=on`, 사용자 승인; 매매마커·용어설명 포함).
- 2026-06-25: **env 키 리네임(코드네임 누수 제거)** — `LOOP_A_*`→`HARDSTOP_*`, `LOOP_B_*`→`TREND_EXIT_*`, `LOOP_C_*`→`FILL_CHASER_*`. **구 키는 deprecated alias로 계속 유효**(코드가 신규 먼저 읽고 구 키 폴백+경고). prod `.env`/crontab 점진 교체 가능. + **Loop C 매수추격 체결우선화**: 예산 `FILL_CHASER_BUY_MAX_PREMIUM_PCT` 0.5%→3%, `FILL_CHASER_BUY_CROSS`(on)로 예산 내 마케터블 cross 즉시체결(예산 초과 시 여전히 CANCEL). SHADOW 유지.
- 2026-06-25: **재진입 쿨다운 게이트 신설(SHADOW)** — `reentry_cooldown.py` + KR/US 매수 caller 훅. 손실매도 후 같은 종목 24h 재매수 차단(승리후 0h=정당 연속진입 허용). MU 과매매(당일왕복 −5.6% 31건·손절후 재매수) 대응. `REENTRY_COOLDOWN_LIVE` 미설정=SHADOW(로그만). prod 이력검증: 리벤지 재매수 3건 차단·오탐 0.
- 2026-07-12: **Post-FTD 파일럿 재진입 재설계(sim/real parity 결함 수정)** — 구 `PULSE_PILOT_FACTOR` 금액 절반매수를 **제거**했다. 결함: 실 KIS 주문(`buy_amount`)만 절반이고 시뮬레이터(방송/저널의 진실원)는 전량 기록 → sim-vs-real 괴리. 본 시스템은 포지션당 all-in/all-out이고 포트폴리오 비중은 **중복매수(피라미딩) 허용**으로만 표현하므로 fractional sizing 자체가 계약 위반이었다. 신규 세만틱: `PULSE_PILOT_REEXPOSURE` ON 시 조정 종료 후 5거래일간 **신규 진입 배치당 1종목(주도주 top-down 우선) + 중복매수 동결**을 시뮬레이터/실주문 공통 결정 레이어(`_get_regime_slots` + tracking agents 보유중복 프리체크)에서 적용. **금액은 항상 100% 정상.** 기본 off, fail-open.
- 2026-07-13: **US 분석 배치 3회→2회** — 장중 분석 배치를 제거했다. US는 정상·UNDER_PRESSURE에서 오전+오후를 실행하고, CORRECTION에서는 KR과 동일하게 오전을 쉬고 오후만 실행한다. 고빈도 hardstop/trend-exit/fill-chaser 스케줄은 변경하지 않는다. 상세 검증·배포 체크는 `docs/US_TWO_BATCH_POLICY.md`를 따른다.
