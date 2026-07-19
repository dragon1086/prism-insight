# Issue #412 Phase 4-a 실행 계획 — Position shadow ledger

> 기준: main `8ff33b17` (Phase 3 OrderIntent 배포 완료)
> 목표: 기존 `stock_holdings`/`us_stock_holdings` 판단 원장을 바꾸지 않고,
> 신규 `positions` 원장을 병행 기록·대조할 수 있는 안전한 첫 단계

## 1. 왜 Phase 4를 나누는가

현재 batch와 loop는 legacy simulator 원장을 broker보다 먼저 변경한다.

- 매수: legacy holding INSERT/COMMIT 후 OrderIntent 생성과 broker 호출
- 매도: history INSERT + legacy holding DELETE/COMMIT 후 OrderIntent 생성과 broker 호출
- hardstop/trend도 같은 `sell_stock()`을 호출한 뒤 broker 주문을 낸다.

이 상태에서 곧바로 `PENDING_ENTRY -> OPEN -> PENDING_EXIT -> CLOSED`를 운영
source of truth로 바꾸면 batch, loop, pyramiding, US full-exit, publish 시점을 한 번에
변경하게 된다. Phase 4-a는 먼저 legacy와 신규 원장이 항상 같은 포지션 집합을
표현하는지 shadow로 증명한다. 동작 순서를 유지하는 Phase 4-b1 intent linkage는
병행할 수 있지만, intent 생성 순서를 앞으로 옮기는 Phase 4-b2 PENDING 상태와
읽기 전환은 아래 관찰 gate를 통과한 뒤에만 다룬다.

## 2. Phase 4-a 범위

### 2.1 신규 `positions` 테이블과 `PositionStore`

- 한 테이블에서 KR/US를 `market`으로 구분한다.
- 한 legacy holding row를 한 position row로 취급한다. pyramiding은 같은 symbol의
  서로 다른 `legacy_holding_id`를 가진 독립 position 여러 개다.
- 안정 키는 `UNIQUE(market, legacy_holding_id)`이고 position id는
  `legacy:{market}:{legacy_holding_id}` 형태의 결정적 값이다.
- close/transition은 key만 보지 않고 `account_id` 일치까지 검증한다.
- 필수 필드:
  - identity: `id`, `market`, `legacy_holding_id`, `account_id`, `account_name`, `symbol`
  - lifecycle: `status`, `execution_mode`, `opened_at`, `closed_at`
  - linkage: `entry_intent_id`, `exit_intent_id` (4-a에서는 nullable)
  - immutable snapshot: `entry_price`, `exit_price`, `realized_pnl_pct`, `exit_kind`
  - audit: `created_at`, `updated_at`
- 허용 상태는 전체 목표 상태를 미리 고정한다:
  `PENDING_ENTRY`, `OPEN`, `ENTRY_FAILED`, `PENDING_EXIT`, `CLOSED`, `EXIT_UNKNOWN`.
- 4-a production mirror는 legacy 동작을 그대로 반영하므로 `OPEN`/`CLOSED`만 쓴다.
  `execution_mode='legacy'`로 기록해 shadow/demo/live 의미를 거짓으로 추정하지 않는다.

### 2.2 idempotent backfill

- KR/US agent DB 초기화 시 해당 legacy holdings를 `OPEN`으로 `INSERT OR IGNORE`한다.
- 기존 position을 덮어쓰거나 삭제하지 않는다.
- 현재 legacy schema에 row id/account key가 없는 비정상 행은 건너뛰고 mismatch로 노출한다.
- 운영 실사 기준 현재 KR 3행/US 6행 모두 id/account key가 있고 pyramiding 행은 0이다.

### 2.3 legacy와 같은 transaction의 dual-write

- buy: legacy INSERT의 `lastrowid`로 position `OPEN`을 삽입한 뒤 기존 COMMIT.
- sell: 기존 `BEGIN IMMEDIATE` transaction 안에서 history INSERT, legacy DELETE,
  position `CLOSED` 전이를 함께 수행한 뒤 기존 COMMIT.
- US full-exit은 `sell_stock()`이 sibling row별로 호출되므로 각 legacy id를 각각 닫는다.
- 성공 경로는 같은 transaction이다. mirror 쓰기는 `SAVEPOINT`로 감싸고, 내부 오류 시
  mirror 부분만 rollback한 뒤 legacy 동작과 broker 호출을 막지 않는 fail-open shadow 정책을
  사용한다. 실패는 구조화 ERROR 로그와 additive `position_mirror_errors`에 raw account 없이
  durable 기록한다. 대조기는 unresolved error를 즉시 불일치로 판정한다.
