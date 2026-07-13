# 03. 목표 아키텍처

## 1. 전체 그림

```
┌─────────────────────────── prism_core (시장/브로커/알림 무지식) ───────────────────────────┐
│                                                                                            │
│  TrackingEngine (흐름만: Sell Phase → Buy Phase → Report)                                   │
│    │                                                                                       │
│    ├── DecisionPort        ← LLM Buy/Sell Agent (Pydantic 출력 계약)                        │
│    ├── MarketDataPort      ← 시세/거래량/지수/변동성                                          │
│    ├── PositionRepository  ← 원장 (상태 전이, delete 금지)                                    │
│    ├── ExecutionService    ← 주문 단일 chokepoint (intent 영속화, idempotency, 가드)          │
│    │     └── BrokerAdapter ← KIS 국내/해외, (미래: 타 증권사)                                 │
│    ├── PortfolioPolicy     ← 슬롯/섹터/점수 임계값 (설정 객체)                                 │
│    ├── MarketProfile       ← timezone/장시간/통화/호가단위/섹터분류                            │
│    └── EventBus            ← 도메인 이벤트 발행                                              │
│                                                                                            │
└────────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ 구독자 (adapter 계층): TelegramNotifier, RedisPublisher, GcpPublisher,
                                 FirebaseNotifier, JournalWriter, ReconciliationAlerter

prism_kr = KR MarketProfile + pykrx MarketData + KIS domestic BrokerAdapter
prism_us = US MarketProfile + yfinance MarketData + KIS overseas BrokerAdapter (포크 삭제)
```

원칙:
- 코어는 어떤 시장·브로커·알림 채널도 모른다. 전부 생성자 주입.
- 상속 대신 합성: Enhanced의 동적 손절/목표가·시장상황 분석은 Strategy 플러그인.
- 알림/publish는 인라인 호출 금지. 엔진은 이벤트만 발행한다
  (`PositionOpened`, `PositionClosed`, `OrderSubmitted`, `OrderFailed`,
  `OrderUnknown`, `ReconciliationMismatch`).

## 2. 포트 인터페이스

### 2.1 ExecutionService — 모든 주문의 단일 chokepoint

```python
class ExecutionService:
    """모든 매수/매도 주문이 지나는 유일한 경로.
    - intent 영속화 (idempotency unique 제약)
    - 중복 SELL 가드 (fresh snapshot — 현 KR/US sell_stock 가드의 시맨틱 1:1 이관)
    - 분할매도 수량 계산 (#288 스냅샷 로직 이관)
    - 크로스 프로세스 lock (loop_a_position_state owner_lock의 계승·일반화)
    - execution_mode 라우팅 (live / demo / shadow)
    """
    async def execute_buy(self, intent: BuyOrderIntent) -> OrderResult: ...
    async def execute_sell(self, intent: SellOrderIntent) -> OrderResult: ...
    async def amend_or_cancel(self, intent: AmendOrderIntent) -> OrderResult: ...
```

**커버리지 (07-13 기준)**: chokepoint가 감싸야 할 실주문 진입점은 batch 4곳이
아니라 **9곳**이다 — KR batch 매수/매도, enhanced 매수, 루프 3종(hardstop /
trend_exit / fill_chaser), US batch 매수/매도, US 예약주문 확인 배치.
전체 표는 01 문서 §1. 루프는 LLM-free·별도 cron이므로 ExecutionService는
LLM 판단 없이 intent만 받아도 동작해야 한다 (DecisionPort 비의존).

### 2.2 BrokerAdapter — 이슈 #412 초안 + 누락 메서드 추가

```python
class BrokerAdapter(Protocol):
    async def buy(self, intent: BuyOrderIntent) -> OrderResult: ...
    async def sell(self, intent: SellOrderIntent) -> OrderResult: ...
    async def get_position(self, symbol: str, account_id: str) -> BrokerPosition | None: ...
    async def get_portfolio(self, account_id: str) -> list[BrokerPosition]: ...
    async def cancel_order(self, order_id: str) -> OrderResult: ...
    # ↓ 이슈 초안에 없던 필수 추가분
    async def get_order_status(self, order_id: str, account_id: str) -> OrderStatus: ...
    async def list_executions(self, account_id: str, date: date) -> list[Execution]: ...
    async def list_open_orders(self, account_id: str) -> list[OpenOrder]: ...
    async def amend_order(self, order_id: str, new_price: Decimal) -> OrderResult: ...  # 07-13 추가
```

