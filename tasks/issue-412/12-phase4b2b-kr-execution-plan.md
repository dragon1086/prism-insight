# Issue #412 Phase 4-b2b 실행 계획 — KR PENDING write-ahead

> 기준: main `de74af0c` (Phase 4-b2a 배포 완료)
> 운영 기본값: `POSITION_PENDING_KR_ENABLED=false`
> 최우선 원칙: 중복·유령 주문을 막기 위해 주문 결과가 불명확하면 자동 재주문하지 않는다.

## 1. 범위와 비목표

이번 단계는 KR batch BUY/SELL, enhanced BUY, hardstop SELL, trend-exit SELL을
동일한 시장 단위 gate 아래 PENDING lifecycle로 연결한다. legacy holdings/history는 계속
유일한 판단 read source다.

비목표:

- US 주문 경로와 `us_pending_orders` 변경
- positions read switch
- 체결·부분체결 추정 또는 reconciliation
- hardstop/trend owner-lock 통합
- 운영 gate 활성화

코드는 배포 가능하게 만들되 이번 PR과 배포에서는 gate를 켜지 않는다.

### 안전한 PR 분할

1. **4-b2b-0 기반, 운영 동작 OFF**: originating store 주입, exit quarantine,
   authoritative 3상태 KIS holding lookup만 추가한다.
2. **4-b2b-1 KR ENTRY, flag OFF**: 일반/enhanced BUY를 write-ahead로 연결한다.
3. **4-b2b-2 KR EXIT 전체, flag OFF**: batch/hardstop/trend SELL을 한 lifecycle로 연결하고
   loop state를 `SOLD/HOLDING/QUARANTINED`로 교정한다.
4. **4-b2b-3 운영 활성화**: 무거래 창에서 별도 승인·검증 후 시장 gate를 한 번에 켠다.

일부 KR caller만 gate ON으로 전환하는 배포는 금지한다.

## 2. 실패·재시도 정책

### 명시적 `FAILED`

- broker가 명시적으로 거절한 경우에만 해당한다.
- ENTRY는 같은 transaction에서 신규 legacy holding을 삭제하고 position을 `ENTRY_FAILED`로 확정한다.
- EXIT는 legacy holding/history를 그대로 두고 position을 `OPEN`으로 되돌린다.
- subscriber publish와 일반 매수·매도 Telegram 메시지는 보내지 않는다.
- CRITICAL 운영 알림을 한 번 보낸다.
- **자동 재주문은 하지 않는다.** 현재 position identity idempotency와 실패 intent linkage를
  그대로 보존하여 다음 batch/loop도 중복 주문하지 못하게 한다.
- 운영자는 KIS 계좌가 미접수임을 확인한 뒤 수동 주문하거나, 후속 Phase 5의 감사 가능한
  reconciliation/new-attempt 도구를 사용한다. 자동 retry 횟수·cooldown을 이번 단계에 추가하지 않는다.

### `UNKNOWN` 또는 coroutine cancellation

- 자동 재주문 금지.
- EXIT는 legacy holding을 유지하고 `EXIT_UNKNOWN`으로 격리한다.
- ENTRY는 legacy holding과 `PENDING_ENTRY`를 유지한다.
- CRITICAL 알림에 market, symbol, side, intent id, 안전한 account fingerprint, action을 포함한다.
- raw broker payload, token, 계좌 원문은 알림이나 로그에 포함하지 않는다.

### `QUEUED`

- KR broker가 접수한 예약주문은 정상 `SUBMITTED`로 분류되므로 `QUEUED`는 예상 외 상태다.
- position은 PENDING으로 유지하고 publish하지 않으며 CRITICAL 알림 후 자동 재주문하지 않는다.
- 현재 KR에는 queued-intent continuation consumer가 없다. domestic broker adapter가 `QUEUED`를
  생성하지 않는 계약을 테스트로 고정하며, 이 계약이 깨지면 gate 활성화를 금지한다.

## 3. cleanup/refactor 계획

기존 public `buy_stock()`/`sell_stock()` bool 계약과 gate=false 경로를 먼저 테스트로 잠근다.
그다음 한 smell만 처리하는 작은 단계로 진행한다.

1. feature gate와 의존성 검증을 추가한다.
   - pending gate=true인데 position shadow가 false면 broker 전에 fail closed한다.
2. 기존 legacy BUY의 DB INSERT와 메시지 생성을 분리한다.
   - gate=false는 기존 commit/message 순서를 그대로 사용한다.
   - gate=true는 holding INSERT + intent CREATED + PENDING_ENTRY를 한 transaction에 commit한다.
3. 기존 legacy SELL의 DB close와 후속 journal/message 생성을 분리한다.
   - gate=false는 기존 simulator-first 순서를 그대로 사용한다.
   - gate=true는 intent CREATED + OPEN→PENDING_EXIT을 먼저 commit하고 broker `SUBMITTED` 뒤에만
     history INSERT + holding DELETE + CLOSED를 한 transaction으로 확정한다.
