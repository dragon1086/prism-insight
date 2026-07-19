# Issue #412 Phase 4-b2b-1 실행 계획 — KR ENTRY 연결, flag OFF

> 기준: main `449e4750` (Phase 4-b2b-0 양 서버 배포 완료)
> 운영 기본값: `POSITION_PENDING_KR_ENABLED=false`
> 배포 원칙: 일반/enhanced BUY 코드를 모두 연결해도 Phase 4-b2b-2 EXIT 완료 전에는 gate를 켜지 않는다.

## 1. 목표와 비목표

일반 KR batch BUY와 enhanced BUY가 같은 PENDING_ENTRY lifecycle을 사용하도록 구현한다.
gate=false에서는 기존 holding commit → message → broker → Redis → GCP 순서와 public bool/count를
완전히 유지한다. gate=true에서는 broker 전에 holding, intent CREATED, PENDING_ENTRY를 한 transaction으로
commit하고 SUBMITTED + OPEN finalize 뒤에만 일반 메시지와 publisher를 실행한다.

비목표:

- KR SELL, hardstop, trend-exit 전환(4-b2b-2)
- gate 활성화 및 운영 실주문 검증(4-b2b-3)
- FAILED 자동 retry 또는 tombstone 해제 도구
- durable alert outbox와 reconciliation
- US 주문 경로

## 2. 구현 전 필수 안전 조건

### 자동 재주문 금지 guard

`OrderIntent` idempotency는 `source_decision_id`를 우선하므로 새 보고서는 새 attempt를 만들 수 있다.
FAILED 보상으로 legacy holding이 삭제되면 기존 holdings gate도 사라진다. enhanced `is_add=True`는
holdings gate 자체를 우회한다.

따라서 `BEGIN IMMEDIATE` 안에서 동일 KR account/symbol의 기존 position 중 다음 상태가 하나라도 있으면
새 holding INSERT와 intent reserve 전에 fail closed한다.

- `PENDING_ENTRY`
- `ENTRY_FAILED`
- `PENDING_EXIT`
- `EXIT_UNKNOWN`

이 guard는 `PositionStore`의 transaction-required API로 구현해 다른 caller도 재사용할 수 있게 하고,
동시 connection 경쟁 테스트로 한 transaction만 통과함을 증명한다. operator authorization/new-attempt
도구가 생기기 전에는 tombstone을 자동 해제하지 않는다.

### readiness

`POSITION_PENDING_KR_ENABLED=true`이면 다음을 broker 전에 모두 만족해야 한다.

- `POSITION_LEDGER_SHADOW_ENABLED=true`
- positions/order_intents schema 초기화 성공
- originating `IntentStore`가 agent DB와 동일

기존 shadow 초기화는 fail-open이므로 pending 전용 readiness를 별도 상태로 저장한다. readiness가 없으면
holding/intent/position/broker를 모두 0으로 유지하고 CRITICAL만 남긴다.

## 3. private lifecycle 경계

- `_position_pending_kr_enabled() -> bool`: 기본 false.
- `_require_pending_entry_ready() -> None`: gate=true dependency를 fail closed 검증.
- immutable `_PreparedKrEntry`: legacy id, account scope, ticker, intent, opaque reservation,
  아직 enqueue하지 않은 message를 운반.
- `_prepare_pending_kr_entry(...)`:
  1. transaction 밖에서 originating `IntentStore` 준비.
  2. `BEGIN IMMEDIATE`.
  3. unresolved account/symbol guard.
  4. legacy holding INSERT 후 id 확보.
  5. id로 `OrderIntent` 생성 및 `reserve_in_transaction()`.
  6. `PositionStore.prepare_entry()`.
  7. message는 생성만 하고 enqueue하지 않음.
  8. commit 후 prepared object 반환. 실패/중복은 전체 rollback, broker 0.
- `_execute_pending_kr_entry(...)`: 동일 `IntentStore`를
  `ExecutionService.domestic(intent_store=...)`에 주입하고 `execute_pre_reserved_buy()` 실행.
- `_complete_pending_kr_entry(...)`: 새 transaction에서 `complete_entry()` 후 commit. commit 성공 후에만
  message enqueue.
- `_fail_pending_kr_entry(...)`: 한 transaction에서 exact holding DELETE의 account/ticker/id와 rowcount=1을
  검증한 뒤 `fail_entry()`. 어느 단계든 실패하면 전체 rollback해 holding + PENDING을 보존.

