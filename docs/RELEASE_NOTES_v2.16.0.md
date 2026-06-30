# PRISM-INSIGHT v2.16.0 — 인사이트 이미지 발행(S6) · 실시간 루프 LIVE · churn 2중방어 · 구독자 실매매 안정화 · 코드품질 B

> **Release Date**: 2026-06-30
> **Range**: `v2.15.0`(73afc671) → `main`(b7263df9) · 105 commits / 44 PRs (#356–#399)

## 개요

v2.16.0은 v2.15.0에서 SHADOW로 출시했던 장치들을 **실거래/실서비스로 승격**하고, 그 과정에서 드러난 안정성·품질·지연 문제를 정리한 릴리즈입니다. 일곱 갈래입니다.

1. **인사이트 이미지 발행 (S6) ⭐ LIVE** — 종목별 분석을 **예측 밴드(시나리오 cone) + 실현 트랙레코드 분포**가 있는 한 장의 이미지로 발행. 통화기호(KR ₩ / US $), 용어 캡션, 매매 마커, 오닐(O'Neil) 차트 패턴을 포함. KR·US 양 시장 LIVE.
2. **실시간 리스크 루프 LIVE 전환·강화 (Loop A/B/C)** — Loop C 미체결 추격을 **LIVE 승격**(체결우선 cross 매수, KR 호가단위 스냅, .env 로딩 버그 수정)하고, Loop A/B 매도를 **Redis+GCP로 발행**해 구독자 미러링과 정합. 포트폴리오 메시지 중복 발송 수정.
3. **손절후 재매수(churn) 2중방어** — **재진입 쿨다운 게이트**(손실 후 24h 동일종목 재매수 차단)와 **매매일지 churn guard**(최근 48h 손절 종목 점수 감점)로, 손절→재매수 과매매를 두 겹으로 차단.
4. **구독자(실매매 미러) 안정화 + 모니터** — 5일 장애(import 실패) 복구 + 시작 자가점검, "살아있는데 0체결" 헬스 모니터 신설 및 오탐 정정.
5. **성능/지연 최적화** — 다국어 PDF 발송을 **단일 브라우저 재사용**으로, KR 배치 매수단계는 **보유종목 LLM 스킵**으로 단축.
6. **모델 마이그레이션** — 기본 경량 모델을 `gpt-5-nano` → `gpt-5.4-nano`로 갱신.
7. **코드품질 Codacy C → B** + 운영 가시성 — 실이슈 ~262건 정리, `/report` 1일 한도 제거 등.

모든 주식 매매 로직 변경은 5인 투자 페르소나(William O'Neil / Mark Minervini / Stanley Druckenmiller / Warren Buffett / Quant Risk Manager) 관점으로 검토해 합의 영역만 채택했습니다.

---

## 1. 인사이트 이미지 발행 (S6) ⭐ LIVE

분석 결과를 텍스트가 아니라 **한 장의 인포그래픽 이미지**로 발행합니다. 단순 차트가 아니라 "이 종목이 앞으로 어떻게 될지(예측)"와 "과거 비슷한 신호가 실제로 어떻게 됐는지(트랙레코드)"를 함께 보여주는 것이 핵심입니다.

- **예측 밴드(시나리오 cone)** (#377) — 코호트 30일 수익 분위수를 √시간으로 펼친 불확실성 부채꼴 + "올랐다/제자리/빠졌다 %" 확률 막대 + 좌측 목표/손절 박스. 공용 엔진 `cores/llm/features/forecast_stats.py`(조건부 base-rate + 분위수).
- **통화기호 정합** (#364) — KR는 ₩, US는 $로 표기.
- **용어 캡션 + 글리프 정리** (#365·#371) — 캡션에 용어 설명, tofu(□) 글리프 제거.
- **매매 마커** (#369) — 이미지에 진입/청산 지점 표시.
- **오닐 차트 패턴 (비전)** (#359·#363) — KR/US 차트 패턴 분석(컵앤핸들 등) 생성, 보고서 기술분석 섹션에 **soft 삽입**(#385, 기본 off — 켜면 매수에이전트가 읽음, 추가 API콜 0).
- **비전 매수품질 S3 (SHADOW)** (#356·#357·#358·#362) — 비전 기반 매수품질 게이트 배관·키소스 격리. 현재 SHADOW(로그 전용, 매매 미반영).

> 비전 호출은 API키/OAuth와 격리(`PRISM_VISION_AUTH`)되어 구독 정책과 충돌하지 않습니다.

---

## 2. 실시간 리스크 루프 LIVE 전환·강화 (Loop A/B/C)

v2.15.0에서 SHADOW로 낸 Loop A/B/C를 검증·강화하고 Loop C를 LIVE로 올렸습니다.

- **Loop C 미체결 추격 LIVE** (#370·#378·#381·#384) — SHADOW 관측 검증(#370) 후 LIVE. **체결우선 cross 매수**(예산 내 마케터블 cross 즉시체결, 초과 시 취소, #378), **KR 호가단위(tick) 스냅**(APBK0506 정렬오류 수정, #384), **`.env` 로딩 버그 수정**(load_dotenv 누락으로 LIVE 켜도 무시되던 것, #381).
- **Loop A/B 매도 Redis+GCP 발행** (#382) — 루프 매도가 배치에만 있던 publish를 안 타 구독자가 미수신·발산하던 것 수정(`loop_publish.py`).
- **포트폴리오 메시지 중복 수정** (#372·#375·#376·#379) — 루프/배치 매도 시 포트폴리오 메시지가 2~3중으로 가던 것 → debounce + per-sell/run-end/수동 append 중복 제거.
- **매수 0원 가드** (#373) — 국내 매수 시 0원 지정가 방어.

---

## 3. 손절후 재매수(churn) 2중방어

손절 직후 같은 종목을 다시 사는 과매매(MU 사례에서 실증)를 두 겹으로 막습니다.

- **재진입 쿨다운 게이트** (#380) — 손실 매도 후 24h 내 동일종목 재매수를 차단(승리 후 0h=미차단). SHADOW 관측 후 LIVE(`REENTRY_COOLDOWN_LIVE`).
- **매매일지 churn guard** (#396) — 최근 48h 내 손절 종목은 점수조정을 `min(adj,0)−2`로 만들어 섹터/동일종목 보너스를 무효화하고 감점(`JOURNAL_RECENT_LOSS_HOURS/PENALTY`, fail-open). 일지의 섹터평균 기반 점수가 오히려 재매수를 부추기던 것을 교정.

> 소프트 advisory(⚠️경고·손절이력 주입)만으로는 LLM이 override해 churn이 반복됨을 실증했고, 그래서 하드 게이트로 대응했습니다.

---

## 4. 구독자(실매매 미러) 안정화 + 모니터

실매매 미러 컨슈머의 5일 장애를 복구하고 재발 감시 장치를 추가했습니다.

- **장애 복구 + 시작 자가점검** (#395) — 매 주문 import 실패의 원인(① 인터프리터 ② 경로 shadowing)을 importlib 로더로 수정, 시작 시 `[STARTUP_SELFCHECK]`로 거래모듈 import 가능 여부를 확인(실패 시 경보+즉시종료).
- **헬스 모니터 신설** (#395) — "프로세스는 살아있는데 0체결/ import 실패"를 5분 주기로 탐지, 별도 알림봇으로 경보.
- **모니터 오탐 정정** (#397·#398) — 미국 거래 로그 포맷 미집계로 인한 가짜 CRITICAL 수정(#397), **정당한 거부**(보유종목 아님·예산초과·현금부족)를 실패에서 분리해 인프라 장애만 경보하도록 정정(#398, 알람 피로 방지).

---

## 5. 성능 / 지연 최적화

- **다국어 PDF 발송 단일 브라우저 재사용 (KR·US)** (#386·#387) — 번역 PDF마다 Chromium을 새로 띄우던 것(4언어×3종목=12 launch)을 **브라우저 1개 재사용**(launch 12→1). 메모리 천장(서버 RAM 제약) 유지하며 순차 렌더.
- **KR 매수단계 보유종목 LLM 스킵** (#399) — 매수 루프가 분석 픽 전 종목에 시나리오 LLM을 돌리던 중, **이미 보유 중이라 추가매수(피라미딩) 불가능한 종목**은 LLM 전에 싼 사전게이트(보유 row수·수익률, DB+현재가만)로 걸러 스킵. 동작 보존(결과·메시지 동일, 낭비 LLM 콜만 제거), 피라미딩(#288)은 유지.

---

## 6. 모델 마이그레이션 (#388)

- 코드 기본 경량 모델을 `gpt-5-nano`(2025-08) → `gpt-5.4-nano`(2026-03)로 교체(14파일). 프록시 모델맵에 `gpt-5.4-nano → gpt-5.4-mini` 추가(Codex가 nano 거부 시 remap). 프로덕션(OAuth)은 어차피 nano→5.4-mini remap이라 영향 0.

---

## 7. 코드품질 (Codacy C → B) · 운영 가시성

- **Codacy 등급 C → B 달성** (#389~#393) — 실이슈 ~262건 정리(bare except → `except Exception`, ruff safe-autofix 191건, E722/B113/E702/E712/E741 등 70건, 진짜 SQL injection 1건 파라미터화) + 테스트 false positive 제외(`.codacy.yaml`). 밀도 8.78% → 2.68%.
- **`/report`·`/us_report` 1일 한도 제거** (#394, 이슈 #307) — 텔레그램 봇 온디맨드 리포트의 1일 횟수 제한 해제.
- **기능 상태 점검 도구** (#360·#366·#367·#368) — `feature_status` 도구 신설 + S6/루프/비전 env 반영 수정.
- **US 분석 실패 처리** (#361) — 재시도 후 실패 시 `[ANALYSIS_FAILED]` 명시.
- **기능 플래그 문서 동기화** (#374).

---

## 변경된 주요 파일

| 파일 | 변경 | PR |
|------|------|-----|
| `cores/insight_image.py` · `cores/llm/features/forecast_stats.py` | 인사이트 이미지: 예측 밴드·트랙레코드 분포·통화·캡션·마커 | #377, #364, #365, #369 |
| `cores/analysis.py` · `cores/buy_quality.py` (비전) | 오닐 차트패턴·비전 매수품질 S3(SHADOW)·보고서 soft 삽입 | #356~#359, #362, #363, #385 |
| `tools/loop_c_fill_chaser.py` · `trading/domestic_stock_trading.py` | Loop C LIVE: 체결우선 cross·KR tick 스냅·.env 로딩 | #370, #378, #381, #384 |
| `tools/loop_publish.py` · `portfolio_broadcast.py` | Loop A/B 매도 Redis+GCP 발행, 포트폴리오 메시지 debounce | #382, #379, #375, #376 |
| `reentry_cooldown.py` · `tracking/journal.py` | 재진입 쿨다운 게이트 + 일지 churn guard | #380, #396 |
| `examples/messaging/gcp_pubsub_subscriber_example.py` · `tools/subscriber_healthcheck.py` | 구독자 importlib/자가점검 + 헬스 모니터·오탐 정정 | #395, #397, #398 |
| `pdf_converter.py` · `prism-us/us_stock_analysis_orchestrator.py` | 다국어 PDF 단일 브라우저 재사용 | #386, #387 |
| `stock_tracking_agent.py` · `tracking/helpers.py` | 보유종목 매수 시나리오 LLM 사전스킵(피라미딩 보존) | #399 |
| 14파일 · `cores/chatgpt_proxy/api_translator.py` | 모델 `gpt-5.4-nano` 마이그레이션 + 프록시 remap | #388 |
| `.codacy.yaml` · 다수 | Codacy 실이슈 정리·테스트 제외 | #389~#393 |
| `telegram_ai_bot.py` | `/report` 1일 한도 제거(#307) | #394 |
| `tools/feature_status.py` (신규) | 기능 상태 점검 | #360, #366~#368 |
| `tests/**` | 인사이트·루프·쿨다운·모니터·피라미딩 회귀 테스트 다수 | 전반 |

---

## 업데이트 방법

```bash
git checkout main
git pull origin main

# 운영서버
ssh root@<server>
cd /root/prism-insight
git pull --ff-only origin main
```

> **기본 동작 불변**: 인사이트 이미지(`PRISM_FEATURE_INSIGHT_IMAGE`), 비전(`PRISM_FEATURE_VISION`), 보고서 삽입(`PRISM_FEATURE_VISION_IN_REPORT`), 루프 LIVE(`FILL_CHASER_LIVE` 등), 쿨다운(`REENTRY_COOLDOWN_LIVE`)은 모두 env 게이트입니다. 미설정 시 종전 동작 유지.
>
> **env 키 리네임**: `LOOP_A_*→HARDSTOP_*`, `LOOP_B_*→TREND_EXIT_*`, `LOOP_C_*→FILL_CHASER_*` (구 키 alias 유효).

---

## 알려진 제한사항

1. **비전 매수품질 S3 = SHADOW**: 비전 `would_buy` 게이트는 로그 전용으로, 실제 매수결정·텔레그램 메시지에 반영되지 않습니다(정보용 품질점수만 이미지 캡션에 표시).
2. **루프/쿨다운 LIVE는 단계 검증 기반**: Loop C는 실 amend/cancel 수락 검증, 쿨다운은 SHADOW 오탐 0 확인 후 전환했습니다. env 게이트로 롤백 가능.
3. **다국어 PDF 단축효과 제한적**: 단일 브라우저 재사용은 정상 동작하나, 실측상 배치 시간은 크게 줄지 않았습니다(병목이 브라우저 launch가 아님). 추가 진단 대상.
4. **#399 매수 LLM 스킵**은 분석 픽이 보유종목과 겹칠 때만 효과가 있습니다(상시 단축 아님).

---

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.16.0 — 인사이트 이미지 + 실시간 루프 LIVE + 과매매 방어

이번 릴리즈는 지난 버전에서 '관측 모드'로 냈던 장치들을 실제로 켜고,
분석 결과를 한 장의 그림으로 보여주는 데 집중했습니다.

🖼️ 1) 인사이트 이미지 발행 (한국·미국)
종목 분석을 한 장의 인포그래픽으로 발행합니다. 단순 차트가 아니라
'앞으로 어떻게 될지(예측 밴드)'와 '과거 비슷한 신호가 실제로 어땠는지
(트랙레코드 분포)'를 함께 보여줍니다. 통화기호(₩/$), 목표·손절 박스,
매매 지점 마커, 차트 패턴까지 한눈에.

🛡️ 2) 실시간 리스크 루프 LIVE 전환
배치 사이 공백을 메우는 Loop A/B/C 중 미체결 추격(Loop C)을 실거래로
올리고(체결우선 즉시체결·호가단위 정렬), 루프 매도를 구독자에게도
정확히 전파하도록 정비했습니다.

🔁 3) 손절후 재매수(과매매) 2중 방어
손실로 판 종목을 곧바로 다시 사는 과매매를, '재진입 쿨다운(24h 차단)'과
'매매일지 감점' 두 겹으로 막습니다.

⚙️ 4) 실매매 미러 안정화 · 성능 · 품질
실매매 미러의 장애를 복구하고 '살아있는데 안 돌아가는' 상황을 감시하는
모니터를 추가했습니다. 다국어 PDF 발송과 매수 분석 단계를 가볍게 다듬고,
코드 품질 등급도 C→B로 올렸습니다(기본 경량 모델도 최신으로 교체).

📊 모든 주식 매매 로직은 5인 투자 거장(오닐·미너비니·드러켄밀러·버핏·퀀트)
관점으로 검토해 합의된 부분만 반영했습니다.
```

### English

```
🚀 PRISM-INSIGHT v2.16.0 — Insight Images + Real-time Loops go LIVE + Churn Guards

This release flips on the defenses we shipped in observe-only mode last
version, and shows each analysis as a single, readable image.

🖼️ 1) Insight image publishing (KR & US)
Each stock analysis is published as one infographic — not just a chart, but
"what may happen next" (a forecast band) alongside "how similar past signals
actually played out" (a track-record distribution). Currency symbols (₩/$),
target/stop boxes, trade markers, and chart patterns at a glance.

🛡️ 2) Real-time risk loops go LIVE
Among Loop A/B/C that fill the gaps between batches, the unfilled-order
chaser (Loop C) is now live (marketable-cross fills, tick-size alignment),
and loop sells now propagate correctly to subscribers.

🔁 3) Double guard against re-buy churn
Buying back a name right after stopping out is now blocked twice: a
re-entry cooldown (24h after a loss) plus a trading-journal score penalty.

⚙️ 4) Live-mirror stability · performance · quality
We recovered the real-trading mirror from an outage and added a monitor for
"alive but not executing" states. Multilingual PDF delivery and the buy
analysis step were trimmed, and code quality moved from grade C to B
(default lightweight model refreshed too).

📊 All stock trading logic was reviewed through 5 investing masters
(O'Neil · Minervini · Druckenmiller · Buffett · Quant) — only consensus adopted.
```

---

**Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>**
