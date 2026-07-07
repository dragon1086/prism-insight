# PRISM-INSIGHT v2.17.0 — 급락장 regime 방어 · 약세장 매수 절제 · 손절 실행 신뢰성 · 봇 차트비전 · Sonnet 5 · 보안 강화

> **Release Date**: 2026-07-07
> **Range**: `v2.16.0`(0d5169f0) → `main`(0cc2212d) · 49 commits / 19 PRs (#400–#419)

## 개요

v2.17.0은 **"급락·고변동장에서 계좌를 지키는" 방어 로직**에 집중한 릴리즈입니다. 시장 국면을 잘못 읽어 무분별하게 매수하고, 손절이 제때 안 나가고, 손절 직후 같은 종목을 다시 사는 — 실운영에서 계좌를 녹이던 문제들을 실데이터로 확증하고 단계적으로 막았습니다. 여기에 텔레그램 봇의 차트 이미지 분석, Sonnet 5 업그레이드, 저장소 보안 강화가 더해졌습니다.

1. **시장 국면(regime) 오판 방지 + 약세장 매수 절제 ⭐** — 급락·고변동장을 이동평균 위치만으로 "온건강세"로 오판하던 것을 실현변동성·낙폭 기반으로 교정(override), 약세·횡보장의 모멘텀추격 매수 억제.
2. **손절 실행 & churn 방어 신뢰성 ⭐** — SHADOW 레코드가 손절을 3주간 영구 차단하던 버그, 매도 chokepoint 중복 SELL 방지, 재진입 쿨다운이 실제 매수경로에서 우회되던 버그 수정.
3. **텔레그램 봇 — 차트 이미지 비전 분석** — 첨부 차트를 비전 LLM으로 분석 + 사진 답장 컨텍스트.
4. **모델 업그레이드** — 리포트 생성 Sonnet 4.6 → **Sonnet 5**.
5. **KIS/데이터 견고성** — KIS 잔고조회 재시도(EGW00215), KRX 지수명 조회 지연 중 스크리닝 유지.
6. **보안 & 프라이버시** — 노출 내부파일 제거 + 코드 취약점 수정, 스킵 사유의 마스킹 계좌번호 공개 노출 제거.
7. **운영 편의** — 구독자 모니터 오탐 정정, `.env.example` 신규 플래그 동기화.

> 매매 로직 변경은 모두 **기본 무영향(env-gated / SHADOW)** 으로 들어와, 실데이터 관찰·검증 후 켜는 방식을 지켰습니다. 롤백은 env 플래그 한 줄.

---

## 1. 시장 국면 오판 방지 + 약세장 매수 절제 ⭐

실운영 `regime_history.jsonl` 실증: 2026-07-02~03 KOSPI가 2주간 **−12~−16%** 폭락 중인데도 regime이 계속 `moderate_bull`(온건강세)로 찍혔습니다 — 분류기가 가격의 60/120일선 위치만 보고 변동성·낙폭을 무시했기 때문. 그 관대한 판정 아래 저품질·모멘텀 종목을 고점 매수→즉시 손절하는 churn이 7월 실현손실 −42%p(승률 1/9)의 주범이었습니다.

- **고변동·낙폭 regime override (#414)** — `_compute_kr_regime`/`_compute_us_regime`에 3중 조건(최근 10일 실현변동성 ≥ 2.5% AND 20일 고점 대비 낙폭 ≥ 8% AND 최근 10일 순변화 ≤ −3%)을 추가해, 장기이평 위에 떠 있어도 **급락형 휩쏘면 `sideways`로 강등**. 순변화 조건이 급등형(melt-up)·횡보 고변동을 배제해 오강등을 막습니다. env `REGIME_HIVOL_OVERRIDE` = `shadow`(기본)/`active`/`off`, `regime_history.jsonl`에 발동사유 로깅. 6년 백테스트상 전 기간 영향 −1.3%p로 보수적이되 2026년 급락기엔 bull 98%→85%로 정확히 발동, 강세년·melt-up 무영향.
- **약세·횡보장 top-down 매수 억제 (#417)** — `_get_regime_slots`가 sideways/moderate_bear에도 강세장과 동일하게 top-down(모멘텀추격) 슬롯을 허용하던 것을, env `REGIME_WEAK_NO_TOPDOWN`(기본 off) ON 시 **0으로** 낮춰 급락장 momentum chase를 접습니다(가치형 bottom-up만). 강세장 무영향.

---

## 2. 손절 실행 & churn 방어 신뢰성 ⭐

- **loop_a 하드스탑 SHADOW inflight → 손절 영구 차단 버그 + TTL (#416)** — `has_open_inflight`가 `status IN ('OPEN','SHADOW')`를 시간필터 없이 조회 → loop_a가 SHADOW 모드였을 때(2026-06-18) 만든 inflight 레코드가 **3주간 해당 종목 손절을 영구 스킵**. 제주반도체(080220) 13주가 손절가 이하인데도 매도 안 됨. 수정: `status='OPEN'` + `submitted_ts >= now-INFLIGHT_TTL_SEC`(기본 900s). SHADOW 레코드는 실주문이 아니라 LIVE 매도를 막지 않고, stale OPEN도 TTL 후 제외.
- **매도 중앙 chokepoint 가드 (#402)** — 모든 매도 경로가 `sell_stock` 단일 지점을 지나게 해 KR+US 전 사이클의 중복 SELL/발산을 차단.
- **US 배치매도 stale-snapshot 이중발행 가드 (#401)** — 오래된 스냅샷으로 인한 SELL 이중 publish 방지(Layer 2).
- **exit_kind 인지 재진입 쿨다운 (#403)** — 청산 유형(stop/trend/target)을 구분해 쿨다운 판정, SHADOW-first.
- **재진입 쿨다운 우회 버그 수정 (#418)** — 쿨다운 게이트가 base `StockTrackingAgent`에만 있고 KR 오케스트레이터가 실제 쓰는 `EnhancedStockTrackingAgent`의 오버라이드된 매수 루프엔 누락 → `REENTRY_COOLDOWN_LIVE=true`인데도 손실 후 24h내 재매수가 그대로 통과(삼성전기 07-06 손절 → 07-07 재매수). base와 동일 게이트를 enhanced 루프에 연결.

---

## 3. 텔레그램 봇 — 차트 이미지 비전 분석

- **첨부 차트 이미지 비전 분석 (#407)** — 사용자가 보낸 차트 이미지를 비전 LLM으로 분석.
- **사진 답장 컨텍스트 인지 (#408) / reply 전용화 (#409) / 전체 대화 맥락·톤 상속 (#410)** — 사진에 대한 답장이 대화 맥락과 톤을 이어가도록 정비.

---

## 4. 모델 업그레이드 (#404)

- 리포트 생성기 + 봇 문서 모델을 **Sonnet 4.6 → Sonnet 5**로 상향.

---

## 5. KIS / 데이터 견고성

- **KIS 잔고조회 재시도 (#406)** — 일시적 오류(EGW00215)에 잔고조회 재시도.
- **KRX 지수명 조회 지연 중 스크리닝 유지 (#411)** — KRX name lookup stall이 KR 트리거 스크리닝을 죽이지 않도록.
- **구독자 모니터 오탐 정정 (#400)** — 'Order window unavailable'을 정당한 거부(benign)로 분류해 가짜 경보 방지.

---

## 6. 보안 & 프라이버시

- **저장소 노출 강화 (#405)** — 노출된 내부 파일 제거 + 코드 레벨 취약점 수정.
- **스킵 사유의 마스킹 계좌번호 공개 노출 제거 (#415)** — 'Max slots reached for {label}'의 `{label}`(로그 전용 함수)이 브로드캐스트 스킵 사유로 나가며 마스킹된 계좌번호(`prod:63****46:01`)가 공개 채널(en/ja/zh/es)에 노출되던 것 → 사용자 메시지는 일반화, 계좌 라벨은 로그에만.

---

## 7. BTC · 문서/설정

- **BTC 라운드5 게이트 교차셀 검증 (#413)** — 룰 유지 + 최접근 리포팅.
- **`.env.example` 신규 플래그 동기화 (#419)** — `REGIME_HIVOL_OVERRIDE`, `REGIME_WEAK_NO_TOPDOWN`, `INFLIGHT_TTL_SEC`, `REENTRY_COOLDOWN_RISK_EXIT_LIVE`를 기본값·설명과 함께 추가.

---

## 변경된 주요 파일

| 파일 | 변경 | PR |
|------|------|-----|
| `cores/data_prefetch.py` · `prism-us/cores/data_prefetch.py` | 고변동·낙폭 regime override(shadow/active/off) + 로깅 | #414 |
| `trigger_batch.py` | 약세장 top-down 억제 옵션(`REGIME_WEAK_NO_TOPDOWN`) | #417 |
| `tools/loop_a_hardstop.py` | inflight TTL + SHADOW 미차단(손절 영구차단 버그) | #416 |
| `stock_tracking_agent.py` · `prism-us/us_stock_tracking_agent.py` | 매도 chokepoint 가드 · stale-snapshot 가드 · exit_kind 쿨다운 | #401, #402, #403 |
| `stock_tracking_enhanced_agent.py` | 재진입 쿨다운 우회 버그 수정 | #418 |
| `telegram_ai_bot.py` | 차트 이미지 비전 분석 + 사진 답장 컨텍스트 | #407, #408, #409, #410 |
| `report_generator.py` 등 | Sonnet 5 업그레이드 | #404 |
| `trading/domestic_stock_trading.py` · `krx_data_client` 경로 | KIS 잔고조회 재시도 · KRX 지수명 조회 회복탄력성 | #406, #411 |
| `tools/subscriber_healthcheck.py` | 모니터 benign 분류 | #400 |
| (repo 전반) | 노출 파일 제거 · 코드 취약점 · 계좌번호 마스킹 | #405, #415 |
| `prism-btc/**` | 라운드5 게이트 교차셀 검증 | #413 |
| `.env.example` · `tests/**` | 신규 플래그 동기화 + regime/loop_a/restraint 회귀 테스트 | #419, #414, #416, #417 |

---

## 업데이트 방법

```bash
git checkout main
git pull origin main

# 운영서버 (main 전용 운영)
ssh root@<server>
cd /root/prism-insight
git pull --ff-only origin main
```

> **기본 동작 불변(전부 env 게이트)**: `REGIME_HIVOL_OVERRIDE`(미설정=shadow), `REGIME_WEAK_NO_TOPDOWN`(미설정=off), `INFLIGHT_TTL_SEC`(기본 900), `REENTRY_COOLDOWN_LIVE`/`REENTRY_COOLDOWN_RISK_EXIT_LIVE`는 미설정 시 종전 동작 유지. 신규 플래그 설명은 `.env.example` 참고.

---

## 알려진 제한사항

1. **regime override 임계값(2.5% / 8% / −3%)은 튜닝 여지**: ~80건 노이즈 데이터로 micro-restraint 정밀 backtest가 어려워, env-gated로 배포 후 forward 관찰로 검증하는 방식. `tools/regime_backtest.py`로 재검증 권장.
2. **약세장 top-down 억제/쿨다운은 forward 관찰 대상**: 과도하게 조이면(매수 0) 회복장 진입을 놓칠 수 있음 — 로그(`[REGIME_SLOTS]`, `[REENTRY_COOLDOWN][LIVE]`)로 발동 빈도 관찰하며 조정.
3. **로컬 원장 ↔ 실계좌 divergence**: loop_a 매도 신호와 구독자 실계좌 상태가 어긋나는 케이스 존재(예: 'not in portfolio' benign). 원장/브로커 완전 분리는 별도 이식 설계(이슈 #412) 범위.
4. **buy_quality 비전 게이트는 여전히 SHADOW**: 매수결정에 미반영(정보 표시).

---

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.17.0 — 급락장 방어 · 약세장 매수 절제 · 손절 신뢰성

이번 릴리즈는 '급락·고변동장에서 계좌를 지키는' 방어에 집중했습니다.

📉 1) 시장 국면 오판 방지 + 약세장 매수 절제
2주에 −16% 빠지는 장을 '온건강세'로 잘못 읽고 무분별하게 사던 문제를,
실현변동성·낙폭 기준으로 교정했습니다. 약세·횡보장에선 모멘텀 추격 매수를
자동으로 접습니다. (전부 관찰 모드로 안전하게 도입)

🛡️ 2) 손절 실행 & 과매매 방어 신뢰성
손절이 며칠간 안 나가던 버그(오래된 내부 레코드가 매도를 막던 것),
같은 종목 중복 매도, 손절 직후 재매수를 막는 쿨다운이 실제로는
우회되던 버그를 잡았습니다.

🤖 3) 텔레그램 봇 차트 이미지 분석
봇에게 차트 이미지를 보내면 비전 AI가 분석합니다. 사진 답장도
대화 맥락을 이어갑니다.

⚙️ 4) 모델·데이터·보안
리포트 생성 모델을 Sonnet 5로 올리고, KIS 잔고조회 재시도·KRX 조회
회복탄력성을 강화했습니다. 노출 파일 제거와 계좌번호 노출 차단 등
보안도 정비했습니다.

📊 매매 로직 변경은 모두 기본 무영향(관찰 모드)으로 도입해, 실데이터로
검증한 뒤 켭니다. 롤백은 설정 한 줄.
```

### English

```
🚀 PRISM-INSIGHT v2.17.0 — Crash-regime defense · buy restraint · reliable stops

This release focuses on protecting the account in sharp, high-volatility markets.

📉 1) Fix market-regime misreads + restrain buying in weak markets
A market down −16% in two weeks was still read as "moderate bull," driving
reckless buys. We now correct it using realized volatility and drawdown, and
suppress momentum-chasing buys in weak/choppy regimes. (All shipped in
observe-only mode.)

🛡️ 2) Reliable stop execution & churn guards
Fixed a bug where stop-losses were blocked for days (a stale internal record
vetoing the sell), duplicate sells, and a re-entry cooldown that was being
bypassed on the actual buy path.

🤖 3) Bot chart-image analysis
Send the bot a chart image and a vision model analyzes it; photo replies now
carry the conversation context.

⚙️ 4) Model · data · security
Report generation moved to Sonnet 5, with sturdier KIS balance-inquiry retries
and KRX lookup resilience. Security was hardened (removed exposed files, and
stopped a masked account number from leaking to public channels).

📊 All trading-logic changes ship inert (observe-only) and are validated on
real data before being turned on. Rollback is a one-line flag.
```

---

**Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>**