4. 네 KR caller가 중복 구현하지 않도록 StockTrackingAgent의 좁은 private lifecycle helper를 재사용한다.
5. generic CRITICAL lifecycle alert를 기존 `telegram_config.py` 패턴으로 추가한다.
6. broker 결과 저장 또는 legacy finalize 실패를 위한 좁은 exit quarantine API를 먼저 추가한다.
   - intent가 `UNKNOWN`, `SUBMITTING`, `SUBMITTED`, 예상 외 `QUEUED`여도 broker 접수 여부를
     로컬에서 확정할 수 없으면 `PENDING_EXIT`을 `EXIT_UNKNOWN`으로 격리할 수 있어야 한다.
   - 정상 `FAILED` 보상이나 정상 `SUBMITTED` close를 대신하지 않고 CRITICAL 경로에서만 사용한다.

새 dependency와 schema table은 추가하지 않는다. 가능한 경우 기존 `IntentStore`, `PositionStore`,
`ExecutionService` API를 그대로 사용하며, 실제 caller 연결에 꼭 필요한 최소 파라미터만 확장한다.
단, 운영자 승인 retry와 durable alert outbox는 별도 schema·runbook 설계가 필요하므로
4-b2b-0에서 성급히 추가하지 않고 gate 활성화 전 별도 안전 PR로 확정한다.

## 4. 상태 순서

### ENTRY

```text
BEGIN IMMEDIATE
  holding INSERT
  intent CREATED reserve
  position PENDING_ENTRY
COMMIT
broker pre-reserved execute
  SUBMITTED -> transaction: position OPEN -> buy message/publish
  FAILED    -> transaction: holding DELETE + ENTRY_FAILED -> CRITICAL
  UNKNOWN   -> PENDING_ENTRY 유지 -> CRITICAL
  QUEUED    -> PENDING_ENTRY 유지 -> CRITICAL
```

### EXIT

```text
BEGIN IMMEDIATE
  intent CREATED reserve
  position OPEN -> PENDING_EXIT
COMMIT
broker pre-reserved execute
  SUBMITTED -> transaction: history INSERT + holding DELETE + position CLOSED
               -> sell message/journal/publish
  FAILED    -> transaction: position OPEN, legacy 불변 -> CRITICAL
  UNKNOWN   -> transaction: position EXIT_UNKNOWN, legacy 불변 -> CRITICAL
  QUEUED    -> PENDING_EXIT 유지, legacy 불변 -> CRITICAL
```

broker 호출 중에는 SQLite transaction을 열어 두지 않는다.

## 5. TDD gate

### gate=false 회귀

- KR batch/enhanced BUY: simulator commit → broker → Redis → GCP 순서와 횟수 불변.
- KR batch/hardstop/trend SELL: simulator close → broker → Telegram/publish 순서와 횟수 불변.
- 기존 bool 반환, pyramiding 수량, sold/buy count 불변.

### gate=true ENTRY

- prepare transaction 실패·lock·disk write 실패 시 holding/intent/position 0, broker 0.
- broker `SUBMITTED`만 OPEN + 메시지/publish 각 1회.
- `FAILED`는 holding 삭제 + ENTRY_FAILED, publish 0, alert 1.
- timeout/exception/cancel은 holding + PENDING_ENTRY 유지, publish 0, alert 1, 후속 broker 0.

### gate=true EXIT

- 두 connection 경쟁 시 하나만 PENDING_EXIT claim하고 broker도 1회.
- `SUBMITTED`만 history 1 + holding delete + CLOSED + publish/Telegram 각 1회.
- `FAILED`는 OPEN + legacy/history 불변, publish 0, alert 1, 자동 재시도 0.
- timeout/exception/cancel은 EXIT_UNKNOWN + legacy/history 불변, publish 0, alert 1.
- broker 성공 후 legacy/CLOSED transaction 실패는 EXIT_UNKNOWN과 CRITICAL을 남기며 publish 0.
  intent 결과 저장 실패로 `SUBMITTING`, 저장 성공 후 finalize 실패로 `SUBMITTED`인 두 경우 모두
  exit quarantine이 가능해야 한다.
- KIS qty=0 local-flat은 broker 0으로 legacy close와 CLOSED가 한 transaction에서 완료된다.
- KIS 잔고 응답 헤더가 연속조회(`tr_cont=M/F`)를 알리면 첫 페이지만 보고 FLAT으로
  확정하지 않고 UNKNOWN으로 차단한다. legacy `get_portfolio()`의 기존 첫 페이지 반환은 유지한다.

## 6. 리뷰·배포 gate

- 독립 architecture, SQLite/concurrency, code, test review blocker 0.
- Python 3.10/3.11/3.12 CI + Codacy green.
- db-server Python 3.11 운영동등 worktree에서 실제 cron command startup/import와 관련 회귀 통과.
- app-server `su - prism` import smoke와 bot 무중단 확인.
- 배포 후에도 `POSITION_PENDING_KR_ENABLED`는 미설정/false로 유지한다.
- 별도 활성화 전 PENDING/UNKNOWN 0, 거래 프로세스 0, rollback runbook과 첫 배치 모니터를 준비한다.
