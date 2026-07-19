# Issue #412 Phase 4-b2b-2 — KR EXIT PENDING write-ahead 실행 계획

> 기준: main `2576c67e` (Phase 4-b2b-1 gate OFF 배포 완료)
> 브랜치: `feature/issue-412-phase4b2b2-kr-exit`
> 운영 기본값: `POSITION_PENDING_KR_ENABLED=false`
> 활성화: 이 단계에서는 금지. Phase 4-b2b-3의 별도 승인·무거래 창에서만 수행한다.

## 1. 목적과 완료 경계

KR SELL의 세 production caller를 같은 write-ahead lifecycle로 연결한다.

1. `StockTrackingAgent.update_holdings()` batch SELL
2. `tools/hardstop_seller.py` KR LIVE SELL
3. `tools/trend_exit_seller.py` KR LIVE SELL

gate=true에서는 legacy holding/history를 broker 전에 닫지 않는다. 먼저 SELL intent와
`OPEN -> PENDING_EXIT` claim을 한 transaction으로 commit하고, broker 결과가 확정된 뒤에만
legacy close와 position finalize를 수행한다. legacy holdings/history는 계속 판단 read source다.

비목표:

- US SELL 또는 `us_pending_orders` 변경
- positions read switch
- 체결/부분체결 추정, amend/cancel, reconciliation
- loop owner-lock 통합 또는 schema 추가
- FAILED/UNKNOWN 자동 retry
- 운영 gate 활성화

## 2. 현재 동작과 보존해야 할 계약

현재 gate=false 순서는 다음과 같다.

- batch: legacy history INSERT + holding DELETE + message/journal -> broker -> Redis -> GCP
- hardstop/trend: legacy close + queued message/journal -> KIS qty 조회/주문 -> Telegram flush
  -> Redis/GCP loop publish -> inflight 기록 -> owner state `SOLD`
- loop KIS qty=0: broker 주문 없이 legacy close와 외부 알림을 완료한다.
- hardstop/trend는 pyramided ticker를 건드리지 않고 batch만 fractional quantity를 계산한다.

gate=false에서는 위 순서, public bool, 수량, 메시지, journal, publish 횟수, loop summary/inflight/
owner state를 변경하지 않는다.

## 3. cleanup/refactor 계획

코드 수정 전 아래 순서로 기존 동작을 잠근다.

1. **회귀 먼저**
   - batch gate=false의 legacy/message/journal -> broker -> Redis -> GCP 순서.
   - hardstop/trend gate=false의 sim -> KIS -> Telegram/publish -> inflight/SOLD 순서.
   - multi-row fractional quantity와 local-flat broker 0 계약.
2. **legacy close 경계만 분리**
   - `sell_stock()` public bool 계약은 유지한다.
   - DB history/delete와 position close를 한 transaction에 두고, message/journal은 commit 뒤에만 노출한다.
   - pending finalize가 전달된 경우 legacy close와 `PENDING_EXIT -> CLOSED`를 같은 transaction에서
     처리하며, 실패를 bool로 숨기지 않고 caller가 quarantine할 수 있게 한다.
3. **좁은 KR EXIT lifecycle helper 재사용**
   - prepare: exact legacy row 검증 + same account/symbol unresolved guard + intent CREATED reserve +
     OPEN claim을 `BEGIN IMMEDIATE`에서 수행.
   - execute: originating IntentStore의 pre-reserved SELL만 허용.
   - complete/fail/quarantine: 기존 PositionStore API를 재사용하고 새 table/dependency를 만들지 않는다.
4. **caller별 최소 연결**
- batch gate=false는 기존 in-pass quantity accumulator를 그대로 사용한다.
- batch gate=true의 pyramided ticker는 accepted-but-unfilled 주문의 재시작 과매도를
  영속적으로 대조할 fill reconciliation이 아직 없으므로 주문 전에 전부 차단한다.
   - hardstop/trend는 KR gate=true에서만 authoritative checked holding lookup을 사용한다.
   - US와 gate=false 분기는 기존 코드를 그대로 실행한다.
5. **외부 효과는 성공 뒤에만**
   - SUBMITTED/local-flat finalize 뒤 message/journal/publish/count/SOLD.
   - FAILED/UNKNOWN/QUEUED/cancellation/finalize 실패에는 일반 SELL 효과 0.
   - enhanced `_analyze_sell_decision()`의 `holding_decisions`/`portfolio_adjustment_log` 삭제도
     gate=true에서는 판단 시점에 하지 않고 성공 finalize 뒤로 미룬다.

새 추상화는 위 세 caller의 중복 lifecycle 제거에 필요한 private dataclass/helper로 제한한다.

## 4. 상태와 부작용 행렬

### prepare

```text
BEGIN IMMEDIATE
  exact stock_holdings(id, account_key, ticker) 존재 검증
  same account/symbol의 PENDING_ENTRY, ENTRY_FAILED, PENDING_EXIT, EXIT_UNKNOWN 또는
    failed exit intent가 연결된 OPEN이 있으면 차단
  SELL intent CREATED reserve (position-first idempotency)
  position OPEN -> PENDING_EXIT
COMMIT
```

claim 성공 전 broker 호출은 0이다. 같은 position에 대한 다른 process/decision은 persisted intent 또는
position claim에서 fail-closed된다. gate=true의 피라미딩은 fill reconciliation이 추가될 때까지
종목 전체를 차단해 같은 pass뿐 아니라 프로세스 재시작 뒤의 accepted-but-unfilled 과매도도 막는다.

### 결과

- `SUBMITTED`
  - 한 transaction에서 history 1 + exact holding delete 1 + position CLOSED.
  - commit 뒤 message 1 + journal best-effort + caller publish/count.
