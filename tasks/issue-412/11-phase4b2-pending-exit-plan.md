# Issue #412 Phase 4-b2 실행 계획 — PENDING write-ahead와 실패 보상

> 기준: main `9b3ecc58` (Phase 4-b1 배포 완료)
> 원칙: legacy holdings/history는 계속 유일한 판단 read source이며, 주문 흐름 변경은
> 시장·방향별로 분리해 각각 독립적으로 롤백할 수 있어야 한다.

## 1. 전체를 한 PR로 구현하지 않는 이유

현재 BUY는 legacy holding INSERT와 OPEN mirror를 먼저 커밋한 뒤 intent를 reserve하고
broker를 호출한다. 신규 position id가 `legacy:{MARKET}:{holding_id}`이므로 holding INSERT
전에는 canonical position id가 존재하지 않는다. BUY write-ahead는 provisional position
identity 또는 simulator DB/message staging 분해가 필요하며 단순 상태 전이 추가가 아니다.

현재 SELL도 legacy history INSERT + holding DELETE + CLOSED mirror를 먼저 커밋한 뒤 broker를
호출한다. 이 순서를 그대로 둔 채 실패 시 holding을 재생성하면 전체 legacy row, history,
US adjustment cleanup, message queue를 완전히 복원해야 하며 crash 중간 상태가 더 위험하다.

따라서 4-b2를 다음처럼 분리한다.

1. **4-b2a — transaction-aware 기반 API**: 실제 주문 호출부는 바꾸지 않고 intent 사전예약,
   pre-reserved 실행, position prepare/finalize/fail API와 comparator만 추가한다.
2. **4-b2b — KR 전체 전환**: KR batch BUY/SELL, enhanced BUY, KR hardstop/trend-exit을
   시장 단위 feature flag로 동시에 전환한다.
3. **4-b2c — US queue 연속성 + US 전체 전환**: `us_pending_orders`가 원 intent ID를
   이어받도록 고친 뒤 US batch/loop/sibling 경로를 동시에 전환한다.

## 2. Phase 4-b2a 범위 — 운영 동작 OFF

### 추가할 core 경계

- `IntentStore.reserve_in_transaction(connection, intent)`
  - caller의 SQLite transaction 안에서 intent CREATED를 원자적으로 예약한다.
  - 기존 `reserve()`와 동일한 idempotency 결과를 반환하고 commit/rollback을 소유하지 않는다.
- `ExecutionService.execute_pre_reserved_*`
  - opaque reservation 없이는 호출할 수 없다.
  - intent를 다시 reserve하지 않고 CREATED -> SUBMITTING -> result 상태만 수행한다.
  - broker 호출 동안 SQLite transaction은 절대 열려 있지 않아야 한다.
- `PositionStore`
  - `prepare_entry`, `complete_entry`, `fail_entry`
  - `prepare_exit_many`, `complete_exit_many`, `fail_exit_many`
  - `mark_exit_unknown_many`
  - market/account/symbol/source ids/status/intent overwrite를 모두 검증하고 multi-row는 원자 처리한다.
- comparator
  - 짧은 PENDING과 stale PENDING, EXIT_UNKNOWN을 구분해 숨김없이 보고한다.

4-b2a에서는 production agent/loop/pending-batch 호출 순서를 변경하지 않는다. 즉 배포해도
기존 simulator -> intent/broker -> publish 동작은 그대로다.

## 3. Phase 4-b2b KR 상태 순서

### ENTRY

```text
legacy holding INSERT + intent CREATED + position PENDING_ENTRY (한 transaction)
  -> commit -> broker
     SUBMITTED -> position OPEN
     QUEUED -> PENDING_ENTRY 유지, 기존 intent를 pending batch가 이어서 제출
     explicit FAILED -> 해당 legacy row만 삭제 + ENTRY_FAILED
     UNKNOWN/cancel -> PENDING_ENTRY 유지 + CRITICAL, 자동 재주문 금지
```

legacy AUTOINCREMENT ID는 transaction 안 INSERT로 얻고, 같은 transaction에서 intent와
canonical `legacy:KR:{id}` PENDING position을 함께 커밋한다. 외부에는 세 상태가 원자적으로 보인다.

### EXIT

```text
intent CREATED + position OPEN -> PENDING_EXIT (한 transaction)
  -> commit -> broker
     SUBMITTED -> legacy history/holding + position CLOSED (한 transaction)
     QUEUED -> PENDING_EXIT 유지, 기존 intent를 pending batch가 이어서 제출
     explicit FAILED -> position OPEN (legacy 불변, 기존 position-key intent link 보존)
     UNKNOWN/cancel -> position EXIT_UNKNOWN (legacy 유지, 자동 재주문 금지)
```

### 공통 핵심 규칙

- intent를 먼저 reserve한 프로세스만 position claim을 시도한다.
- claim은 market/account/symbol/source_position_id/OPEN 상태/기존 intent overwrite를 전부
  검증한 뒤 같은 SQLite transaction에서 수행한다.
- US ticker 전체 청산은 sibling canonical id 전체를 먼저 검증한 뒤 모두 PENDING_EXIT으로
  원자 전이한다. 부분 피라미딩 청산은 대상 row 하나만 claim한다.
- claim 성공 전에는 broker를 절대 호출하지 않는다.
- claim 이후에도 legacy holding은 유지하므로 명시적 broker 실패에는 legacy 복원이 없다.
- broker SUBMITTED만 기존 `sell_stock()`을 호출해 history INSERT + holding DELETE를
  수행한다. 그 transaction에서 PENDING_EXIT -> CLOSED를 확정한다.