gate=false는 기존 `_buy_stock_with_position()`과 두 caller 블록을 그대로 사용한다. gate=true만 신규
lifecycle로 분기한다. 공통 message builder 추출이 필요하면 먼저 기존 문자열 golden 테스트를 고정하고,
OFF 경로의 enqueue 위치는 바꾸지 않는다.

## 4. 결과별 상태와 부작용

- `SUBMITTED`: intent SUBMITTED 확인 → position OPEN transaction commit → message 1 → Redis 1 → GCP 1
  → buy_count 1.
- `FAILED`: exact holding delete + position ENTRY_FAILED transaction commit → 일반 message/publish/count 0
  → CRITICAL 1.
- `UNKNOWN`, broker exception, result persistence failure, 예상 외 `QUEUED`: holding과 PENDING_ENTRY 유지,
  일반 message/publish/count 0, CRITICAL 1, 자동 재주문 0.
- coroutine cancellation: claim 결과에 따라 intent UNKNOWN 보존, position PENDING_ENTRY 유지, shielded
  CRITICAL 후 `CancelledError` 재전파.
- broker SUBMITTED 후 OPEN finalize 실패: transaction rollback, holding + PENDING_ENTRY 유지,
  publish 0, CRITICAL 1.
- FAILED 보상 transaction 실패: rollback으로 holding + PENDING_ENTRY 유지, publish 0, CRITICAL 1.

CRITICAL에는 market, symbol, side, intent id, 안전한 account fingerprint, status/action만 포함한다.
raw broker payload, token, account 원문은 금지한다. durable exactly-once는 outbox 전에는 보장하지 않으며
이 한계 때문에 운영 gate 활성화는 계속 차단한다.

## 5. TDD 순서

### Slice 1 — OFF 회귀 + SUBMITTED happy path

1. 일반 BUY gate=false 이벤트 순서:
   `legacy commit/message → broker → Redis → GCP`, 각 1회.
2. enhanced BUY gate=false 이벤트 순서:
   `_buy_stock_with_position → broker → Redis → GCP`, 각 1회.
3. 일반 gate=true SUBMITTED:
   - broker 진입 시 별도 DB connection에서 holding 1, intent SUBMITTING, position PENDING_ENTRY.
   - publisher 진입 시 intent SUBMITTED, position OPEN, message 1.
   - 최종 broker/message/Redis/GCP 각 1, CRITICAL 0.

### Slice 2 — fail closed matrix

- explicit FAILED 보상.
- broker exception/UNKNOWN.
- broker 성공 후 result persistence failure(SUBMITTING 유지).
- 예상 외 QUEUED.
- OPEN finalize 실패.
- FAILED compensation 실패.
- prepare/lock/write failure: holding/intent/position/broker 모두 0.
- pending=true + shadow/readiness=false: 전부 0.

### Slice 3 — cancellation, 경쟁, 재실행

- broker coroutine cancellation은 UNKNOWN/PENDING을 남기고 cancel 재전파.
- 같은 account/symbol 두 connection 경쟁은 reservation/holding/position/broker 각 1.
- UNKNOWN 뒤 같은 decision 재실행과 새 decision 재실행 모두 broker 누계 1.
- ENTRY_FAILED 뒤 새 decision 및 `is_add=True`도 broker 0.

### Slice 4 — enhanced 동일 lifecycle

- enhanced SUBMITTED가 base와 같은 private lifecycle을 사용.
- enhanced FAILED/UNKNOWN은 대표 1개씩 동일 상태/부작용 계약.
- 병렬 pre-pass 뒤 sequential holdings/sector gate 순서와 buy_count 불변.

## 6. 검증·배포 gate

- 기존 BUY baseline 22개 이상 전부 유지.
- order_intents/positions/execution_service, pyramiding, parallel batch, process_reports 관련 회귀 green.
- 독립 architecture/code/test review blocker 0.
- Python 3.10/3.11/3.12 CI + Codacy green.
- db-server 실제 root crontab의 KR orchestrator와 hardstop/trend/fill-chaser compile/import 및 관련 회귀.
- app-server는 root SSH 후 반드시 `su - prism`; bot/report/core import와 기존 bot 무중단 확인.
- 배포 후 `POSITION_PENDING_KR_ENABLED`가 미설정/false이고 PENDING/ENTRY_FAILED 신규 행 0인지 확인.
- bot/cron 재시작 불필요. gate 활성화는 4-b2b-2 완료 후 별도 4-b2b-3 승인에서만 수행.