- KIS 구현: 주문체결조회(`inquire-daily-ccld`), 해외 체결내역 조회 사용.
- `list_open_orders`/`amend_order`의 참조 구현이 이미 있다: `tools/fill_chaser.py`가
  KR `get_revisable_orders` / US `get_unfilled_orders`를 source of truth로 정정/취소를
  수행한다 (07-13 확인). adapter는 이 wrapper들을 흡수한다. ⚠️ 해당 TR wrapper는
  아직 라이브 검증 전 (`tasks/loop_c_design.md` 체크리스트 선행).
- US 예약주문의 **시간창 밖 큐잉 → 지연 제출**(`prism-us/us_pending_order_batch.py`,
  `us_pending_orders` 큐)을 order_intents 상태기계로 흡수한다 — 이 큐는 사실상
  CREATED→SUBMITTING 전이의 기존 실물 선례이므로, §3.1 상태기계의 마이그레이션
  대상으로 명시한다. 제출 후 체결 확인은 reconciliation job 담당.
- get_portfolio의 일시적 빈 응답 가드(현 `domestic_stock_trading.py:1431`)는
  adapter 내부 책임으로 유지.

### 2.3 DecisionPort — LLM 출력 계약 고정

```python
class BuyDecision(BaseModel):
    decision: Literal["Enter", "No Entry"]
    buy_score: int = Field(ge=0, le=10)
    scenario: TradingScenario        # target_price, stop_loss, invalidation 포함
    rationale: str
    decision_id: str                 # UUID, 생성 시점 부여 → idempotency_key의 원천
    prompt_version: str

class SellDecision(BaseModel):
    decision: Literal["Hold", "Full Exit"]
    exit_kind: Literal["stop", "trend_exit", "target", "ai", "manual"] | None
    rationale: str
    decision_id: str
    prompt_version: str
```

