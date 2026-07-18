# Phase 3 — OrderIntent 영속화 실행 계획

> 기준: `main` `55dd2418` (Phase 2 및 보고서 런타임 이관 포함)
> 브랜치: `feature/issue-412-phase3-order-intent`

## 1. 이번 Phase의 안전 경계

Phase 3는 기존 `stock_holdings`/`us_stock_holdings`/`trading_history`를 변경하지 않고
실제 broker 주문 시도만 additive 원장에 기록한다. 현재 simulator 원장은 KIS 주문 성공 여부와
독립적으로 유지되는 운영 계약이므로, KIS 실패를 이유로 simulator holding을 삭제하거나 복원하지 않는다.
포지션 상태기계와 기존 원장 읽기 전환은 Phase 4 범위다.

## 2. 상태기계

```text
CREATED -> SUBMITTING -> SUBMITTED
                    \-> QUEUED   (미국 장외 주문이 로컬 지연 큐에 저장)
                    \-> FAILED   (broker가 명시적으로 거부)
                    \-> UNKNOWN  (예외/타임아웃으로 접수 여부 불명)
```

- `SUBMITTED`는 접수 성공이며 체결 성공이 아니다.
- `QUEUED`는 KIS 접수가 아니라 `LOCAL_QUEUE` 저장 성공이며 pending batch가 실제 제출한다.
- 같은 `idempotency_key`는 상태와 무관하게 두 번째 broker 호출을 차단한다.
- `UNKNOWN`은 자동 재주문하지 않는다. Phase 5 reconciliation이 체결 조회로 해소한다.
- broker 결과 수신 후 원장 저장이 실패해도 `OrderOutcomeUnknown`으로 전파해 legacy 상태를
  `failed`/`REJECTED`로 강등하지 않는다.

## 3. 식별자

- batch/loop SELL과 batch BUY는 기존 simulator position row의 `id`를 source position으로 사용한다.
- US 지연 예약주문은 기존 `us_pending_orders.id`를 source decision으로 사용한다.
- idempotency key는 market + account scope + side + source position/decision으로 결정론적으로 생성한다.
- source 이름은 key에 포함하지 않는다. hardstop/trend/batch가 같은 position을 주문해도 하나만 허용하기 위함이다.

## 4. 장애 정책

- intent DB를 열거나 `CREATED`를 저장하지 못하면 broker 호출을 하지 않는 fail-closed.
- 중복 key면 broker 호출 없이 구조화된 blocked 결과를 반환.
- broker가 명시적 실패 dict를 반환하면 `FAILED`와 raw response 저장.
- broker 호출 중 예외가 나면 접수 여부를 단정하지 않고 `UNKNOWN` 저장 후
  원인을 보존한 `OrderOutcomeUnknown`을 전파한다.
- KR/US 어댑터가 catch/timeout을 dict로 반환할 때는 `outcome_unknown=True`를 붙이고,
  ExecutionService는 문자열 추측보다 이 구조화 필드를 우선한다.
- 원장에는 API key/token 등 요청 secret을 저장하지 않는다. broker 결과와 오류 문자열은
  재귀적 redaction 후 저장한다.

## 5. 범위

- `order_intents`, `broker_orders` additive table 및 index
- `OrderIntent`, `IntentStore`
- `ExecutionService.execute_buy/execute_sell/execute_reserved_*` 영속화
- KR/US batch 5곳, hardstop/trend 2곳, US pending reserved 2종에 intent 전달
- 기존 함수 인자/반환값과 simulator publish 시점 보존

### 제외

- fill-chaser amend/cancel 흡수: Phase 5 BrokerAdapter 범위
- 체결 조회, 자동 reconciliation, UNKNOWN 자동 복구: Phase 5
- positions 테이블 및 simulator 원장 순서 변경: Phase 4
- Telegram 이벤트 버스: Phase 6. 이번 Phase는 명시적 로그와 원장 상태를 남긴다.

## 6. 구현 전 회귀 기준

1. intent가 없는 기존 테스트 호출은 broker 인자/반환값이 완전히 동일하다.
2. 성공 결과는 `SUBMITTED` + broker order row 1개.
3. 명시적 실패는 `FAILED` + broker order row 1개.
4. 예외/timeout은 `UNKNOWN`; 동일 key 재호출은 broker에 도달하지 않는다.
5. 두 SQLite 연결이 같은 key로 동시에 호출해도 broker 호출은 총 1회.
6. 기존 MU 중복 SELL, #288 over-sell, transient empty portfolio 회귀가 계속 통과한다.

## 7. 운영 배포 게이트

- 두 서버에서 additive schema 생성 및 기존 table 변경 0 확인
- 실제 주문 없이 fake broker selftest로 상태 4종(SUBMITTED/QUEUED/FAILED/UNKNOWN) 확인
- 기존 cron entrypoint import/compile 통과
- live 주문 경로는 기존 설정을 유지하며 별도 자동 주문을 만들지 않는다.
