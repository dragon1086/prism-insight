# 04. 단계별 마이그레이션 계획 (Strangler)

원칙: 각 Phase는 **독립 배포 가능, 독립 롤백 가능, 서버 demo 검증 통과 후 다음 단계 진입.**
검증 절차의 상세는 05-verification-plan.md.

---

## Phase 0 — 합의 (코드 변경 없음)

- [x] 이 디렉토리의 계획/설계/검증 문서 작성
- [ ] 이슈 #412에 검토 코멘트 게시, 방향 합의
- 완료 조건: 이슈 코멘트에 방향 합의 (또는 이견 반영해 문서 수정)

## Phase 1 — 순수 함수 추출 (무위험)

대상: 로직 변화 없이 옮기기만 하면 되는 것들.
- `_normalize_decision`, `_parse_price_value`, `_safe_number_conversion`
  → `prism_core/parsing.py`
- `compute_fractional_sell_quantity` 및 #288 스냅샷 분배 로직 → 순수 함수화
- 주문 시간대 판정 (KST/ET 거래소 timezone 기준) → `time_windows.py`
- 완료 조건:
  - 추출된 함수 전부에 단위 테스트 (기존 동작 고정, #288 over-sell 케이스 포함)
  - 기존 호출부는 import 경로만 변경, diff에 로직 변화 없음
  - 서버 demo 1일 운영에서 기존과 동일 동작

## Phase 2 — ExecutionService chokepoint (동작 불변 래핑)

- `AsyncTradingContext` 직접 호출 4곳(KR 매수/매도 × base/enhanced)을
  `ExecutionService.execute_buy/execute_sell` 뒤로 이동. **내부는 기존 코드 그대로 위임.**
- 이때 함께: US의 중복 SELL 가드(fresh snapshot)를 ExecutionService에 구현해
  KR에도 적용 (현재 KR에는 없음 — 2026-07-01 MU 사고의 KR 재발 방지)
- 완료 조건:
  - 주문 경로 grep 검사: `AsyncTradingContext` 사용처가 ExecutionService 내부 1곳뿐
  - 중복 SELL 회귀 테스트 (동시 2 프로세스 시나리오) 통과
  - 서버 demo 3일 운영: 주문 결과가 리팩토링 전과 동일 패턴

## Phase 3 — OrderIntent 영속화 + 쓰기 순서 교정

- `order_intents`, `broker_orders` 테이블 신설 (기존 테이블 무변경)
- ExecutionService가 ① intent 저장(CREATED, idempotency unique) → ② broker 호출
  → ③ 결과로 상태 갱신 (SUBMITTED/FAILED/UNKNOWN)
- 주문 실패 시: 매수는 원장 보상 삭제(현행 buy_stock 커밋 취소), 매도는 원장 복원
  + `OrderFailed` 이벤트로 Telegram 알림 (**현재는 로그만 남기고 침묵 — 이걸 제거**)
- UNKNOWN 처리: `list_executions` 대조 후 확정, 미확정 시 사람 알림
- 완료 조건:
  - 강제 실패 주입 테스트(모의 broker 예외/타임아웃)에서 원장-intent 정합 유지
  - idempotency: 같은 decision_id 재실행 시 두 번째 주문이 차단되는 테스트
  - 서버 demo에서 정상 매수/매도 + 인위적 실패 시나리오 각 1회 검증

## Phase 4 — 포지션 상태기계 (가장 위험, shadow 병행 검증)

- `positions` 신설, PENDING_ENTRY→OPEN→PENDING_EXIT→CLOSED 전이
- **병행 기록 기간**: 기존 `stock_holdings`(insert/delete)와 신규 `positions`를
  동시에 기록하되, 판단 루프는 여전히 기존 테이블을 읽는다.
- 매일 두 테이블 대조 스크립트 실행 → N일(최소 5 거래일) 무불일치 후 읽기 전환.
- 읽기 전환 후에도 기존 테이블 기록은 1주 유지 (즉시 롤백 경로).
- 완료 조건: 5 거래일 연속 대조 무불일치 → 읽기 전환 → 3 거래일 정상 → 구기록 중단

## Phase 5 — BrokerAdapter 추출 + Reconciliation (alert-only)

- KIS 국내 어댑터를 BrokerAdapter Protocol로 정리, **체결 조회 메서드 구현**
- 예약주문 익일 체결 확인 로직 흡수
- reconciliation job: positions(OPEN) vs `get_portfolio()` 대조.
  빈 응답 가드 필수. **자동 수정 금지, 불일치는 `ReconciliationMismatch` 이벤트
  → Telegram 알림만** (자동 보정은 운영 신뢰 쌓인 뒤 별도 결정)
- `kis_auth` 전역 상태 → `KisSession` 객체 스코프 전환
- 완료 조건: 인위적 불일치(demo 계좌에서 수동 주문)를 job이 탐지·알림

## Phase 6 — 코어/어댑터 패키지 분리 + prism-us 흡수

- 이벤트 버스 도입, Telegram/Redis/GCP/일지/Firebase를 구독자로 이동
- `prism_core` / `prism_kr` / `prism_us` 패키지 분리
- MarketProfile, PortfolioPolicy 도입 (하드코딩 상수 제거)
- Enhanced 상속 → Strategy 합성 전환
- **prism-us의 us_stock_tracking_agent 포크를 코어+어댑터 조합으로 대체**
- 완료 조건 (= 이식성 합격 기준):
  - prism-us 전용 tracking 코드가 프로파일/어댑터/전략 등록 수준으로 축소
  - KR/US 모두 코어 엔진 하나로 서버 demo 5 거래일 무사고
  - 이 시점에 이슈 #412의 "타 프로젝트 이식" 요구는 문서가 아니라
    동작하는 코어 패키지로 충족된다

---

## Phase 간 공통 규칙

- 한 Phase = 한 PR. PR에는 해당 Phase 문서 갱신 포함.
- 배포는 항상 demo 계좌 모드 먼저. live 전환은 Phase별 완료 조건 + Rocky 승인.
- 회귀 케이스 3종(MU 중복 SELL, #288 over-sell, 빈 portfolio)은 Phase 2부터
  CI에 상주.
- 문제 발생 시: 해당 Phase revert → 이전 Phase 상태로 복귀 (각 Phase가
  이전 상태와 호환되도록 DB 변경은 additive-only).