- UNKNOWN은 legacy holding을 유지하고 position을 EXIT_UNKNOWN으로 바꾼다. 같은 position의
  후속 SELL intent는 차단하고 운영 알림/reconciliation 대상으로 남긴다.
- 현행 SELL idempotency key는 position identity만 사용하므로 FAILED도 새 intent 자동 재제출이
  차단된다. 4-b2a는 이 안전 우선 동작을 보존한다. 4-b2b 활성화 전에 명시적 거절의 운영 알림과
  감사 가능한 retry/new-attempt 정책을 별도로 확정해야 하며, 그 전에는 KR gate를 켜지 않는다.
- 기존 `buy_stock()`/`sell_stock()` public bool 계약은 유지한다.
- subscriber publish와 Telegram flush는 legacy `sell_stock()` 성공 뒤에만 실행한다.
- KIS 조회 결과 이미 qty=0이면 broker 호출 없이 기존 simulator close를 수행하되, intent와
  position에는 명시적인 local-flat 결과가 남아야 한다.

## 4. Phase 4-b2c US 선행조건

- 현재 `us_pending_order_batch.py`는 queued intent를 이어가지 않고 새 intent를 만든다.
- `us_pending_orders`에 원 intent ID와 source position IDs를 저장하고, batch가 기존 QUEUED
  intent를 claim해 SUBMITTED/FAILED/UNKNOWN으로 계속 전이해야 한다.
- US full-exit sibling의 prepare/finalize/fail/unknown은 모두 한 transaction에서 all-or-nothing.
- 위 queue 연속성이 완료되기 전에는 US PENDING 상태를 운영 활성화하지 않는다.

## 5. 롤아웃 및 롤백

- 4-b2a는 호출부 미배선이라 별도 운영 flag 없이 동작 변화가 없다.
- 4-b2b 신규 env gate: `POSITION_PENDING_KR_ENABLED=false`가 기본값.
- 4-b2c 신규 env gate: `POSITION_PENDING_US_ENABLED=false`가 기본값.
- false이면 simulator -> broker -> publish 기존 순서와 기존 linkage를 바이트 수준으로 유지한다.
- true는 해당 시장의 batch + hardstop + trend-exit 경로가 함께 신규 lifecycle을 사용할 준비가
  된 뒤에만 허용한다. 일부 경로만 활성화하면 legacy read가 PENDING position을 다시 선택할 수 있다.
- 운영 활성화는 거래 프로세스가 없는 창에 수행하고 다음 1회 배치를 집중 관찰한다.
- 문제 시 신규 주문을 중지한 뒤 PENDING/UNKNOWN이 없는 것을 확인하고 env를 false로 되돌린다.
  남은 PENDING/UNKNOWN이 있는데 flag만 내리는 롤백은 금지한다.

## 6. 테스트 우선 gate

1. transaction-aware reserve는 caller rollback 시 intent도 사라지고 commit 시 CREATED가 보인다.
2. 기존 reserve와 transaction reserve의 duplicate/idempotency 결과가 동일하다.
3. opaque reservation 없이는 pre-reserved broker API를 호출할 수 없다.
4. pre-reserved 실행은 재-reserve하지 않고 broker를 정확히 한 번 호출한다.
5. persisted intent만 PENDING_ENTRY/PENDING_EXIT prepare가 가능하다.
6. identity/account/symbol/source sibling 불일치와 overwrite는 broker 전에 차단된다.
7. 같은 intent 재호출은 idempotent하고 다른 intent는 차단된다.
8. 두 connection 동시 SELL에서 하나만 claim하고 broker도 정확히 한 번 호출된다.
9. explicit FAILED는 PENDING_EXIT -> OPEN, legacy holding/history/message는 불변이다.
10. timeout/exception/cancel/ledger-after-broker failure는 EXIT_UNKNOWN, legacy holding 유지,
    재주문 차단이다.
11. SUBMITTED 결과만 기존 legacy sell을 실행하고 CLOSED로 확정하며 QUEUED는 PENDING을 유지한다.
12. US full-exit sibling 전체 prepare/finalize/fail이 원자적이며 부분 업데이트가 없다.
13. QUEUED -> pending batch -> SUBMITTED가 동일 intent ID를 유지한다.
14. SQLite lock/disk-full/write-ahead 실패 시 broker 호출은 0회다.
15. KR/US batch, hardstop, trend-exit의 broker/publish/Telegram 횟수와 순서를 각각 고정한다.
16. gate=false에서 기존 회귀 테스트 결과와 호출 순서가 그대로다.
17. comparator는 PENDING/EXIT_UNKNOWN을 정상 OPEN 일치로 숨기지 않고 intent/status/age를
    식별 가능한 mismatch로 보고한다.

## 7. 명시적 비목표

- positions를 판단 read source로 전환
- FILLED/PARTIALLY_FILLED 추정
- UNKNOWN 자동 복구 또는 자동 OPEN 전환
- KIS 체결 조회/reconciliation (Phase 5)
- legacy 테이블/schema 삭제
- hardstop/trend owner-lock 통합 (Phase 5)
- US pending-order queue 자체의 구조 이관

## 8. 4-b2a 완료 조건

- 신규 core 상태 API가 추가되지만 production caller diff는 0이다.
- 기존 전체 회귀 + 신규 transaction/transition/concurrency failure-injection이 green이다.
- 운영동등 DB copy에서 cron 명령 전부 startup/import 및 comparator 통과.
- 독립 architecture/SQLite/concurrency/final 리뷰에서 blocker 0.
- CI Python 3.10/3.11/3.12 + static analysis green.
- 실제 운영 활성화는 별도 승인된 무거래 창에서 수행하며, 첫 배치 전후 positions/intent/
  legacy/broker/publish 수를 대조한다.
