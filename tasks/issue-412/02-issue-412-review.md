# 02. 이슈 #412 설계 검토

결론: **방향 동의, 실행 계획 수정 필요.** 이슈의 현재 코드 서술은 정확하다
(라인 참조, TR ID, 운영 주의점 모두 실제 코드와 일치). 다만 목표 설계는
이 프로젝트가 사고로 배운 교훈이 빠진 일반론이며, 가장 어려운 세 문제
(체결 추적, 진짜 idempotency, 마이그레이션)가 비어 있다.

## A. 그대로 채택

- Buy/Sell Agent 역할 분리 + "매수 시나리오는 매도 Agent의 입력" 데이터 계약
  (현재 코드도 `stock_holdings.scenario` JSON으로 이미 이렇게 동작)
- Agent 판단 → OrderIntent 변환 → Broker Adapter 실행의 3계층
- demo/shadow/live 모드 구분, 프롬프트 버전 관리
- "KIS 주문번호는 체결 보장이 아니라 접수 확인" 원칙
- 운영 주의점 목록 (예약주문 fallback 금지, 미국 시장가 매도 제약, 빈 portfolio 응답 등)

## B. 채택하되 보완 (설계 허점)

### B-1. BrokerAdapter에 체결/미체결 조회가 없다 — 치명적
이슈 초안의 인터페이스는 `buy / sell / get_position / get_portfolio / cancel_order`뿐.
**주문 상태 조회(get_order_status)와 체결 내역 조회(list_executions)가 없다.**

- 이러면 reconciliation job이 포지션 수준 비교밖에 못 해서 불일치의 원인
  (부분체결? 미체결? 유령주문?)을 특정할 수 없다.
- "주문번호는 접수 확인일 뿐"이라 스스로 정의해놓고, 접수 이후를 추적할 수단이 없다.
- 예약주문(오늘 접수 → 내일 체결)은 특히 치명적. 기존 `us_pending_order_batch.py`가
  푸는 문제를 설계가 무시한다.

→ 보완: `get_order_status()`, `list_executions()` (KIS 주문체결조회
`inquire-daily-ccld` 계열) 추가, pending order 추적을 adapter 책임으로 흡수.

### B-2. idempotency_key가 체크박스 수준
- 키 생성 규칙(무엇이 "같은 주문"인가), 저장/검사 지점, unique 제약 여부 미정의.
- **KIS API에는 client-order-id가 없다.** 타임아웃 후 "나갔는지 모르는 주문"은
  intent 중복 차단으로 해결되지 않는다.

→ 보완: `idempotency_key = decision_id` (Agent 판단 시점에 UUID 부여) +
`order_intents.idempotency_key` unique 제약 + 주문 상태기계
`CREATED → SUBMITTING → SUBMITTED | FAILED | UNKNOWN` +
UNKNOWN은 체결내역 조회로 복구.

### B-3. 원장-주문 쓰기 순서를 결정하지 않음
현재 코드의 실제 버그(원장 선커밋 → 주문 fire-and-forget)에 대해
"주문 실패 시 position 복구 또는 ERROR_RETRY"라는 체크박스 한 줄뿐.

→ 보완: **intent 먼저 영속화, 포지션 전이는 broker 접수 확인 후.**
03-target-design.md의 상태기계로 확정.

### B-4. lock 개념이 프로세스 내/간을 구분하지 않음
`asyncio.Lock`은 이미 존재하지만 프로세스 내부용. orchestrator/tracking/batch가
별도 프로세스(cron)로 도는 현실에서 필요한 것은 **크로스 프로세스 lock**
(SQLite 트랜잭션 or flock). 또한 "전역 KIS env를 lock으로 보호"는 전역 가변
인증 상태라는 결함을 영속화한다 — 근본 해결은 인증 컨텍스트의 객체 스코프화.

### B-5. shadow 모드 시맨틱 미정의
shadow 포지션과 실포지션이 한 positions 테이블에 섞이면 매도 판단이 무엇을
보고 도는지 불명확. → `execution_mode` 컬럼 + 엄격한 필터링으로 정의 (03 참고).

## C. 대체

### C-1. Greenfield 파일 구조 → Strangler 단계 전환
이슈는 `trading/kis/...` 신규 구조 + 일괄 구현을 제안하지만:
- 기존 `trading/` 모듈과 충돌하고, 라이브 데이터(`stock_holdings`,
  `trading_history` 등)와 운영 중인 cron 배치에서 새 스키마로 가는
  마이그레이션 경로가 전혀 없다.
- 이슈가 참조하는 기준 문서 2개(`docs/BUY_SELL_AGENT_SPLIT_DESIGN.md`,
  `docs/BROKER_ORDER_EXECUTION_DESIGN.md`)는 repo에 존재하지 않는다
  (작성자 로컬 트리에만 존재).

→ 대체: 04-migration-plan.md의 Phase 0~6. 각 Phase는 독립 배포·독립 롤백.

### C-2. "타 프로젝트 이식용 기준 구현" → "prism-us 흡수로 이식성 증명"
이식 가능한 코어가 진짜인지는 문서가 아니라 코드로 증명한다:
**prism-us 포크가 프로파일+어댑터 조합으로 대체되면** 제3 프로젝트 이식은
부산물로 달성된다.

## D. 이슈 범위 밖 추가 (이 계획의 확장)

- `StockTrackingAgent` god class 해체 자체 — 이슈는 주문 실행 계층만 다루고,
  판단~원장~알림이 엉킨 2,300줄 클래스는 건드리지 않는다.
- 도메인 이벤트 버스 (Telegram/Redis/GCP/일지를 구독자로)
- `MarketProfile` (KR/US 시장 차이를 데이터로), `PortfolioPolicy` (하드코딩 상수 제거)
- 상속(`Enhanced extends Base`) → 합성(Strategy 플러그인) 전환
- 사고 회귀 케이스의 자동화 테스트 고정 (MU 중복 SELL, #288 over-sell, 빈 portfolio)
- KR에 아직 없는 중복 SELL chokepoint 가드의 이식
