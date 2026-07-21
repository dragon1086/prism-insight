# PRISM-INSIGHT v2.19.0 — 매수/매도 Agent 분리·KIS 실주문 단일 chokepoint·상태기계 & durable outbox · RS Rating · 리포트 SDK-중립화

> **Release Date**: 2026-07-21
> **Range**: `v2.18.0`(8eb4987f) → `main`(dd649e04) · 116 commits / 43 PRs (#431–#477)
> **Scale**: 157 files, +26,651 / −905

## 개요

이번 릴리즈의 몸통은 **거래 실행 구조의 전면 재설계(#412)** 입니다. 기존에는
`Agent가 판단 + 로컬 DB 선반영 + KIS 주문 + Journal/Telegram/Redis/GCP까지 직접 결합`된
높은 결합도 구조였고, 특히 **로컬 원장을 먼저 커밋/삭제한 뒤 KIS 주문 → 실패 시 로그만
남기는** 순서 탓에 로컬 DB와 증권사 상태가 어긋날 수 있었습니다. 이번에 모든 실주문을
`OrderIntent → ExecutionService(단일 chokepoint) → Broker/KIS` 한 경로로 모으고,
`PENDING_ENTRY → OPEN → PENDING_EXIT → CLOSED` **명시적 상태기계**와 청산 후속효과를 위한
**durable outbox(장애 격리·재시도)** 로 전환했습니다. 전 과정은 default-OFF 게이트
(`POSITION_PENDING_KR_ENABLED`) 아래 **shadow-first(무손 롤아웃)** 로 들어갔고, 이번
릴리즈에서 게이트를 켜지 않습니다.

그 외에 **오닐 RS Rating(상대강도) 스크리닝·백테스트**, **리포트 파이프라인 SDK-중립화 +
매수 시나리오 병렬 분석**, **스크리닝·시세 견고성(KRX fallback/스로틀)**, **매매·번역
gpt-5.6 전환**, **저널·프롬프트 품질**, 그리고 마지막으로 **구독자 부분매도 싱크(#477)** 가
포함됩니다. 아래는 변경 규모에 비례해 공평하게 정리했습니다.

---

## 1. #412 — 매수/매도 Agent 분리 · KIS 실주문 단일 chokepoint · 상태기계 & durable outbox ⭐ (이번 릴리즈 최대 비중, ~19 PR / +21k 라인)

설계·문제정의의 출발점은 GitHub Issue **#412**(2026-07-03)입니다. 외부 대형 PR #433은
출발 이슈가 아니라 촉매/negative reference로, 통째 병합하지 않고 안전 조각만 단계 반영했습니다.

**(1) 순수 코어 추출** — 파싱·정규화(#447)와 KST 주문시간 판정(#455)을 `prism_core`
순수 함수로 분리. LLM/증권사 의존 없는 결정 로직을 테스트 가능한 단위로 격리.

**(2) 단일 실행 chokepoint (#456)** — 모든 실주문을 **`ExecutionService`** 한 출입구로
통합. 주문 실행 경로가 하나로 모여 관측·교체·롤아웃이 가능해졌습니다.

**(3) 의도·결과 영속화 + Position shadow (#459 #460 #461 #462)** — 주문 전
`OrderIntent`와 broker result를 먼저 영속화하고, legacy holdings를 additive
**Position ledger에 shadow**로 비교. intent↔position 연결과 comparator를 직접 실행
가능한 형태로 정리(동적 SQL 제거, fail-open 경로 고정).

**(4) KR 진입/청산 PENDING 상태기계 (#463 #464 #465 #467)** — KR 진입·청산을
default-OFF 게이트 아래 `PENDING_ENTRY → OPEN → PENDING_EXIT → CLOSED` 상태기계로 연결.
happy/failure 라이프사이클과 안전경계를 회귀 테스트로 고정(#467은 +4,342로 단일 최대 PR).

**(5) readiness · outbox · replay · adapters (#469 #470 #471 #472)**
- mutation 없는 **alert-only readiness preflight**(#469)로 게이트 ON 전 상태 점검.
- CLOSED와 후속효과를 **atomic exit outbox**에 기록(#470).
- claim/lease 기반 **bounded replay**(#471) — 기본 read-only dry-run, 실제 재생은
  `--execute` + 명시 `--effect` + `--limit` 필요.
- 실제 **Journal/Telegram/Redis/GCP effect adapter**(#472) 연결. 각 effect는 독립
  전달/재시도 — 하나가 실패해도 이미 커밋된 거래는 되돌리지 않고 실패 effect만 재예약/DEAD.

**(6) KR 매도 주문번호 캡처 수정 (#473)** — KIS가 대문자 `ODNO`로 반환하는 주문번호를
KR wrapper가 소문자 `odno`로 읽어 모든 KR 매도가 빈 order_no를 기록하던 문제 수정.

> 게이트는 default OFF 유지. Phase 5(완전 BrokerAdapter·부분체결/취소 reconciliation)와
> Phase 6(prism_core + KR/US adapter 완전 분리)는 후속 범위입니다.

## 2. 오닐 RS Rating(상대강도) 스크리닝 & 백테스트 ⭐ (#435 #436 #437)

- `cores/rs_rating.py` 신설 + KR/US 스크리닝에 **RS Rating SHADOW-gate** 도입(#437).
- RS Rating **백테스트 도구**와 KR/US 검증 보고서(#436, +509).
- KR #289 다주 상대강도를 US로 이식 — 60일 수익률(return_nd) + extension 블렌드(#435).

## 3. 리포트 파이프라인 SDK-중립화 + 매수 병렬 분석 (#454 #457 #458 #453)

- KR 파이프라인에서 **mcp-agent 런타임 제거**, `report_generation`을 SDK-중립 경로로
  전환하고 CI 게이트 추가(#457).
- Telegram/dashboard **report generator 포트 분리**(#458)로 리포트 생성과 전달을 디커플.
- KR 트레이딩 배치의 **매수 시나리오 분석 병렬화**(#454, +411)로 배치 처리 시간 단축.
- Responses API + **gpt-5.6-terra**로 리포트 생성 경로 추가(#453).

## 4. 스크리닝 · 시세 견고성 (#434 #440 #441 #445 #450)

- KRX 장애 시 **FinanceDataReader fallback** + 260일 단일 fetch 최적화(#440).
- KRX **요청 버스트 스로틀**로 연타 방지 → read-timeout 빈도 감소(#441).
- KRX 순단 시 **KIS 시세 fallback** + 매수후보 분석 실패 알림(#434).
- bottom-up 트리거의 **실제 변동률** 보고(#450, 스크리닝 0% 표기 버그).
- BTC 시세 **None 가드** — 시세 부재 시 값 조작 대신 defer(#445).

## 5. 매매 · 번역 모델 gpt-5.6 전환 (#451 #452)

- 매매 판단 모델을 **gpt-5.6-sol**(reasoning_effort=high)로 전환(#451).
- Telegram/dashboard 번역을 **gpt-5.6-luna**로 전환(#452).

## 6. US 2배치 정책 & 루프 정합 (#431 #438)

- US 분석 배치를 **아침/오후 2배치 정책**으로 정리(#431).
- 루프 매도 텔레그램 누락 수정 — `send_telegram_message` await_broadcast 시그니처 정합(#438).

## 7. 저널 · 매수 프롬프트 품질 (#474 #475)

- 매수 프롬프트에 주입되는 **직관(intuition) 노이즈** 상한·카테고리별 다양화(#475) —
  활성 직관 무한 누적(99개) 문제 해소.
- **분산일(distribution_days)** 카운트를 KR/US 매수 프롬프트에 숫자로 주입(#474, #448).

## 8. 안정성 · 운영 · 보안 픽스 (#432 #439 #442 #443 #444 #476)

- OAuth 토큰 **atomic·private(600) 저장**(#442)과 쿼터 창을 백엔드 텔레메트리에서 도출한
  3시간 리포트(#432).
- Redis/Pub-Sub **동기 I/O를 이벤트 루프 밖으로**(#443) — 트레이딩/텔레그램 블로킹 방지.
- 잘못된 매도수량은 **전량청산 대신 거부**(#444).
- 백오피스 텔레그램 **수동 발송 툴**(#439, `tools/admin_send_message.py`).
- **US 리포트 cores-shadow 버그 수정**(#476) — `prism-us/cores/`가 루트 `cores/`를
  shadow해 발생한 `ModuleNotFoundError`(밤사이 US morning 배치 실패의 원인)를 US 경계만
  스왑해 해결.

## 9. 구독자 부분매도 싱크 (#477)

본로직은 피라미딩(#288) 다중 lot 티커를 **부분매도**(`floor(총량/remaining_rows)`)하지만,
발행 SELL 시그널에 수량 힌트가 없어 예제 구독자가 항상 **전량청산**했습니다. SELL payload에
**`sell_denominator`**(기본 1=전량, 하위호환) 를 추가하고, 구독자가
`floor(보유/denominator)` 로 **소스와 동일한 1/N 비율**을 매도하도록 정합. hardstop/trend
루프 매도는 기본 1 → 전량(의도된 동작).

---

## 변경 규모 요약 (PR별, 공평 비교)

| PR | 주제 | 규모 |
|---|---|---|
| #467 | #412 KR pending exit 라이프사이클 | 18 files, +4,342/−19 |
| #465 | #412 KR pending entry harden | 11 files, +2,183/−12 |
| #460 | #412 legacy holdings Position shadow | 11 files, +2,023/−19 |
| #463 | #412 pending position 상태 foundation | 7 files, +2,000/−52 |
| #456 | #412 ExecutionService 단일 chokepoint | 26 files, +1,915/−94 |
| #472 | #412 production effect adapters (outbox 마감) | 24 files, +1,762/−197 |
| #459 | #412 OrderIntent/broker result 영속화 | 18 files, +1,718/−54 |
| #462 | #412 intent↔position 연결 | 19 files, +1,449/−91 |
| #471 | #412 bounded exit replay | 7 files, +1,227/−8 |
| #469 | #412 KR readiness preflight | 6 files, +1,084/−6 |
| #464 | #412 KR pending exit lifecycle wire | 8 files, +674/−14 |
| #474 | 분산일 매수 프롬프트 주입 (#448) | 5 files, +630/−10 |
| #470 | #412 durable exit outbox | 5 files, +517/−1 |
| #436 | RS Rating 백테스트 도구 + 보고서 | 2 files, +509 |
| #437 | 오닐 RS Rating SHADOW-gate (KR/US) | 6 files, +404/−3 |
| #454 | KR 매수 시나리오 병렬 분석 | 3 files, +411/−7 |
| #475 | 매수 프롬프트 직관 노이즈 상한·다양화 | 3 files, +387/−6 |
| #440 | KRX fallback + 260일 단일 fetch 최적화 | 4 files, +345/−24 |
| #435 | US 다주 상대강도 이식 | 2 files, +338/−10 |
| #477 | 구독자 부분매도 싱크 (sell_denominator) | 6 files, +333/−12 |
| #458 | telegram report generator 포트 분리 | 7 files, +289/−253 |
| #443 | Redis/Pub-Sub 논블로킹 I/O | 5 files, +221/−7 |
| #447 | #412 파싱·정규화 prism_core 추출 | 6 files, +219/−61 |
| #445 | BTC 시세 None 가드 | 4 files, +197/−24 |
| #444 | 매도수량 검증(전량청산 방지) | 3 files, +176/−4 |
| #432 | OAuth 쿼터 3시간 리포트 | 4 files, +175/−27 |
| #431 | US 2배치 정책 | 13 files, +169/−110 |
| #476 | US 리포트 cores-shadow 수정 | 2 files, +188/−2 |
| #457 | mcp-agent 런타임 제거(리포트 SDK-중립) | 16 files, +399/−75 |
| #439 | 백오피스 텔레그램 수동 발송 툴 | 1 file, +126 |
| #455 | #412 KST 주문시간 판정 추출 | 3 files, +117/−12 |
| #450 | bottom-up 실제 변동률 보고 | 2 files, +93 |
| #442 | OAuth 토큰 atomic·private 저장 | 3 files, +82/−14 |
| #434 | KRX 순단 시 KIS 시세 fallback | 3 files, +82 |
| #473 | KR 매도 ODNO 캡처 수정 | 2 files, +76/−6 |
| #461 | #412 position CLI import 수정 | 2 files, +26 |
| #441 | KRX 요청 버스트 스로틀 | 1 file, +28 |
| #438 | 루프 매도 텔레그램 시그니처 정합 | 1 file, +22/−3 |
| #452 | 번역 gpt-5.6-luna 전환 | 12 files, +22/−22 |
| #453 | Responses API gpt-5.6-terra 리포트 | 1 file, +18/−11 |
| #451 | 매매판단 gpt-5.6-sol 전환 | 5 files, +15/−11 |
| #466 #468 | #412 phase 문서 마킹 | 각 1 file, +3/−3 |

## 업데이트 방법

```bash
git fetch origin --tags
git checkout v2.19.0        # 또는: git pull origin main (ff-only)
# 운영 서버는 dirty 운영파일 보존을 위해 ff-only pull 권장
```

`.env` 신규 필수 키 없음. `POSITION_PENDING_KR_ENABLED`는 **default OFF 유지** — 이번
릴리즈에서 게이트를 켜지 않습니다. 실주문 실행 동작은 게이트 OFF에서 기존과 동일합니다.

## 알려진 제한사항

- **#412 게이트는 OFF**: PENDING 상태기계·outbox·replay는 코드/테스트로 검증됐으나
  `POSITION_PENDING_KR_ENABLED=true`는 별도 명시 승인 + 무거래 창 + readiness `ready` 확인
  후에만 켭니다. Phase 5(부분체결/취소 reconciliation)·Phase 6(코어/어댑터 완전 분리)는 후속.
- **구독자 부분매도(#477)** 효과는 발행 서버가 `sell_denominator`를 실제 발행하는 다음
  KR/US 부분매도 이벤트에서 관측됩니다(구버전 시그널은 기본 1=전량으로 안전 폴백).
- **RS Rating(#437)** 은 SHADOW-gate — 실매매 반영 전 관측 단계입니다.
- 외부 대형 PR **#433은 계속 OPEN** — 안전 조각만 단계 반영했고 통째 병합하지 않습니다.

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.19.0 — 거래 실행 구조 전면 재설계 · RS Rating · 리포트 SDK-중립화
(Release Note : https://github.com/dragon1086/prism-insight/releases/tag/v2.19.0)

이번 릴리즈의 몸통은 '거래 실행 구조의 전면 재설계'입니다.

🏗️ 1) 매수/매도 실행을 한 경로로 (최대 비중, #412)
· 모든 실주문을 OrderIntent → ExecutionService(단일 출입구) → KIS 한 경로로 통합
· PENDING_ENTRY→OPEN→PENDING_EXIT→CLOSED 명시적 상태기계 + 청산 후속효과 durable outbox
· 로컬 원장 선커밋/삭제로 증권사와 어긋나던 옛 순서를 제거 (shadow-first, 게이트는 OFF 유지)

📈 2) 오닐 RS Rating(상대강도) 스크리닝 + 백테스트
· cores/rs_rating.py 신설, KR/US 스크리닝에 SHADOW-gate로 도입 (관측 단계)

⚙️ 3) 리포트 파이프라인 SDK-중립화 + 매수 병렬 분석
· mcp-agent 런타임 제거, 리포트 생성/전달 디커플, KR 배치 매수분석 병렬화로 처리시간↓

🛡️ 4) 견고성·안정성
· KRX 장애 fallback/스로틀, KIS 시세 fallback, Redis/PubSub 논블로킹 I/O
· 매도수량 검증(전량청산 방지), OAuth 토큰 atomic 저장, US 리포트 shadow 버그 수정

🔁 5) 구독자 부분매도 싱크
· 피라미딩 부분매도를 구독자가 sell_denominator로 미러링 — 전량청산 방지

📊 #412 상태기계는 default-OFF 게이트로 무손 롤아웃, 이번 릴리즈에서 켜지 않습니다.
```

### English

```
🚀 PRISM-INSIGHT v2.19.0 — trading-execution rearchitecture · RS Rating · SDK-neutral reports
(Release Note : https://github.com/dragon1086/prism-insight/releases/tag/v2.19.0)

The backbone of this release is a full rearchitecture of trade execution.

🏗️ 1) One path for buy/sell execution (biggest theme, #412)
· Every real order routed through OrderIntent → ExecutionService (single chokepoint) → KIS
· Explicit state machine PENDING_ENTRY→OPEN→PENDING_EXIT→CLOSED + durable exit-effect outbox
· Removed the old "commit/delete local ledger first" order that could diverge from the broker
  (shadow-first, gate stays OFF)

📈 2) O'Neil RS Rating screening + backtest
· New cores/rs_rating.py, wired into KR/US screening behind a SHADOW gate (observation phase)

⚙️ 3) SDK-neutral report pipeline + parallel buy analysis
· mcp-agent runtime removed, report generation/delivery decoupled, KR batch buy-analysis parallelized

🛡️ 4) Robustness & stability
· KRX fallback/throttle, KIS quote fallback, non-blocking Redis/PubSub I/O
· Reject malformed sell quantity (no accidental full liquidation), atomic OAuth token save,
  US report cores-shadow fix

🔁 5) Subscriber partial-sell sync
· Pyramiding partial exits mirrored to subscribers via sell_denominator — no more full liquidation

📊 The #412 state machine ships behind a default-OFF gate (zero-loss rollout) and is not enabled here.
```