- 판단 쿼리, 주문 수량, broker 호출, Telegram/Redis/GCP publish 순서는 변경하지 않는다.

### 2.4 read-only 대조기

- `tools/compare_position_ledger.py --db-path ...`는 market/account/symbol/legacy id/entry fingerprint 기준으로
  legacy의 현재 holding 집합과 `positions(status='OPEN', execution_mode='legacy')`를 비교한다.
- 일치 시 exit 0, 불일치/중복/잘못된 CLOSED 상태 시 non-zero와 구조화 JSON을 출력한다.
- 구조화 출력에는 raw `account_id`를 노출하지 않고 hash 또는 마스킹된 식별자만 포함한다.
- 자동 수정은 금지한다. 운영 cron 등록은 코드 리뷰·배포 gate 통과 후 별도 수행한다.

## 3. 명시적 비목표

- 판단 루프의 `positions` 읽기 전환
- production에서 PENDING 상태 사용 또는 legacy 쓰기 순서 변경
- broker 접수와 체결(FILLED)의 동일시
- 주문 실패 시 legacy 원장 자동 보상
- BrokerAdapter, amend/cancel, execution 조회, reconciliation 자동화
- hardstop/trend owner lock 통합
- 기존 holdings/history 삭제 또는 schema 변경

위 항목은 최소 5번의 실제 주문 경로 실행일 동안 shadow ledger 무불일치가 확인된 뒤
Phase 4-b2, Phase 4 read switch 또는 Phase 5에서 수행한다. 기존 순서를 바꾸지 않고
nullable intent id만 연결하는 Phase 4-b1은 이 gate의 적용 대상이 아니다.

## 4. 테스트 우선 순서

1. schema가 additive이며 기존 테이블/row를 보존한다.
2. 허용 전이는 성공하고 비허용 전이는 거부된다.
3. KR/US backfill은 OPEN을 만들고 재실행해도 중복되지 않는다.
4. 같은 symbol의 pyramiding row 여러 개가 독립 position으로 보존된다.
5. KR/US buy dual-write가 legacy row와 OPEN position을 같은 commit에 남긴다.
6. KR/US sell dual-write가 정확한 legacy id 하나만 CLOSED로 만들며 남은 pyramid row는 OPEN이다.
7. US full-exit sibling 전부가 각각 CLOSED가 된다.
8. 기존 2-connection 동시 SELL 테스트에서 history/legacy delete/position close/publish가 모두 1회다.
9. mirror 실패 주입 시 기존 legacy 결과와 broker 호출 순서가 바뀌지 않고 대조기가 mismatch를 반환한다.
10. mirror 실패는 savepoint rollback 후 raw account 없는 durable error를 남기며 관찰 연속일 카운터를 리셋한다.
11. 대조기 일치/누락/extra/중복/unresolved-error 케이스와 exit code를 검증한다.

## 5. 배포·관찰 gate

1. 로컬 focused tests + 기존 KR/US process/concurrency/loop 회귀 전체 통과.
2. DB 서버 detached worktree에서 실제 Python 3.11 compile/import, 운영 DB 복제 없는
   read-only schema 사전점검, 임시 DB backfill/compare 통과.
3. main 병합 후 주문 프로세스 0인 안전창에 additive schema 적용.
4. 최초 backfill 직후 legacy OPEN 집합과 positions OPEN 집합이 완전 일치해야 한다.
   비교 단위는 legacy에 없는 broker quantity가 아니라
   `(market, account, legacy_id, ticker, active row_count, entry fingerprint)`다.
5. 매일 batch + hardstop/trend 실행 후 대조 결과 저장. batch-rest로 주문 경로가 실행되지 않은
   날은 관찰 일수에 포함하지 않는다.
6. mismatch 또는 unresolved mirror error가 한 건이라도 생기면 연속 무불일치 카운터를 0으로 리셋한다.
7. 최소 5 실행일 무불일치 전에는 Phase 4-b2 PENDING 전이와 Phase 4 read switch를 시작하지 않는다.
8. 롤백: `POSITION_LEDGER_SHADOW_ENABLED=false`; 신규 테이블은 보존해 사후 분석에 사용한다.

## 6. 완료 조건

- 신규 원장은 운영 판단/주문 결과에 영향을 주지 않는다.
- 기존 holdings/history row와 schema가 변하지 않는다.
- KR/US 단일·pyramiding·US full-exit·동시 SELL에서 shadow position 집합이 일치한다.
- 대조기가 불일치를 자동 수정하지 않고 non-zero로 탐지한다.
- 5 실행일 관찰 계획과 Phase 4-b2/read switch 진입 gate가 handoff에 기록된다.
