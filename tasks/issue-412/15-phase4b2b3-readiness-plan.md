# Issue #412 Phase 4-b2b-3 — KR gate 활성화 readiness 계획

> 기준: main `2bfc9162` (Phase 4-b2b-2 gate OFF 배포 완료)
> 브랜치: `feature/issue-412-phase4b2b3-readiness`
> 운영 기본값: `POSITION_PENDING_KR_ENABLED=false`
> 활성화: 이 작업에서는 금지. 별도 사용자 승인과 무거래 창이 필요하다.

## 1. 목적과 완료 경계

Phase 4-b2b-3 활성화 판단 전에 실행할 **mutation 없는 alert-only KR preflight**를 만든다.
preflight는 환경, crontab, 로컬 원장, legacy holdings, KIS 현재 미체결 SELL을 읽어
`ready`, `blocked`, `unknown` 중 하나를 JSON으로 보고한다.

이번 slice의 완료 조건은 다음과 같다.

1. `.env`/현재 process env와 모든 active cron inline에서
   `POSITION_PENDING_KR_ENABLED`가 unset 또는 명시적 false인지 확인한다.
2. KR position의 `PENDING_ENTRY`, `PENDING_EXIT`, `EXIT_UNKNOWN`, `ENTRY_FAILED`가 모두 0인지
   확인하고, comparator의 `failed_exit_linked_open_positions`도 0인지 확인한다.
3. 기존 `PositionStore.compare_legacy_positions("KR")` 결과가 완전 match인지 확인한다.
4. `stock_holdings`의 같은 account/ticker 다중 row(피라미딩)를 활성화 blocker로 보고한다.
5. accepted KR SELL broker order의 broker order id 누락 및 현재 KIS open SELL과의 연결 불일치를
   경고한다. 이 정보로 FILLED/CANCELLED/REJECTED를 추정하거나 상태를 바꾸지 않는다.
6. KIS 조회 실패, malformed response, 미처리 pagination, crontab/DB 읽기 실패는 PASS가 아니라
   `unknown`과 비정상 종료(exit 2)로 보고한다.
7. 정상 readiness 위반은 `blocked`와 exit 1, 모든 검사가 깨끗할 때만 `ready`와 exit 0이다.

## 2. 비목표와 절대 안전선

- `POSITION_PENDING_KR_ENABLED` 값을 쓰거나 활성화하지 않는다.
- `positions`, `order_intents`, `broker_orders`, legacy holdings/history를 수정하지 않는다.
- KIS 주문, 정정, 취소를 호출하지 않는다. 현재 정정취소가능 주문 조회만 사용한다.
- open-order 목록에서 사라진 주문을 FILLED/CANCELLED/REJECTED로 분류하지 않는다.
- intent/position/legacy 자동 보상 또는 수동 복구 명령을 추가하지 않는다.
- durable CLOSED 이후 journal/Redis/GCP replay, 다중 프로세스 통합 계약, execution history 기반
  reconciliation은 다음 slice/Phase 5로 남긴다.
- hardstop/trend lifecycle 중복 공통화는 별도 회귀 PR 전까지 건드리지 않는다.

## 3. cleanup/refactor 계획

코드 수정 전 현재 동작을 테스트로 잠그고, 새 추상화는 읽기 경계에만 둔다.

1. `DomesticStockTrading.get_revisable_orders()`의 기존 `list`/실패 시 `[]` 계약은 유지한다.
2. 같은 KIS 응답을 해석하되 성공 여부를 잃지 않는
   `get_revisable_orders_checked() -> (authoritative, rows)`를 추가한다.
3. 기존 public wrapper는 checked 결과의 rows만 반환하도록 최소 위임하되, 정상/실패/부분 파싱
   결과가 기존과 같음을 회귀로 고정한다.
4. preflight의 DB 판정은 순수 함수로 분리하고 CLI는 read-only SQLite URI로만 연다.
5. 계좌 번호는 출력하지 않고 기존 `account_fingerprint()`만 사용한다.
6. 기존 comparator, feature gate truthy 규칙, KIS account resolver를 재사용하며 새 dependency/table은
   추가하지 않는다.

## 4. 판정 계약

우선순위는 `unknown > blocked > ready`다.

- `unknown`
  - `.env`, active crontab, SQLite, KIS 계좌 설정/인증/조회 중 하나라도 신뢰할 수 없음.
  - KIS output이 list가 아님, row 필수 필드/수량/side가 malformed, 응답에 다음 페이지가 있음.
- `blocked`
  - gate가 어느 source에서든 truthy 또는 비표준 값.
  - 대상 position 상태/failed-exit link/comparator mismatch/pyramiding이 하나라도 존재.
  - accepted SELL의 broker id 누락, accepted SELL↔현재 open SELL 불일치, 현재 open SELL의 ledger 누락.
  - 현재 KIS open SELL이 하나라도 남아 있음(연결이 맞아도 accepted-but-unfilled 활성화 blocker).
- `ready`
  - 모든 source를 authoritative하게 읽었고 위 blocker가 전부 0.

accepted SELL이 현재 open 목록에 없다는 사실은 상태 추정 근거가 아니다. preflight는 해당 주문을
`accepted_sell_not_currently_open`으로만 노출하고 `broker_orders`나 `positions`를 갱신하지 않는다.

## 5. TDD slices

### Slice A — checked KIS inquiry

- 정상 empty/list 응답은 authoritative.
- API failure/exception, non-list output, malformed row, pagination은 non-authoritative.
- legacy `get_revisable_orders()` 반환은 기존과 동일.

### Slice B — pure/read-only database audit

- clean comparator + 상태 0 + non-pyramided holdings는 통과.
- 각 PENDING/UNKNOWN/FAILED 상태, failed-exit-linked OPEN, comparator mismatch를 개별 검출.
- account/ticker별 pyramiding 검출과 account fingerprint redaction.
- broker id 누락, accepted↔open 양방향 mismatch 검출.
- audit 전후 SQLite `total_changes == 0` 및 DB bytes 불변.

### Slice C — CLI/result aggregation

- env/process/active cron 어느 하나라도 truthy면 blocked.
- commented cron은 무시하고 모든 active inline assignment를 검사.
- invalid gate value는 blocked, crontab/KIS/DB read failure는 unknown.
- exit code 0/1/2와 JSON schema 고정.

## 6. 검증과 후속 활성화 gate

- 로컬 실행 명령:
  `python tools/check_kr_pending_readiness.py --db-path stock_tracking_db.sqlite`
- 로컬 구현 검증(2026-07-19): 관련 259 passed, 신규 파일 Ruff/format, 변경 파일 compile,
  `git diff --check` 통과. 운영 DB/KIS 실조회, PR/CI/배포는 아직 수행하지 않았다.
- 신규/관련 pytest, Ruff, `py_compile`, `git diff --check` 통과.
- 실제 운영 DB 검증 시에도 read-only URI와 KIS inquiry 외 호출 0을 확인한다.
- 양 서버 `.env`와 모든 active cron inline이 OFF이고, preflight `ready`가 확인돼도 이 PR에서는
  gate를 켜지 않는다.
- 실제 ON 전에 durable CLOSED 이후 외부효과 outbox/replay와 batch↔hardstop↔trend 다중 프로세스
  통합 테스트, operator recovery runbook을 별도 구현·검증하고 사용자 승인을 받는다.

## 7. 롤백 원칙

이 slice는 gate OFF/read-only이므로 문제 시 새 preflight CLI만 제거한다. checked inquiry는 기존
public list 계약을 보존해야 하며, 회귀 실패 시 production caller 변경 없이 되돌린다.
