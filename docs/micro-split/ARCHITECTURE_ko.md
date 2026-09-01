# 초분할 아키텍처

## 1. 목표

현행은 한 후보를 즉시 1슬롯 100%로 기록하고, 강세장 피라미딩에서 같은 크기의 행을
최대 두 번 추가합니다. 초분할은 실패 후보의 초기 노출을 줄이고, 시장이 추세를
확인해 줄 때만 내부 목표비중을 높이는 anti-martingale 포지션 구축 방식입니다.

초분할은 횡보장에서 새 알파를 만드는 전략이 아닙니다. 기존 추세추종의 **진입 시점
위험과 자본 배분 경로**를 바꾸는 집행 전 정책입니다.

## 2. 절대 불변조건

1. 내부 원장·시뮬레이터와 KIS 실주문은 독립적입니다.
2. KIS 미주문·실패·미체결·취소가 내부 목표비중을 되돌리지 않습니다.
3. 목표비중은 캠페인 종료 전까지 단조 증가합니다. 비중 축소 대신 명확히 종료합니다.
4. 추가매수는 목표 단계가 상승할 때만 계산합니다. 가격 하락만으로 주문하지 않습니다.
5. 물타기를 금지합니다. 손실 중인 캠페인은 다음 단계로 올리지 않습니다.
6. 일반 레짐은 100%, `strong_bull|parabolic`만 300%까지 허용합니다.
7. 종목 수는 최대 10개, 전체 목표 슬롯 합은 최대 10슬롯입니다.
8. 한 종목은 10%만 활성화돼도 종목 슬롯 하나를 차지합니다.
9. 브로커 체결 수량은 `CONFIRMED`만 증가시킵니다. `SUBMITTED`는 체결이 아닙니다.
10. 기존 LIVE 경로는 feature gate가 명시적으로 승격되기 전까지 불변입니다.

## 3. 두 개의 독립 상태

### 전략 캠페인 상태

종목별로 다음을 가집니다.

- `campaign_id`, `market`, `symbol`, `strategy_id`, `policy_version`
- `status`: `ACTIVE|INVALIDATED|CLOSED`
- `target_pct`: 0~300, 1슬롯=100
- `unit_amount_at_start`: 캠페인 시작 당시 계정별 단위금액 snapshot
- `entry_reference`, `invalidation_price`, `risk_per_slot`
- `regime_at_start`, 현재 레짐, code/config/input hash
- 마지막 단계 전이 시각과 reason code

전략 캠페인은 KIS에 1주도 없더라도 존재할 수 있습니다.

### 실행 상태

계정별로 다음을 별도 관리합니다.

- `campaign_id`, `account_id`
- 내부 목표금액과 실행 기준가격
- 예상 정수 수량, 요청 수량, broker-confirmed 전략 소유 수량
- intent/order ref와 `NOT_REQUESTED|QUEUED|SUBMITTED|CONFIRMED|CANCELLED|REJECTED|UNKNOWN`
- 요청·ACK·fill·reconcile 시각

실행 상태는 캠페인의 목표비중을 수정할 권한이 없습니다.

## 4. 정수 주식 투영

목표 단계가 상승할 때만 다음을 계산합니다.

```text
target_notional = unit_amount_at_start × target_pct / 100
desired_qty = floor(target_notional / execution_price)
buy_delta = max(0, desired_qty - confirmed_strategy_qty)
```

`round`나 `ceil`을 사용하지 않으므로 목표금액을 넘지 않습니다. `target_pct`가 그대로인데
가격만 내려간 경우에는 계산 자체를 호출하지 않습니다.

### 예시

1슬롯 100만원, 주가 60만원:

- 10%: 목표 10만원, 예상 0주
- 30%: 목표 30만원, 예상 0주
- 60%: 목표 60만원, 예상 1주, KIS delta 1주
- 100%: 목표 100만원, 예상 1주, KIS delta 0주

1슬롯 100만원, 주가 150만원:

- 일반장 100%: 예상 0주지만 내부 캠페인은 100%까지 기록할 수 있습니다.
- 강세장 160%: 예상 1주가 되어 첫 실주문이 발생할 수 있습니다.
- 첫 실주문 시점의 손익비·손절 위험을 다시 검증하고, 부적격이면 실주문만 생략합니다.

## 5. 초안 상태 전이

전이 조건은 같은 후보의 확정 데이터만 사용하고 미래정보를 금지합니다.

- `0 → 10`: 기존 결정론적 신규매수 게이트 통과
- `10 → 30`: 다음 확정봉에서 pivot·진입 기준 유지
- `30 → 60`: 후속 상승·상대강도·거래량 확인
- `60 → 100`: +R 진행과 추세 지속, 전체 위험예산 충족
- `100 초과`: 강세 레짐, 수익 중, stop ratchet, 피라미딩 증명 조건 충족
- 어느 단계든 invalidation: 캠페인 종료, 실제 전략 소유 수량이 있으면 기존 청산 경로로 전량 종료

구체 threshold는 아직 LIVE 계약이 아닙니다. SHADOW Evidence Packet으로 고정할 한 개의
profile을 선택한 뒤 별도 문서에 사전등록합니다.

## 6. 권장 컴포넌트 경계

- `MicroSplitPolicy`: 단계·레짐 상한·전이 검증, 순수 함수
- `CampaignLedger`: 내부 목표비중과 전이 원장, broker 독립
- `ExecutionProjector`: 계정별 정수 주식 delta 예상, 주문 없음
- `ExecutionAdapter`: KIS·다른 브로커별 주문 실행
- `FillReconciler`: broker-confirmed 수량만 실행 상태에 반영
- `RiskAllocator`: 종목 수·전체 목표 슬롯·섹터·상관·open-risk 제한
- `EvidenceEmitter`: baseline과 초분할 후보를 동일 decision에 연결

다른 PRISM 버전은 Policy와 Ledger를 재사용하고 ExecutionAdapter만 교체할 수 있어야 합니다.

## 7. 기존 시스템과의 호환 제약

- `us_stock_holdings`와 `stock_holdings`는 행 하나를 사실상 1슬롯으로 가정하며 수량 필드가 없습니다.
- 기존 피라미딩 평균가는 행별 단순 평균이고, 부분매도는 브로커 총수량을 남은 행 수로 나눕니다.
- hardstop/trend-exit은 다중 행 ticker를 건너뛰고 batch가 부분청산을 담당합니다.
- 따라서 초분할 비중을 기존 holding 행 개수로 표현하면 안 됩니다.
- 새 캠페인·leg 원장은 additive shadow로 시작하고 기존 테이블을 즉시 대체하지 않습니다.