현재 흩어져 있는 방어적 파싱(`_normalize_decision`, `_parse_price_value`,
`_safe_number_conversion`)은 이 경계 한 곳으로 수렴한다.
프롬프트와 스키마는 함께 버저닝한다 (이슈 #412 제안 채택).

## 3. 상태기계

### 3.1 주문 (order_intents)

```
CREATED ──► SUBMITTING ──► SUBMITTED ──► FILLED | PARTIALLY_FILLED | CANCELLED | REJECTED
                │
                ├──► FAILED    (broker가 명시적 거부 — 보상 실행)
                └──► UNKNOWN   (타임아웃/네트워크 — 체결내역 조회로 복구, 사람 알림)
```

- KIS에 client-order-id가 없으므로, UNKNOWN 상태의 복구는
  `list_executions()` 대조로만 가능하다. 이것이 idempotency의 실체다.

### 3.2 포지션 (positions) — delete 금지

```
PENDING_ENTRY ──► OPEN ──► PENDING_EXIT ──► CLOSED
      │                        │
      └──► ENTRY_FAILED        └──► EXIT_UNKNOWN (사람 개입)
```

- **쓰기 순서 확정**: ① intent 영속화(CREATED) → ② 포지션 PENDING_* 전이 →
  ③ broker 호출 → ④ 접수 확인 시 OPEN/CLOSED 확정, 실패 시 보상 전이.
  현재의 "원장 선커밋 후 fire-and-forget"을 제거한다.
- 매도 시 행 삭제 금지 → CLOSED 전이. reconciliation의 전제 조건.

## 4. DB 모델 (이슈 #412 채택 + 보강)

- `order_intents`: idempotency_key UNIQUE, decision_id, status, execution_mode,
  created_at, submitted_at, raw_request
- `broker_orders`: intent_id FK, broker_order_no, status, filled_qty, avg_price,
  last_checked_at, raw_response
- `positions`: 상태기계 컬럼 + execution_mode 컬럼 (shadow/demo/live 격리)
- 기존 `stock_holdings`/`trading_history`는 Phase 4까지 병행 유지 (04 참고)

### shadow 모드 시맨틱 (이슈 미정의분 확정)
- shadow intent는 broker 호출 없이 SUBMITTED(가상) 처리, 시세 기준 가상 체결 기록.
- positions.execution_mode='shadow' 행은 매도 판단 루프에서 shadow 전용 계좌
  스코프로만 조회된다. live 판단 경로와 절대 섞이지 않는다 (쿼리 필터 강제).
- 선례 (07-13): 루프의 `HARDSTOP_LIVE`/`TREND_EXIT_LIVE`/`FILL_CHASER_LIVE` env
  게이트가 이미 "기본 SHADOW, 명시적 opt-in으로만 LIVE" 시맨틱을 운영 중이다.
  execution_mode 라우팅은 이 규칙을 계승한다 (기본값은 항상 비실주문 쪽).
- 주의 (hardstop 사례에서 배운 것): 과거 SHADOW 레코드가 LIVE 매도를 3주간
  차단한 버그가 있었다 (`tools/hardstop_seller.py:186-187` 주석). shadow 기록은
  live 경로의 중복 판정에 **절대 입력되지 않아야 한다** — 격리는 양방향이다.

## 5. 크로스 프로세스 동시성

- 티커/계좌 단위 lock: **기존 `loop_a_position_state` owner_lock(SQLite
  `BEGIN IMMEDIATE` + 만료 시각, `tools/hardstop_seller.py:200-235`)을 계승·일반화**해
  루프 전체 + batch 에이전트를 포괄한다. 새 메커니즘 발명 금지. 주의: 현재는
  hardstop+fill_chaser만 이 lock을 공유하고 **trend_exit는 별도 `loop_b_position_state`를
  쓴다** — hardstop↔trend_exit 미직렬화 상태이므로 통합 시 loop_b_* 테이블 흡수가
  필수 작업이다 (01 §4.5). (asyncio.Lock은 보조 수단으로 유지)
- 중복 SELL 가드는 ExecutionService 안에서 fresh snapshot(commit 후 재조회)으로
  수행 — 현행 KR(`stock_tracking_agent.py:1300-1327`)·US(`prism-us/
  us_stock_tracking_agent.py:2242-2261` + Layer 2 `:2432-2445`)의 시맨틱을 1:1 이관.
- kis_auth(현 `trading/kis_auth.py`)의 전역 가변 인증 상태 → `KisSession` 객체
  스코프로 전환 (계좌별 세션 인스턴스, 전역 env 오염 제거).

## 6. 이벤트 버스

- 최초 구현은 in-process 동기 dispatch (외부 브로커 불필요).
- 구독자 실패는 격리(try/except + 로그) — 현 Redis/GCP publish의
  non-critical 패턴을 규칙으로 승격.
- 이벤트 페이로드에 decision_id, intent_id 포함 → 외부 구독자(GCP 수신측)도
  중복 처리 가능해짐.
- **발행 시점 계약 (07-13 추가 — 결정 필요)**: 현행 publish는 batch 인라인 3곳 +
  `sell_broadcast.py`(루프 매도) 총 4곳이며, 시맨틱은 "**sim 커밋 = 방송 source of
  truth**" (자사 KIS 체결 여부와 무관하게 sim_ok 기준 발행 — Rocky 규칙). §3.1의
  쓰기 순서 교정(broker 접수 확인 후 포지션 확정)과 그대로 합치면 발행 시점이
  뒤로 밀린다. 확정: **구독자 향 시그널 이벤트는 sim 커밋 시점 유지**(구독자 계약
  불변), **원장/reconciliation 향 이벤트(OrderSubmitted 등)는 broker 접수 기준** —
  두 종류를 다른 이벤트로 분리해 발행한다. `sell_broadcast.publish_loop_sell`은
  Phase 6에서 이벤트 버스 구독자로 흡수.
