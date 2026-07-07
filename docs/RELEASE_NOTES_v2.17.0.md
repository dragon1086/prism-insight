# PRISM-INSIGHT v2.17.0 — 손절 실행·과매매 방어 · 급락장 regime 방어 · 봇 차트비전 · 보안 강화 · Sonnet 5

> **Release Date**: 2026-07-07
> **Range**: `v2.16.0`(0d5169f0) → `main`(869e7b75) · 49 commits / 19 PRs (#400–#419)

## 개요

v2.17.0은 **실운영에서 계좌를 녹이던 매매 안전 문제**를 실데이터로 확증해 정리한 릴리즈입니다. 손절이 제때 안 나가고, 손절 직후 같은 종목을 다시 사고, 같은 매도가 중복 발행되고, 급락장을 강세로 오판해 무분별하게 사던 문제들을 잡았습니다. 여기에 텔레그램 봇 차트 이미지 분석, 저장소 보안 강화, Sonnet 5 업그레이드가 더해졌습니다. 아래 **중요도순**으로 정리합니다.

1. **손절 실행 & 과매매(churn)·중복매도 방어 ⭐** — 청산유형 인지 재진입 쿨다운, 매도 단일 chokepoint, stale-snapshot 이중발행 가드, 손절 영구차단 버그, 쿨다운 우회 버그.
2. **시장 국면(regime) 오판 방지 & 약세장 매수 절제 ⭐** — 급락·고변동장을 온건강세로 오판하던 것 교정 + 약세장 모멘텀추격 매수 억제.
3. **텔레그램 봇 — 차트 이미지 비전 분석** — 첨부 차트 분석 + 사진 답장 컨텍스트.
4. **보안 & 프라이버시** — 코드 인젝션 수정 + 노출 내부파일 제거, 계좌번호 공개노출 차단.
5. **모델 업그레이드** — Sonnet 4.6 → **Sonnet 5**.
6. **데이터·운영 견고성** — KRX 지수명 조회 회복탄력성, KIS 잔고조회 재시도, 구독자 모니터 오탐 정정.
7. **BTC · 문서** — 라운드5 게이트 교차셀 검증, `.env.example` 동기화.

> 매매 로직 변경은 대부분 **기본 무영향(env-gated / SHADOW-first)** 으로 들어와 실데이터 검증 후 켜는 방식입니다. 롤백은 env 플래그 한 줄.

---

## 1. 손절 실행 & 과매매·중복매도 방어 ⭐ (이번 릴리즈 최대 비중)

배치 사이 실시간 루프와 매도 경로 전반에서, 손절이 실제로 나가고·중복되지 않고·손절 직후 재매수하지 않도록 여러 겹으로 보강했습니다.

- **청산유형(exit_kind) 인지 재진입 쿨다운 (#403)** — 재진입 쿨다운을 stop/trend/target 등 **청산 유형별로** 판정하도록 대폭 확장(KR+US, SHADOW-first). `reentry_cooldown.py`(+143/−) 재작성, 매매일지·`db_schema`에 exit_kind 반영, loop_a/loop_b 연동 — **churn 방어의 본체**(12파일·+392). 정적 리터럴 SQL로 리팩터해 SQL 빌드 제거 + Codacy B608 false positive 정리.
- **loop_a 하드스탑 SHADOW inflight → 손절 영구 차단 버그 + TTL (#416)** — `has_open_inflight`가 `status IN ('OPEN','SHADOW')`를 시간필터 없이 조회 → loop_a가 SHADOW였던 2026-06-18에 만든 inflight 레코드가 **3주간 해당 종목 손절을 영구 스킵**. 제주반도체(080220) 13주가 손절가 이하인데도 매도 안 됨(실증). 수정: `status='OPEN'` + `submitted_ts >= now-INFLIGHT_TTL_SEC`(기본 900s). SHADOW 레코드는 실주문이 아니라 LIVE 매도를 막지 않음.
- **매도 중앙 chokepoint 가드 (#402)** — 모든 매도 경로가 `sell_stock` 단일 지점을 지나게 해(KR+US) 전 사이클의 중복 SELL/발산 차단.
- **US 배치매도 stale-snapshot 이중발행 가드 (#401)** — 오래된 스냅샷으로 인한 SELL 이중 publish 방지(Layer 2, 전용 테스트 200줄).
- **재진입 쿨다운 우회 버그 수정 (#418)** — 위 쿨다운 게이트가 base `StockTrackingAgent`에만 있고, KR 오케스트레이터가 실제 쓰는 `EnhancedStockTrackingAgent`의 오버라이드 매수 루프엔 누락 → `REENTRY_COOLDOWN_LIVE=true`인데도 손실 후 24h내 재매수가 그대로 통과(삼성전기 07-06 손절 → 07-07 재매수 실증). base와 동일 게이트를 enhanced 루프에 연결.

---

## 2. 시장 국면 오판 방지 & 약세장 매수 절제 ⭐

실운영 `regime_history.jsonl` 실증: 2026-07-02~03 KOSPI가 2주간 **−12~−16%** 폭락 중인데도 regime이 `moderate_bull`로 찍힘 — 분류기가 60/120일선 위치만 보고 변동성·낙폭을 무시. 그 관대한 판정이 7월 실현손실 −42%p(승률 1/9)의 배경.

- **고변동·낙폭 regime override (#414)** — `_compute_kr_regime`/`_compute_us_regime`에 3중 조건(최근 10일 실현변동성 ≥ 2.5% AND 20일 고점 낙폭 ≥ 8% AND 최근 10일 순변화 ≤ −3%)을 추가해 장기이평 위여도 **급락형 휩쏘면 `sideways`로 강등**. 순변화 조건이 melt-up·횡보 고변동을 배제. env `REGIME_HIVOL_OVERRIDE`=shadow(기본)/active/off. 6년 백테스트상 전기간 −1.3%p로 보수적이되 2026 급락기엔 bull 98%→85% 발동, 강세년·melt-up 무영향(+491, KR/US 대칭).
- **약세·횡보장 top-down 매수 억제 (#417)** — `_get_regime_slots`가 sideways/moderate_bear에도 강세장과 동일 top-down(모멘텀추격) 슬롯을 허용하던 것을, env `REGIME_WEAK_NO_TOPDOWN`(기본 off) ON 시 **0으로** 낮춰 급락장 momentum chase를 접음. 강세장 무영향.

---

## 3. 텔레그램 봇 — 차트 이미지 비전 분석

봇에 사진(차트)을 보내면 비전 LLM으로 분석하고, 사진 답장이 대화 맥락을 잇는 기능군(`telegram_ai_bot.py` ~250줄).

- **첨부 차트 이미지 비전 분석 (#407)** — 사용자가 보낸 차트를 비전 LLM으로 해석.
- **사진 답장 컨텍스트 인지 (#408)** — 사진에 대한 답장이 대화 맥락 반영.
- **차트 분석 reply 전용화 (#409)** — 이미지 분석을 답장 기반으로 정리.
- **전체 대화 맥락·톤 상속 (#410)** — 사진 답장이 전체 대화의 맥락과 톤을 그대로 이어감.

---

## 4. 보안 & 프라이버시

- **저장소 노출 강화 (#405)** — **리포트 subprocess 실행의 코드 인젝션 수정**, archive API 인증·YAML 로딩·시크릿 처리 강화(`trading/kis_auth.py`, `report_generator.py`), 그리고 **공개 repo에 노출돼 있던 내부 문서(`tasks/**`, `.idea/`) 대량 제거**(~1,900줄 삭제·30파일).
- **스킵 사유의 마스킹 계좌번호 공개노출 제거 (#415)** — 'Max slots reached for {label}'의 `{label}`(로그 전용 함수)이 브로드캐스트로 나가며 마스킹 계좌번호(`prod:63****46:01`)가 공개 채널에 노출되던 것 → 사용자 메시지 일반화, 계좌 라벨은 로그에만.

---

## 5. 모델 업그레이드 (#404)

- 리포트 생성기 + 봇 LLM 호출 모델을 **Sonnet 4.6 → Sonnet 5**로 상향.

---

## 6. 데이터 · 운영 견고성

- **KRX 지수명 조회 회복탄력성 (#411)** — KRX name lookup stall이 KR 트리거 스크리닝을 죽이던 것 방지(반복 stall 차단 + 스크리닝 유지, 테스트 106줄).
- **KIS 잔고조회 재시도 (#406)** — 일시적 오류(EGW00215)에 잔고조회 재시도.
- **구독자 모니터 오탐 정정 (#400)** — 'Order window unavailable'을 정당한 거부(benign)로 분류해 가짜 경보 방지.

---

## 7. BTC · 문서/설정

- **BTC 라운드5 게이트 교차셀 검증 (#413)** — 게이트 룰 유지 + 근접(near-miss) 리포팅 추가(`prism-btc`, +337·테스트 포함).
- **`.env.example` 신규 플래그 동기화 (#419)** — `REGIME_HIVOL_OVERRIDE`, `REGIME_WEAK_NO_TOPDOWN`, `INFLIGHT_TTL_SEC`, `REENTRY_COOLDOWN_RISK_EXIT_LIVE`를 기본값·설명과 함께 추가.

---

## 변경 규모 요약 (PR별, 공평 비교)

| 비중 | PR | 요지 | 규모(대략) |
|---|---|---|---|
| ⭐ | #403 | exit_kind 인지 재진입 쿨다운 (KR+US) | 12파일 +392/−71 |
| ⭐ | #414 | 고변동·낙폭 regime override (KR/US) | 5파일 +491 |
| ⭐ | #401 | US stale-snapshot 이중발행 가드 | +225 |
| 中 | #413 | BTC 라운드5 게이트 교차셀 검증 | +337 |
| 中 | #407~410 | 봇 차트 이미지 비전 + 사진 컨텍스트 | ~250 |
| 中 | #411 | KRX 지수명 조회 회복탄력성 | +140 |
| 中 | #405 | 보안(코드인젝션+노출파일 제거) | +67/−1914 |
| 中 | #402 | 매도 chokepoint 가드 | +76 |
| 中 | #416 | loop_a 손절 영구차단 버그 + TTL | +65 |
| 中 | #417 | 약세장 top-down 억제 옵션 | +54 |
| 小 | #406 | KIS 잔고조회 재시도 | +31 |
| 小 | #404 | Sonnet 5 업그레이드 | 2파일 |
| 小 | #418 | 쿨다운 우회 버그 수정 | +21 |
| 小 | #400 | 모니터 benign 정정 | +19 |
| 小 | #419 | .env.example 동기화 | +13 |
| 小 | #415 | 계좌번호 노출 제거 | +8 |

---

## 업데이트 방법

```bash
git checkout main && git pull origin main
# 운영서버(main 전용): ssh root@<server>; cd /root/prism-insight; git pull --ff-only origin main
```

> **기본 동작 불변(env 게이트)**: `REGIME_HIVOL_OVERRIDE`(미설정=shadow)·`REGIME_WEAK_NO_TOPDOWN`(off)·`INFLIGHT_TTL_SEC`(900)·`REENTRY_COOLDOWN_LIVE`/`_RISK_EXIT_LIVE`는 미설정 시 종전 동작. 설명은 `.env.example` 참고.

---

## 알려진 제한사항

1. **regime override 임계값(2.5%/8%/−3%)·약세장 절제는 튜닝/관찰 대상** — ~80건 노이즈로 정밀 backtest 어려워 env-gated 후 forward 관찰로 검증(`[REGIME_SLOTS]`·`[REENTRY_COOLDOWN][LIVE]` 로그로 발동 관찰). 과도하게 조이면 회복장 진입을 놓칠 수 있음.
2. **로컬 원장 ↔ 실계좌 divergence** — 매도 신호와 구독자 실계좌 상태가 어긋나는 케이스 존재. 원장/브로커 완전 분리는 이식 설계(이슈 #412) 범위.
3. **buy_quality 비전 게이트는 여전히 SHADOW** — 매수결정 미반영(정보 표시).

---

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.17.0 — 손절 실행·과매매 방어 + 급락장 방어

이번 릴리즈는 '급락·고변동장에서 계좌를 지키는' 매매 안전에 집중했습니다.

🛡️ 1) 손절 실행 & 과매매 방어 (최대 비중)
· 청산유형을 구분하는 재진입 쿨다운을 대폭 강화(손절 직후 재매수 차단)
· 같은 매도가 중복 발행되던 것 차단(매도 단일 chokepoint)
· 손절이 며칠간 안 나가던 버그(오래된 내부 레코드가 매도를 막던 것) 수정
· 쿨다운이 실제 매수경로에서 우회되던 버그 수정

📉 2) 시장 국면 오판 방지 + 약세장 매수 절제
2주 −16% 빠지는 장을 '온건강세'로 읽던 것을 실현변동성·낙폭으로 교정.
약세·횡보장에선 모멘텀 추격 매수를 자동으로 접습니다. (관찰 모드로 안전 도입)

🤖 3) 텔레그램 봇 차트 이미지 분석
봇에게 차트 사진을 보내면 비전 AI가 분석하고, 사진 답장이 대화 맥락을 잇습니다.

🔒 4) 보안 · 모델 · 데이터
리포트 실행의 코드 인젝션을 막고 노출된 내부 파일을 제거했습니다. 계좌번호
공개노출도 차단. 리포트 모델은 Sonnet 5로, KRX/KIS 조회 안정성도 강화했습니다.

📊 매매 로직은 대부분 기본 무영향(관찰 모드)으로 도입해 실데이터로 검증 후 켭니다.
```

### English

```
🚀 PRISM-INSIGHT v2.17.0 — Reliable stops · churn defense · crash-regime guard

This release focuses on trading safety in sharp, high-volatility markets.

🛡️ 1) Stop execution & churn defense (biggest theme)
· Greatly expanded exit-kind-aware re-entry cooldown (block re-buys after a stop)
· Blocked duplicate sells (single sell chokepoint)
· Fixed a bug where stops were vetoed for days by a stale internal record
· Fixed a cooldown that was being bypassed on the actual buy path

📉 2) Fix regime misreads + restrain buying in weak markets
A market down −16% in two weeks was read as "moderate bull"; now corrected via
realized volatility and drawdown. Momentum-chasing buys are suppressed in weak
regimes. (Shipped in observe-only mode.)

🤖 3) Bot chart-image analysis
Send the bot a chart image and a vision model analyzes it; photo replies now
carry the full conversation context.

🔒 4) Security · model · data
Fixed a code-injection path in report execution and removed exposed internal
files; stopped a masked account number from leaking publicly. Report model moved
to Sonnet 5, with sturdier KRX/KIS lookups.

📊 Most trading-logic changes ship inert (observe-only) and are validated on
real data before being turned on.
```

---

**Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>**
