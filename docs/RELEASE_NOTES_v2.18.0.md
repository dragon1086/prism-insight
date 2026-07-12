# PRISM-INSIGHT v2.18.0 — 오닐 Market Pulse 상태머신 · 약세장 매수 하드게이트 · 파일럿 재진입 · 루프 리네임

> **Release Date**: 2026-07-12
> **Range**: `v2.17.0`(869e7b75) → `main`(a525c346) · 16 commits / 9 PRs (#422–#430) + docs 직커밋 1건
> **Scale**: 46 files, +5,680 / −2,417

## 개요

v2.18.0의 주제는 하나로 요약된다: **"확실한 기회가 없으면 기다린다"(오닐)를 감(感)이 아니라 코드로.**
윌리엄 오닐의 M(Market Direction)을 분산일 카운팅 → CORRECTION → FTD(팔로우스루) 상태머신으로
구현해 6년 리플레이로 검증했고(LIVE 가동), 약세·횡보장에서 신규 매수를 조이는 하드게이트 4종을
붙였다. 조정 탈출 직후엔 정찰(파일럿) 포지션 1개로 발만 담근 뒤 정상 재개한다 — 금액은 항상
100%라 시뮬레이터와 실계좌가 단 한 주도 어긋나지 않는다. 덤으로 3년 묵은 암호명 loop_a/b/c를
hardstop / trend_exit / fill_chaser로 리네임했다(구 경로 shim 유지, crontab 무수정 호환).

---

## 1. Market Pulse — 오닐 M 상태머신 & 조정장 배치 휴식 ⭐ (이번 릴리즈 최대 비중, #425 #426 #427)

- **O'Neil M 상태머신** (`cores/market_pulse.py`): IBD 표준 분산일(DD) 카운팅(25세션 롤링,
  −0.2% & 거래량 증가) → DD 누적 시 CORRECTION 진입 → **FTD(랠리 4일차+ & +1.25% & 거래량 증가)**
  또는 전고점 회복 종가로만 탈출. −10% drawdown 즉시 진입 규칙 포함.
- **6년 리플레이 검증기** (`tools/market_pulse_backtest.py`, 388줄): KR/US 지수로 상태 전이를
  재현해 정책을 데이터로 결정. "CORRECTION=전면 매수중단"은 실거래 표본 감사에서 기각
  (조정창 매수 순손익 +25.3%) — 대신 **배치 감속**을 채택.
- **배치 휴식 정책** (`cores/regime_policy.py`, Rev.3→Rev.5): CORRECTION에서 KR은 오후
  (종가확인)만, US는 midday만 가동. 매도/청산 루프는 절대 쉬지 않음. 데이터 오류 시 fail-open.
