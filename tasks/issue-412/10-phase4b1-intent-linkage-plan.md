# Issue #412 Phase 4-b1 실행 계획 — Position ↔ persisted intent linkage

> 기준: main `5ed6f38c` (Phase 4-a + direct comparator hotfix 배포 완료)
> 목표: 기존 simulator/read/order/publish 순서를 바꾸지 않고, 실제로 영속화된
> `order_intents.id`를 해당 legacy position에 연결한다.

## 1. 왜 Phase 4-b를 다시 나누는가

목표 상태기계의 `PENDING_ENTRY/PENDING_EXIT`을 의미 있게 쓰려면
`intent CREATED -> position PENDING_* -> broker -> position 확정` 순서가 필요하다.
현재 운영은 legacy holding INSERT/DELETE를 먼저 commit한 뒤 intent를 만들고 broker를
호출한다. 이 순서 변경은 실패 보상과 UNKNOWN reconciliation까지 함께 요구하므로
단순 리팩터링 범위를 넘는다.

따라서 4-b1은 현재 순서를 그대로 보존하면서 Phase 3 intent 원장과 Phase 4-a position
원장의 식별자만 연결한다. 4-b2에서만 write-ahead/PENDING 전이를 별도 검토한다.

## 2. 포함 범위

- `PositionStore`에 persisted intent 검증 + idempotent linkage API 추가.
  - entry: legacy OPEN position의 `entry_intent_id`
  - exit: legacy CLOSED position의 `exit_intent_id`
  - intent가 `order_intents`에 실제 존재하고 market/account/symbol/side가 position과
    일치할 때만 연결한다.
  - `order_intents.source_position_id`도 대상 canonical position id
    (`legacy:{MARKET}:{holding_id}`)와 일치해야 한다. US full-exit은 정렬된 sibling
    canonical id 집합 전체가 정확히 일치해야 한다.
  - 같은 intent 재연결은 성공, 다른 intent로 덮어쓰기는 거부한다.
  - US full-exit은 전체 sibling의 존재/account/symbol/CLOSED/overwrite를 먼저 검증하고
    하나의 SQL UPDATE로 동일 exit intent를 모두 연결한다.
- buy simulator 성공 시 legacy holding id를 내부 caller가 안전하게 받을 수 있는
  동작 보존형 결과 경계를 추가한다. 기존 public bool 사용자는 그대로 유지한다.
- broker 실행 결과의 `intent_id`(중복 차단이면 기존 persisted id 포함)를 사용해 연결한다.
- 신규 BUY intent에는 canonical `source_position_id`도 기록하되 idempotency identity는
  기존 `source_decision_id`를 계속 우선한다. SELL은 position identity를 우선한다.
- duplicate 결과는 기존 intent의 source position이 현재 대상과 정확히 같을 때만 연결한다.
  다르거나 NULL이면 linkage를 비워두고 durable audit을 남긴다.
- UNKNOWN 예외도 persisted intent id가 있으므로 연결하되 legacy 보상은 하지 않는다.
- linkage SQLite lock busy timeout은 50ms로 제한해 기존 거래/publish를 지연시키지 않는다.
- validation linkage 실패는 구조화 로그 + durable mirror audit으로 남긴다. SQLite lock은
  같은 DB에 audit을 쓸 수 없으므로 CRITICAL 운영 로그를 남기고 comparator가 canonical
  intent source와 NULL/wrong linkage를 후속 탐지한다.
- `POSITION_LEDGER_SHADOW_ENABLED=false`이면 linkage도 no-op한다.

## 3. 명시적 비목표

- production `PENDING_ENTRY/PENDING_EXIT` 사용
- legacy holding/history 쓰기 또는 판단 read 순서 변경
- OrderIntent reserve 시점 변경
- broker 호출, 수량 계산, Telegram/Redis/GCP publish 순서 변경
- 주문 실패 시 simulator 보상
- read source를 `positions`로 전환
- 체결(FILLED) 추정, reconciliation, amend/cancel
- 기존 position/intent schema 삭제·변경 또는 FK 강제 migration

## 4. 테스트 우선 gate

1. persisted intent만 연결되고 identity 불일치/미영속 intent는 거부된다.
2. 같은 intent 재연결은 idempotent, 다른 intent overwrite는 거부된다.
3. OPEN entry / CLOSED exit 상태 제약을 지킨다.
4. US full-exit sibling 여러 행에 같은 exit intent가 연결된다.
5. KR/US buy caller는 기존 bool 결과를 유지하면서 정확한 legacy id를 linkage에 전달한다.
6. KR/US batch와 enhanced buy, batch/loop sell의 broker/publish 순서는 그대로다.
7. BUY는 decision-first, SELL은 position-first idempotency를 유지한다.
8. duplicate intent 결과는 source position이 정확히 같을 때만 persisted existing intent id를
   연결하고, 다르면 거부/audit한다.
9. validation linkage 오류 주입 시 legacy 결과·broker/publish가 유지되고 unresolved audit이 남는다.
   SQLite lock은 1초 미만에 fail-open하고 comparator가 missing linkage를 탐지한다.
10. 기존 Phase 4-a comparator/positions, Phase 3 intent, sell concurrency 전체 회귀 통과.

## 5. 완료 조건과 롤백

- 모든 새 linkage는 additive nullable 컬럼만 갱신한다.
- legacy와 positions의 OPEN 집합 comparator는 계속 일치한다.
- 운영 DB의 기존 9 position은 linkage null이어도 정상이며 과거 주문을 추정 backfill하지 않는다.
- 신규 주문부터만 linkage를 채운다.
- 롤백은 `POSITION_LEDGER_SHADOW_ENABLED=false`; 기존 linkage 값은 사후 분석을 위해 보존한다.
- 4-b1 완료는 4-b2 PENDING write-ahead 또는 read switch 승인을 의미하지 않는다.
- broker 성공 후 linkage commit 전에 프로세스가 종료되는 crash gap은 4-b1에서 자동 복구하지
  않는다. comparator의 `intent_link_mismatches`가 탐지만 수행하고 reconciliation은 Phase 5 비목표다.