- `LOCAL_FLAT`
  - checked KIS 상태가 명확한 FLAT일 때 broker 주문 0.
  - pre-reserved capability를 local result로 소진하고 intent를 감사 가능한 SUBMITTED/local-flat으로 기록.
  - 이후 SUBMITTED와 같은 legacy/CLOSED finalize.
- `FAILED`
  - position `PENDING_EXIT -> OPEN`; legacy/history/message/publish 불변.
  - failed intent linkage는 보존하여 자동 재주문을 막는다.
- `UNKNOWN` 또는 cancellation
  - position `EXIT_UNKNOWN`; legacy/history/message/publish 불변.
  - cancellation은 quarantine 후 원래 `CancelledError`를 재전파한다.
- pre-reserved claim 자체의 pre-broker 실패/cancellation
  - claim이 `SUBMITTING`에 도달했다면 UNKNOWN/EXIT_UNKNOWN으로 격리한다.
  - `mark_submitting` 실패로 intent가 여전히 CREATED임이 확인되면 broker 0이므로 position은
    PENDING_EXIT로 보존하고 CRITICAL/manual review로 남긴다. CREATED를 EXIT_UNKNOWN으로 바꾸는
    허위 상태 전이는 하지 않는다.
- `QUEUED`
  - position `PENDING_EXIT` 유지; legacy/history/message/publish 불변.
  - KR에서는 예상 외이므로 CRITICAL/manual review.
- broker result persistence 또는 legacy/CLOSED finalize 실패
  - intent `SUBMITTING` 또는 `SUBMITTED`를 허용해 position을 `EXIT_UNKNOWN`으로 quarantine.
  - legacy transaction은 rollback, 외부 효과 0.

## 5. loop 상태 계약

KR gate=true에서 hardstop/trend는 다음 상태만 사용한다.

- 성공 또는 authoritative local-flat: inflight `FILLED`, owner `SOLD`, `sold += 1`.
- explicit FAILED: inflight `REJECTED`, owner `HOLDING`, 일반 알림/publish/sold count 0.
- UNKNOWN: inflight `UNKNOWN`, owner `QUARANTINED`, 일반 알림/publish/sold count 0.
- QUEUED: inflight `QUEUED`, owner `QUARANTINED`, 일반 알림/publish/sold count 0.
- prepare failure/duplicate claim: broker 0, owner `HOLDING`, 일반 효과 0.
- pre-broker claim failure로 CREATED/PENDING이 남은 경우: broker 0, owner `QUARANTINED`, 일반 효과 0.
- cancellation: UNKNOWN/quarantine 기록과 lock release 후 재전파.

US 및 gate=false loop state는 변경하지 않는다.

## 6. TDD slices

### Slice A — production 변화 0

- 계획/README 갱신.
- batch/hardstop/trend gate=false 순서와 외부 효과 golden test.
- exact legacy row, same/new decision 경쟁, pyramiding 수량 기준선.

### Slice B — core/private lifecycle, caller 미배선

- local-flat pre-reserved result capability와 분류 회귀.
- `_PreparedKrExit` prepare/FAILED/UNKNOWN/QUEUED/SUBMITTED finalize helper.
- transaction rollback, cancellation, persistence/finalize failure injection.

### Slice C — batch gate=true

- single-row SUBMITTED, FAILED, UNKNOWN, QUEUED, local-flat-equivalent 경계.
- multi-row는 checked lookup/intent/broker 이전에 완전 차단.
- message/journal/Redis/GCP/sold count와 broker 호출 수.
- 두 SQLite connection 경쟁 및 다음 batch 자동 재주문 0.

### Slice D — hardstop/trend KR gate=true

- 양 loop 대표 성공/FAILED/UNKNOWN/QUEUED/local-flat/cancellation.
- checked holding `HELD/FLAT/UNKNOWN`과 pagination/malformed fail-closed.
- inflight/owner/summary 상태 행렬.
- gate=false 및 US 경로 무변경.

## 7. 검증·배포 gate

- 관련 전체 pytest, Ruff, py_compile, `git diff --check` 통과.
- 독립 architecture, concurrency/SQLite, code, test review blocker 0.
- GitHub Python 3.10/3.11/3.12 + Codacy green.
- db-server 실제 crontab wrapper(`loop_a_hardstop.py`, `loop_b_trend_exit.py`)와 batch import/compile,
  관련 회귀, 최근 fatal 로그 확인.
- app-server는 반드시 `root -> su - prism`; bot/core import, PID/cwd/stdout/fatal 확인.
- 양 서버 incoming/local overlap 0, `POSITION_PENDING_KR_ENABLED` 미설정/false 확인 후 배포.
- 배포 후에도 gate OFF. 실제 활성화는 Phase 4-b2b-3에서 사용자 판단을 받는다.
- Phase 4-b2b-3 활성화 전에는 broker accepted-but-unfilled/피라미딩 대조뿐 아니라,
  durable CLOSED 직후 cancellation으로 journal/Redis/GCP가 누락된 경우를 탐지·복구하는
  운영 reconciliation/runbook도 검증한다.
- hardstop/trend의 중복된 lifecycle 구현은 gate OFF 배포 안정성을 위해 이번 단계에서
  합치지 않는다. 공통 상태기계 추출은 동일한 다중 프로세스 회귀를 갖춘 후 별도 변경으로 수행한다.

## 8. 롤백 원칙

gate OFF 배포이므로 문제 시 코드 rollback보다 먼저 gate가 계속 OFF인지 확인한다. gate 활성화 이후에는
PENDING/EXIT_UNKNOWN 0을 확인하지 않고 flag만 내리는 롤백을 금지한다. UNKNOWN은 자동 주문하지 않고
KIS 계좌와 intent/position/legacy를 수동 대조한다.
