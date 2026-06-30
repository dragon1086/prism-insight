# PRISM-INSIGHT v2.16.0 — 인사이트 이미지 발행(Phase 6 / S6) · 실시간 루프 LIVE · churn 2중방어 · 구독자 실매매 안정화 · 코드품질 B

> **Release Date**: 2026-06-30
> **Range**: `v2.15.0`(73afc671) → `main`(b7263df9) · 105 commits / 44 PRs (#356–#399)

## 개요

v2.16.0은 v2.15.0에서 SHADOW/OFF로 출시했던 장치들을 **실서비스/실거래로 승격**하고, 그 과정에서 드러난 안정성·지연·품질 문제를 정리한 대형 릴리즈입니다. 여덟 갈래입니다.

1. **인사이트 이미지 발행 — Phase 6 (S1→S6) ⭐ LIVE** — 비전 배관(S1)부터 렌더 QA(S2), 오닐 매수품질 게이트(S3, SHADOW), 오닐 차트+RS선(S3.5), 그리고 **예측 밴드 + 실현 트랙레코드 분포가 있는 인사이트 이미지 발행(S6)**까지의 단계적 프로그램. KR·US 양 시장 LIVE.
2. **실시간 리스크 루프 LIVE 전환·강화 (Loop A/B/C)** — Loop C 미체결 추격 LIVE 승격(체결우선 cross·KR 호가단위 스냅·.env 로딩 수정), Loop A/B 매도 Redis+GCP 발행, 포트폴리오 메시지 중복 정리, env 키 리네임.
3. **손절후 재매수(churn) 2중방어** — 재진입 쿨다운 게이트 + 매매일지 churn guard.
4. **구독자(실매매 미러) 안정화 + 모니터** — 5일 장애 복구·시작 자가점검·헬스 모니터·오탐 정정.
5. **성능/지연 최적화** — 다국어 PDF 단일 브라우저 재사용, KR 매수단계 보유종목 LLM 스킵.
6. **모델 마이그레이션** — `gpt-5-nano` → `gpt-5.4-nano`.
7. **코드품질 Codacy C → B** — 실이슈 ~262건 정리 + 테스트 제외.
8. **운영 가시성·견고성** — 기능 플래그 레지스트리/상태도구, US 분석 실패 처리, 매수 0원 가드 등.

모든 주식 매매 로직 변경은 5인 투자 페르소나(William O'Neil / Mark Minervini / Stanley Druckenmiller / Warren Buffett / Quant Risk Manager) 관점으로 검토해 합의 영역만 채택했습니다.

---

## 1. 인사이트 이미지 발행 — Phase 6 비전 프로그램 (S1→S6) ⭐

분석 결과를 텍스트가 아니라 **한 장의 인포그래픽**으로 발행하기까지, 비전 기능을 단계(Stage)로 쌓아 올렸습니다. 각 단계는 기본 OFF/SHADOW로 안전하게 들어온 뒤 검증을 거쳐 켜졌습니다.

### 1-1. 비전 배관 · 렌더 QA · 매수품질 게이트 (S1~S3) (#356·#357)
- **S1 — 비전 배관 + 능력 탐지** (#356): 비전 호출 인프라와 capability detection. 기본 OFF.
- **S2 — 렌더 QA**: 이미지 렌더 품질 점검 단계(기본 OFF, 비차단).
- **S3 — 오닐 매수품질 비전 게이트 (SHADOW)** (#357): 차트 이미지를 보고 매수품질을 평가하는 게이트. **로그 전용(SHADOW)** — 매매 미반영.

### 1-2. 비전 키소스 격리 · 오닐 차트 + RS선 (S3.5) (#358·#359)
- **비전 API 키 격리** (#358): 비전 키를 `mcp_agent.secrets.yaml`에서 해석, 기본 모델 `gpt-5.4-mini` — API키/OAuth 정책과 충돌 방지.
- **오닐 차트 (일봉+주봉) + RS선 + 멀티이미지 비전** (#359): 일봉·주봉 오닐 차트에 상대강도(RS) 라인을 얹고 여러 이미지를 비전에 투입. 주봉 fetch를 KRX 730일 한도로 클램프, RS선은 `krx_data_client` 인덱스 fetch로 정직하게 산출.

### 1-3. 인사이트 이미지 (S6) — 예측 밴드 + 트랙레코드 ⭐ (#362·#363·#364·#365·#369·#371·#377)
- **예측 밴드(시나리오 cone) + Prism 트랙레코드 확률 패널** (#377): 코호트 30일 수익 분위수를 √시간으로 펼친 **불확실성 부채꼴(곡선)** + "올랐다/제자리/빠졌다 %" 평이한 확률 막대. 밴드가 차트를 가리지 않도록 목표/손절 콜아웃을 우상단 플랜박스로 이동, 베이스박스는 은은하게(점선·연한 채움) 디엠퍼사이즈. 공용 엔진 `cores/llm/features/forecast_stats.py`(조건부 base-rate + 분위수) 신설 + 단위테스트.
- **US 인사이트 이미지 활성화** (#363): US 오닐 인사이트 차트 추가로 US도 이미지 발행.
- **통화기호 정합** (#364): US $, KR ₩.
- **평이한 용어 캡션(글로서리)** (#365) + **폰트 안전 글리프** (#371): 캡션 용어 설명, tofu(□) 유발 글리프(U+2212 −, U+25B8 ▸, ℹ️)를 폰트 안전 문자로 교체(NanumGothicCoding).
- **과거 매매 마커** (#369): 인사이트 이미지에 과거 진입/청산 지점 표시 + LLM 컨텍스트 주입.
- **레이아웃 폴리시** (#362): 한국어 캡션 밴드·여백·플롯 내 라벨 정리(표시 전용, OFF 게이트).

### 1-4. 비전을 보고서에 soft 삽입 + 매수품질 SHADOW 로깅 복구 (#377·#385)
- **매수품질 SHADOW 로깅 복구** (#377): `cores/analysis.py` 훅이 예외를 `except:pass`로 삼켜 verdict 0건이던 것을 표준 `logging` 로거로 교체 → `[BUY_QUALITY][SHADOW]` 기록 정상화.
- **비전 차트패턴을 보고서 기술분석 섹션에 soft 삽입** (#385, 기본 OFF): 켜면 S3가 이미 계산한 분석을 보고서에 렌더해 매수에이전트가 읽음(추가 API콜 0).

> 비전 매수품질(S3)은 여전히 **SHADOW**입니다 — 매매결정·텔레그램 메시지에 반영되지 않고, 인사이트 이미지의 품질점수는 정보용 표시입니다.

---

## 2. 실시간 리스크 루프 LIVE 전환·강화 (Loop A/B/C)

v2.15.0에서 SHADOW로 낸 루프를 검증·강화하고 Loop C를 LIVE로 올렸습니다.

- **Loop C SHADOW 검증 강화** (#370): 풍부한 SHADOW 로깅 + dry-run 페이로드 + selftest.
- **Loop C 체결우선 cross 매수** (#378): 예산(`FILL_CHASER_BUY_MAX_PREMIUM_PCT`) 내 마케터블 cross로 즉시체결, 초과 시 취소. 동시에 **env 키 리네임**(`LOOP_A/B/C_*` → `HARDSTOP_*`/`TREND_EXIT_*`/`FILL_CHASER_*`, 구 키 alias 유지).
- **Loop C `.env` 로딩 버그 수정** (#381): load_dotenv 누락으로 LIVE 켜도 무시되던 것 수정 → LIVE 승격.
- **Loop C KR 호가단위(tick) 스냅** (#384): 추격 정정가를 KRX 호가단위로 정수 스냅(APBK0506 미정렬 오류 수정 + float 노이즈 가드).
- **Loop A/B 매도 Redis+GCP 발행** (#382): 루프 매도가 배치에만 있던 publish를 안 타 구독자 미수신·발산하던 것 수정(`loop_publish.py`).
- **포트폴리오 메시지 중복 정리** (#372 → #375 revert → #376 redo → #379): 루프/배치 매도 시 실시간 포트폴리오 요약이 2~3중 발송되던 것을, 시도 끝에 **run-end 1회 + debounce**로 정리.

---

## 3. 손절후 재매수(churn) 2중방어

손절 직후 같은 종목을 다시 사는 과매매(MU 사례 실증)를 두 겹으로 차단.

- **재진입 쿨다운 게이트** (#380): 손실 매도 후 24h 내 동일종목 재매수 차단(승리 후 0h). SHADOW-first(`REENTRY_COOLDOWN_LIVE`).
- **매매일지 churn guard** (#396): 최근 48h 내 손절 종목은 점수조정을 `min(adj,0)−2`로 만들어 섹터/동일종목 보너스를 무효화+감점(`JOURNAL_RECENT_LOSS_HOURS/PENALTY`, fail-open). 일지의 섹터평균 점수가 오히려 재매수를 부추기던 것 교정.

---

## 4. 구독자(실매매 미러) 안정화 + 모니터

- **5일 장애 복구 + 시작 자가점검** (#395): 매 주문 import 실패(인터프리터·경로 shadowing)를 importlib 로더로 수정, 시작 시 `[STARTUP_SELFCHECK]`로 거래모듈 import 확인(실패 시 경보+종료). + **헬스 모니터** 신설("살아있는데 0체결/import실패"를 5분 주기 탐지, 별도 알림봇).
- **모니터 US 로그 미집계 정정** (#397): 미국 거래 로그 포맷(`🇺🇸 US ... successful/failed`)을 못 세 가짜 CRITICAL이 뜨던 것 수정.
- **모니터 양성 거부 제외** (#398): 정당한 거부(보유아님·예산초과·현금부족)를 실패에서 분리(`benign_rejections`)해 인프라 장애만 경보 — 알람 피로 방지.

---

## 5. 성능 / 지연 최적화

- **다국어 PDF 발송 단일 브라우저 재사용 (KR·US)** (#386·#387): 번역 PDF마다 Chromium을 새로 띄우던 것(4언어×3종목=12 launch)을 **브라우저 1개 재사용**(launch 12→1), 메모리 천장 유지하며 순차 렌더.
- **KR 매수단계 보유종목 LLM 스킵** (#399): 매수 루프가 분석 픽 전 종목에 시나리오 LLM을 돌리던 중, **이미 보유 중이라 추가매수(피라미딩 #288) 불가능한 종목**은 LLM 전에 싼 사전게이트(보유 row수·수익률, DB+현재가)로 걸러 스킵. 동작 보존(결과·메시지 동일, 낭비 LLM 콜만 제거).

---

## 6. 모델 마이그레이션 (#388)

- 코드 기본 경량 모델 `gpt-5-nano`(2025-08) → `gpt-5.4-nano`(2026-03), 14파일. 프록시 모델맵에 `gpt-5.4-nano → gpt-5.4-mini` remap 추가(Codex가 nano 거부 대비). 프로덕션(OAuth)은 remap 경유라 영향 0.

---

## 7. 코드품질 — Codacy C → B (#389~#393)

- **등급 C → B 달성.** 실이슈 ~262건 정리: bare except 22건 → `except Exception`(#390), ruff safe-autofix 191건(#391), E722/B113 timeout/E702/E712/E741 등 70건(#392), 진짜 SQL injection 1건 파라미터화(#393), Codacy new-issue 게이트에 걸린 미사용 변수 정리. + 테스트/예제 경로 분석 제외(`.codacy.yaml`, #389). 밀도 8.78% → 2.68%.
- ⚠️ Codacy 게이트 = 신규 이슈 0개 정책 — PR마다 커밋 전 `ruff check` 필요.

---

## 8. 운영 가시성 · 견고성 · 문서

| # | 변경 | PR |
|---|------|-----|
| 8-1 | **`/report`·`/us_report` 1일 한도 제거** (이슈 #307) | #394 |
| 8-2 | **기능 플래그 레지스트리(LIVE/SHADOW/OFF) + 자동승격 정책 문서** | (docs) |
| 8-3 | **`feature_status.py` 런타임 상태 리포터** 신설 + 수정(OAuth 인라인 cron 감지·S6/Loop·비전 env·sys.path) | #360, #366, #367, #368 |
| 8-4 | **US 분석 실패 처리 견고화** + 날짜기반 로그 압축 | #361 |
| 8-5 | **KR 매수 0원/무효가 가드** (ZeroDivisionError 방지) | #373 |
| 8-6 | **기능 플래그 문서 런타임 동기화** (Loop B LIVE / Loop C SHADOW / S6 LIVE 반영) | #374 |

---

## 변경된 주요 파일

| 파일 | 변경 | PR |
|------|------|-----|
| `cores/insight_image.py` · `cores/llm/features/forecast_stats.py` | 인사이트 이미지: 예측 밴드·트랙레코드·통화·캡션·마커·레이아웃 | #377, #362~#365, #369, #371 |
| `cores/analysis.py` · `cores/buy_quality.py` | 비전 배관(S1~S3)·매수품질 SHADOW 로깅 복구·보고서 soft 삽입 | #356~#359, #377, #385 |
| `cores/stock_chart.py` · 비전 차트 | 오닐 일봉/주봉 차트 + RS선 + 멀티이미지(US 포함) | #359, #363 |
| `tools/loop_c_fill_chaser.py` · `trading/domestic_stock_trading.py` | Loop C LIVE: 체결우선 cross·KR tick 스냅·.env·검증로깅 | #370, #378, #381, #384 |
| `tools/loop_publish.py` · `portfolio_broadcast.py` | Loop A/B 매도 발행, 포트폴리오 메시지 1회/debounce | #382, #372, #375, #376, #379 |
| `reentry_cooldown.py` · `tracking/journal.py` | 재진입 쿨다운 + 일지 churn guard | #380, #396 |
| `examples/messaging/gcp_pubsub_subscriber_example.py` · `tools/subscriber_healthcheck.py` | 구독자 importlib/자가점검 + 헬스 모니터·오탐 정정 | #395, #397, #398 |
| `pdf_converter.py` · `prism-us/us_stock_analysis_orchestrator.py` | 다국어 PDF 단일 브라우저 | #386, #387 |
| `stock_tracking_agent.py` · `tracking/helpers.py` | 보유종목 매수 LLM 사전스킵(피라미딩 보존) | #399 |
| 14파일 · `cores/chatgpt_proxy/api_translator.py` | `gpt-5.4-nano` 마이그레이션 + remap | #388 |
| `.codacy.yaml` · 다수 | Codacy 실이슈 정리·테스트 제외 | #389~#393 |
| `telegram_ai_bot.py` | `/report` 1일 한도 제거(#307) | #394 |
| `tools/feature_status.py` (신규) · `docs/` | 기능 상태 도구 + 플래그 레지스트리/자동승격 정책 | #360, #366~#368, #374 |
| `tests/**` | 인사이트/forecast/루프/쿨다운/모니터/피라미딩 회귀 테스트 다수 | 전반 |

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

> **기본 동작 불변(전부 env 게이트)**: 인사이트 이미지(`PRISM_FEATURE_INSIGHT_IMAGE`)·비전(`PRISM_FEATURE_VISION`)·보고서 삽입(`PRISM_FEATURE_VISION_IN_REPORT`)·루프 LIVE(`FILL_CHASER_LIVE` 등)·쿨다운(`REENTRY_COOLDOWN_LIVE`)은 미설정 시 종전 동작 유지.
>
> **env 키 리네임**: `LOOP_A_*→HARDSTOP_*`, `LOOP_B_*→TREND_EXIT_*`, `LOOP_C_*→FILL_CHASER_*` (구 키 alias 유효, deprecation 경고만). 런타임 상태는 `tools/feature_status.py`로 확인.

---

## 알려진 제한사항

1. **비전 매수품질 S3 = SHADOW**: `would_buy` 게이트는 로그 전용으로 매매 미반영(품질점수는 이미지 캡션 정보 표시).
2. **루프/쿨다운 LIVE는 단계 검증 기반**: Loop C는 실 amend/cancel 수락 검증, 쿨다운은 SHADOW 오탐 0 확인 후 전환. env 게이트로 롤백 가능.
3. **다국어 PDF 단축효과 제한적**: 단일 브라우저는 정상 동작하나 실측상 배치 시간이 크게 줄지 않음(병목이 브라우저 launch가 아님) — 추가 진단 대상.
4. **#399 매수 LLM 스킵**은 분석 픽이 보유종목과 겹칠 때만 효과(상시 단축 아님).
5. **Codacy 분모(LOC) 주의**: 테스트/일부 디렉토리가 분석에서 빠져 있어, 등급(B)과 별개로 해당 영역은 품질 사각지대로 남음.

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
매매 지점 마커, 오닐 차트 패턴(일봉·주봉+상대강도선)까지 한눈에.

🛡️ 2) 실시간 리스크 루프 LIVE 전환
배치 사이 공백을 메우는 Loop A/B/C 중 미체결 추격(Loop C)을 실거래로
올리고(체결우선 즉시체결·호가단위 정렬), 루프 매도를 구독자에게도
정확히 전파하도록 정비했습니다.

🔁 3) 손절후 재매수(과매매) 2중 방어
손실로 판 종목을 곧바로 다시 사는 과매매를 '재진입 쿨다운(24h 차단)'과
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
target/stop boxes, trade markers, and O'Neil chart patterns (daily/weekly +
relative-strength line) at a glance.

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