- **구독자 휴식 공지**: LIVE 배치 스킵 시 구독 채널에 5개 언어로 휴식 사유 발송 (#426).
- 운영 상태: `MARKET_PULSE_MODE=live`.

## 2. 약세·횡보장 매수 하드게이트 & 파일럿 재진입 ⭐ (#429 #430)

- **US 약세장 모멘텀추격 차단 (버그픽스)**: `REGIME_WEAK_NO_TOPDOWN`이 KR에만 구현되어 있어
  플래그가 켜져 있어도 미국장은 횡보약세에서 top-down 추격을 계속하던 비대칭을 미러링으로 수정.
- **UNDER_PRESSURE 감속 (Rev.5)**: 조정 판정 전 압박 구간에서 US 아침 배치만 휴식.
- **레짐 적응 매수 하한선** (`REGIME_MIN_SCORE_FLOOR`): strong_bear ≥9점, moderate_bear/sideways
  ≥8점 미만 셋업은 LLM이 사자고 해도 거부하는 하드게이트. LLM min_score와 max() 결합.
- **파일럿 재진입 (Post-FTD)** (`PULSE_PILOT_REEXPOSURE`): 조정 종료 후 5거래일간 배치당 신규
  진입 1종목(주도주 우선) + 중복매수 동결. **금액은 항상 100% 정상** — 최초 구현(금액 절반)은
  시뮬-실계좌 괴리를 만들어 당일 재설계(#430). 의사결정 레이어에서 작동해 sim/real parity 보장.
- 모든 게이트 env-gated + fail-open(판정 오류 시 정상 매수).

## 3. 루프 리네임 — loop_a/b/c 암호 청산 (#428)

- `tools/loop_a_hardstop.py` → `tools/hardstop_seller.py` (HARDSTOP_*)
- `tools/loop_b_trend_exit.py` → `tools/trend_exit_seller.py` (TREND_EXIT_*)
- `tools/loop_c_fill_chaser.py` → `tools/fill_chaser.py` (FILL_CHASER_*)
- `loop_publish.py` → `sell_broadcast.py`
- 로거명·로그 프리픽스(`[LOOP_C]`→`[FILL_CHASER]`)·내부 상수·테스트 6파일 일괄 정리.
- **하위호환 완비**: 구 경로는 deprecation shim(경고 1줄 후 새 모듈 실행) — 기존 crontab 무수정
  동작. DB 테이블명·env legacy 별칭·Pub/Sub 프로토콜 불변. `feature_status`는 신·구 겸용 인식.

## 4. 매수 프롬프트 게이트 — 개별 추세·반복 손절 (#424)

- 매수 프롬프트에 **개별 종목 추세 게이트**(20/50/200일선 정렬 T1/T2)와 **반복 스탑아웃
  게이트**(최근 5거래일 내 매도·유사 손실 패턴 시 추격 재진입 감속) 주입. KR+US, ko+en.

## 5. 배치-루프 교통정리 & 루프 매도 다국어 방송 (#422 #423)

- 배치와 trend_exit(구 loop_b)가 같은 종목을 두고 충돌하지 않도록 신규매수 유예 규칙 추가,
  포트폴리오 최종요약은 충돌 시에도 강제 발송 (#422).
- 루프발 매도 메시지도 배치 매도와 동일하게 **다국어 채널 비동기 브로드캐스트** (#423).

## 6. 문서 · 운영

- **Subscriber 로컬 운영 하네스** (`examples/messaging/SUBSCRIBER_OPS_HARNESS.md`, 직커밋):
  AI 에이전트/사람 겸용 Phase별 셋업 런북. cron 타임존 환산 규칙(시스템 TZ ≠ 셸 TZ 실사고 반영),
  demo→SHADOW→LIVE 승격 게이트, 비밀값 불변식, 흔한 에러 표(APBK0952/EGW00123) 포함.
- `docs/FEATURE_FLAGS.md`에 리네임 용어집 및 신규 플래그 3종 문서화.

---

## 변경 규모 요약 (PR별, 공평 비교)

| PR | 주제 | 규모 |
|---|---|---|
| #425 | Market Pulse 상태머신 + 6y 리플레이 검증기 | 9 files, +1,618 |
| #426 | 구독자 휴식 공지(5개 언어) + Rev 정책 | 3 files, +149 |
| #427 | regime-policy Rev.4 (KR 오후 유지) | 3 files, +17/−12 |
| #428 | loop_a/b/c 리네임 + shim | 24 files, +2,486/−2,360 |
| #429 | 약세장 하드게이트 4종 (US 미러 버그픽스 포함) | 11 files, +616/−24 |
| #430 | 파일럿 재진입 개수 스로틀 재설계 (sim/real parity) | 7 files, +178/−47 |
| #424 | 매수 프롬프트 게이트 (추세·반복손절) | 4 files, +347 |
| #423 | 루프 매도 다국어 브로드캐스트 | 4 files, +44/−6 |
| #422 | 배치-루프 충돌 교통정리 | 4 files, +45/−6 |
| (직커밋) | Subscriber 운영 하네스 런북 | 1 file, +217 |

## 업데이트 방법

```bash
# 운영서버(main 전용): ssh root@<server>; cd /root/prism-insight; git pull --ff-only origin main
# subscriber: ~/work/restart_subscriber.sh  (pull + 프로세스 재기동 + Listening 검증)
# 신규 플래그(.env): REGIME_MIN_SCORE_FLOOR=true, PULSE_PILOT_REEXPOSURE=true
#   (REGIME_WEAK_NO_TOPDOWN, MARKET_PULSE_MODE=live 는 기존 플래그로 자동 활성)
# 구 loop_* 경로 crontab은 shim으로 무수정 동작. 신규 경로 전환은 선택.
```

## 알려진 제한사항

- **REGIME_MIN_SCORE_FLOOR**: LLM 점수는 무보정 주관 척도라 절대 하한(8~9점)이 과차단할 수 있음.
  발동 로그(`[REGIME_MIN_SCORE_FLOOR]`) 모니터링 중이며, 분포 데이터 기반 재보정 예정.
- **파일럿 재진입**: 다음 CORRECTION 종료 시점부터 최초 발동 — 그 전까지는 로그 없음이 정상.
- fill_chaser의 KIS 정정/취소 TR은 실계좌 검증 완료, 모의계좌(VTTS)는 미검증.

## 텔레그램 공지

### 한국어

📢 PRISM-INSIGHT v2.18.0 배포
(Release Note : https://github.com/dragon1086/prism-insight/releases/tag/v2.18.0)

"확실한 기회가 없으면 기다린다" — 오닐의 시장 판독(M)이 코드가 됐습니다.

- 분산일→조정→팔로우스루(FTD) 상태머신이 6년 검증을 거쳐 LIVE로 가동, 조정장엔 분석 배치가 스스로 쉽니다
- 약세·횡보장 신규 매수 하드게이트 4종 (미국장 추격 차단 버그픽스 포함)
- 조정 탈출 직후엔 정찰 1종목만 — 가짜 반등에 당해도 노출 최소
- 매도·손절 루프는 어떤 국면에도 쉬지 않습니다

### English

📢 PRISM-INSIGHT v2.18.0 released
(Release Note : https://github.com/dragon1086/prism-insight/releases/tag/v2.18.0)

"When the market offers no clear opportunity, wait." — O'Neil's market read (M) is now code.

- Distribution-day → Correction → Follow-Through-Day state machine, validated over 6 years of replay, now LIVE — analysis batches rest themselves during corrections
- 4 hard gates against new buying in weak/sideways regimes (incl. a US-side chase-suppression bug fix)
- Right after a correction ends, only one scout position per batch — minimal exposure to false rallies
- Sell/stop-loss loops never rest, in any regime
