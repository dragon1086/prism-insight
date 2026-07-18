# Phase 2 — ExecutionService 실행 계획

> 기준: `main` `c4c2539d` (PR #447/#455 포함)  
> 브랜치: `feature/issue-412-phase2-execution-service`  
> 목표: 주문 동작을 바꾸지 않고 실주문 진입점을 단일 서비스 뒤로 이동한다.

## 1. 잠글 기존 동작

구현 전에 다음 회귀를 테스트로 고정한다.

1. KR/US 중복 SELL 가드: 별도 SQLite 연결 두 개가 같은 포지션을 닫으려 할 때
   첫 번째만 성공하고 두 번째는 주문·원장·publish 없이 중단한다.
2. #288 피라미딩 분할매도: 미체결 주문 때문에 broker 수량이 줄지 않아도 한 pass의
   주문 수량 합이 최초 snapshot을 넘지 않는다.
3. 일시적인 빈 portfolio: 한 번의 빈 응답만으로 보유 없음 또는 전량청산으로 확정하지 않는다.
4. 래퍼 계약: 기존 context에 전달되는 account/ticker/price/quantity 인자와 반환값이
   리팩터 전후 동일하다.

## 2. 최소 구조

- `prism_core/execution_service.py`에 과도기 `ExecutionService`를 둔다.
- 서비스는 기존 `AsyncTradingContext`/`AsyncUSTradingContext`와 US 예약주문 trader를
  **그대로 위임**한다. 새 retry, lock, DB, idempotency 로직을 추가하지 않는다.
- 일반 주문은 `execute_buy`/`execute_sell`, 루프 정정·취소는
  `amend_or_cancel`, 예약주문은 기존 동기 호출 의미를 보존하는 전용 위임 메서드를 사용한다.
- 보유수량/fresh-position 조회는 현재 주문 직전 안전검사를 보존하기 위한 과도기
  pass-through로만 제공하고 Phase 5/6에서 BrokerAdapter/MarketDataPort로 분리한다.

### 리뷰 중 발견된 안전 수정

두 SQLite 연결을 동시에 출발시킨 실제 `sell_stock` 회귀에서 기존 guard가 모두 같은
holding 행을 읽고 둘 다 성공하는 TOCTOU가 재현됐다. 서비스에는 DB 정책을 추가하지 않고,
KR/US `sell_stock`의 기존 guard-read → history INSERT → holding DELETE 구간만
`BEGIN IMMEDIATE` 트랜잭션으로 원자화한다. 이는 새 owner-lock 설계가 아니라 기존 guard의
의도(중복 SELL 차단)를 실제 프로세스 경합에서도 성립시키는 결함 수정이다. Phase 5의
hardstop/trend/fill owner-lock 통합은 계속 비목표로 남긴다.

피라미딩 포지션에서는 ticker/account 집계만 확인하면 이미 매도된 동일 `row_id`의 두 번째
작업이 남은 다른 행을 잘못 닫을 수 있으므로, `row_id`가 전달된 경로는 writer lock 안에서
`id + ticker + account_key`가 모두 일치하는 행을 정확히 claim한다. CI는 실제 KR/US
`sell_stock` 메서드를 경량 AST로 실행하여 KR/US × DELETE/WAL × 단일행/피라미딩의
8개 두-연결 경쟁 조합을 필수 gate로 고정한다.

## 3. 이관 순서

1. KR batch 3곳
   - `stock_tracking_agent.py` 매도/매수
   - `stock_tracking_enhanced_agent.py` 매수
2. LLM-free 루프 3곳
   - `tools/hardstop_seller.py`
   - `tools/trend_exit_seller.py`
   - `tools/fill_chaser.py`
3. US 3곳
   - `prism-us/us_stock_tracking_agent.py` 매도/매수
   - `prism-us/us_pending_order_batch.py` 지연 예약주문 제출

각 묶음은 기존 focused test를 통과시킨 뒤 다음 묶음으로 넘어간다.

## 4. 명시적 비목표

- `order_intents`/`broker_orders` 테이블과 idempotency: Phase 3
- 포지션 상태기계 및 기존 delete 제거: Phase 4
- owner lock 통합, BrokerAdapter, reconciliation: Phase 5
- 이벤트버스와 KR/US 패키지 통합: Phase 6
- 매매 판단, 주문 종류, 가격, 수량, 알림 발행 시점 변경

## 5. 완료 증거

- 신규 회귀/래퍼 테스트와 기존 거래 안전 테스트 통과
- production 코드에서 직접 주문 컨텍스트·예약주문 호출은 ExecutionService 내부와
  조회 전용 제외 목록에만 존재
- import/compile 검사와 최소 진입점 smoke test 통과
- 서버 demo에서 주문 경로가 실제 실행된 거래일 3일 동안 리팩터 전과 동일한 결과 확인

live 배포와 3일 demo 게이트는 별도 운영 승인 및 장 시간 검증이 필요하므로, 코드 PR의
완료와 운영 Phase 2 완료를 구분해 기록한다.
