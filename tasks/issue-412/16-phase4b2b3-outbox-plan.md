# Issue #412 Phase 4-b2b-3 — durable CLOSED effect outbox 계획

> 기준: main `0499d096` (PR #469 readiness preflight 병합)
> 브랜치: `feature/issue-412-phase4b2b3-outbox`
> 운영 기본값: `POSITION_PENDING_KR_ENABLED=false`
> 활성화·배포: 이 작업에서는 금지. 별도 사용자 승인과 무거래 창이 필요하다.

## 1. 문제와 확인된 취소 구간

KR pending exit는 broker `SUBMITTED` 뒤 한 SQLite transaction에서 legacy holding/history와
`positions.status=CLOSED`를 확정한다. 그러나 다음 효과는 commit 뒤 process memory/외부 서비스에서
순차 실행된다.

1. trading journal 생성
2. Telegram message queue 적재·flush
3. Redis sell signal publish
4. GCP Pub/Sub sell signal publish

batch, hardstop, trend-exit 모두 CLOSED 이후 `CancelledError`, process crash, transport failure가 나면
position은 올바르게 CLOSED/SOLD로 남지만 어떤 효과가 누락됐는지 내구적으로 알 수 없다. 현재
회귀 테스트도 CLOSED가 quarantine으로 되돌아가지 않는지만 검증하며 누락 효과의 복구 근거는
검증하지 않는다.

Redis/GCP publisher는 성공 시 message id, 실패·미설정 시 `None`을 반환한다. 반면 journal은 exit
intent 고유키가 없고 Telegram은 전송 후 DB 완료 기록 전 crash를 제거할 exactly-once API가 없다.
따라서 전체 replay를 한 번에 연결하지 않고 원자적 이벤트 기반부터 작게 잠근다.

## 2. 이번 foundation slice의 완료 경계

이번 slice는 **외부 행동을 바꾸지 않는 atomic outbox foundation**까지만 구현한다.

1. additive SQLite `exit_effect_outbox` schema와 transaction-neutral store를 추가한다.
2. effect identity는 `(intent_id, effect_type)`로 결정론적으로 고정한다.
3. `JOURNAL`, `TELEGRAM`, `REDIS`, `GCP` 네 effect가 동일한 versioned payload를 가진다.
4. `_complete_pending_kr_exit()`가 history 삭제/position CLOSED와 같은 caller-owned transaction에서
   네 effect를 적재한다.
5. position ledger 초기화와 pending readiness 경계에서 schema를 idempotent하게 보장한다.
6. CLOSED 이후 cancellation이 발생해도 네 PENDING effect가 남고, CLOSED transaction rollback 시
   effect도 하나도 남지 않음을 회귀 테스트로 고정한다.
7. 동일 intent/effect의 동일 payload 재적재는 중복 없이 허용하고, 다른 payload 충돌은 실패시킨다.

## 3. 비목표와 절대 안전선

- `POSITION_PENDING_KR_ENABLED`를 켜거나 운영 `.env`/cron을 수정하지 않는다.
- 운영 DB, KIS, Telegram, Redis, GCP를 호출하지 않는다.
- 이번 slice에서 outbox row를 claim, dispatch, retry, `DELIVERED` 처리하지 않는다.
- 기존 journal/Telegram/Redis/GCP 즉시 실행 순서와 public 반환 계약을 바꾸지 않는다.
- PENDING row가 있다고 즉시 전송 누락이라고 단정하지 않는다. 기존 즉시 경로가 아직 완료 상태를
  기록하지 않으므로 foundation 단계에서는 durable recovery candidate 의미만 가진다.
- journal exactly-once, Telegram at-least-once 운영 정책, Redis/GCP message-id 완료 기록은 다음
  replay slice에서 테스트 우선으로 결정한다.
- hardstop/trend duplicated orchestration refactor와 gate ON은 하지 않는다.
- 계좌 식별자는 외부 출력이나 로그에 기록하지 않는다.

## 4. cleanup/구현 계획

1. `prism_core`의 기존 `PositionStore`처럼 caller transaction을 소유하지 않는 작은 store를 만든다.
2. schema creation은 additive `CREATE TABLE/INDEX IF NOT EXISTS`만 사용하고 migration dependency를
   추가하지 않는다.
3. JSON payload는 canonical serialization으로 저장해 동일 이벤트 재적재 비교를 안정화한다.
4. `_PreparedKrExit`에 이미 있는 값을 재사용하고 별도 DTO/외부 dependency를 추가하지 않는다.
5. `_complete_pending_kr_exit()`의 기존 history/delete/CLOSED/message 동작은 유지하고 transaction
   내부에 enqueue 한 단계만 추가한다.
6. 테스트 fixture만 새 schema를 보장하도록 갱신하고 production 전송 caller는 건드리지 않는다.

## 5. payload v1 계약

payload는 향후 각 effect dispatcher가 동일한 CLOSED 사실을 재구성할 수 있도록 다음 필드를 가진다.

- `version`, `event_id`(exit intent id), `market`, `source`
- `account_id`, `account_name`, `symbol`, `company_name`
- `sell_price`, `buy_price`, `profit_rate`, `holding_days`, `sell_reason`, `exit_kind`
- `message`, `journal_stock_data`

DB 내부 account identity는 올바른 account scope 재생에 필요하지만 CLI/로그에서는 fingerprint 또는
redaction만 허용한다. payload는 생성 후 수정하지 않으며 effect별 delivery metadata만 별도 column에
기록한다.

## 6. TDD slices

### Slice A — store contract

- active caller transaction 밖 enqueue 거부.
- 네 effect 원자 적재와 결정론적 id/상태 확인.
- 동일 payload 재적재는 0건 추가, 다른 payload는 conflict 오류.
- rollback 뒤 row 0건.

### Slice B — CLOSED transaction integration

- 정상 completion에서 history/CLOSED/outbox 4건이 함께 commit.
- position finalize 또는 outbox enqueue 실패 시 holding/history/CLOSED/outbox가 함께 rollback.
- message queue는 commit 뒤에만 기존과 같이 적재.

### Slice C — post-CLOSED cancellation evidence

- batch post-commit cancellation 뒤 position CLOSED, history 1, outbox PENDING 4 유지.
- broker pre-completion cancellation/FAILED/UNKNOWN에는 outbox 0.
- 이 slice에서는 hardstop/trend fake-agent 테스트를 outbox 구현처럼 꾸미지 않는다. 실제 DB를 공유하는
  다중 process 통합 테스트는 dispatcher가 생기는 다음 slice에서 추가한다.

## 7. 다음 replay slice의 진입 조건

1. Journal row에 exit intent 고유 연결을 추가해 LLM 재실행/DB insert 중복을 방지한다.
2. Telegram은 at-least-once 한계와 operator-visible event id를 명시하고 bool 성공만 완료 처리한다.
3. Redis/GCP는 반환 message id가 있을 때만 개별 effect를 완료 처리한다.
4. lease/attempt/backoff를 가진 bounded replay worker와 read-only audit/dry-run CLI를 구현한다.
5. batch↔hardstop↔trend 세 process가 같은 DB에서 하나의 exit intent/effect set만 만드는 통합 테스트를
   통과한다.
6. readiness preflight가 unresolved outbox를 blocker/unknown 중 올바른 범주로 판정한다.

## 8. 검증과 롤백

- 신규 store unit test, KR pending exit 관련 pytest, hardstop/trend 회귀 테스트를 실행한다.
- Ruff, format check, `py_compile`, `git diff --check`를 통과한다.
- 기존 전송 함수와 운영 설정 diff가 없음을 확인한다.
- 문제 시 additive store/schema와 `_complete_pending_kr_exit()` enqueue만 되돌린다. gate OFF이므로
  production pending exit 동작은 활성화되지 않는다.

구현 검증(2026-07-20): outbox/KR pending entry·exit/hardstop/trend/position/intent/report 관련
`220 passed`, 신규·변경 범위 Ruff, 신규 파일 format check, 변경 Python compile,
`git diff --check`를 통과했다. 테스트 import용 git-ignore 로컬 KIS placeholder 외 운영 설정 변경은 없고,
운영 DB·KIS·Telegram·Redis·GCP 호출, 배포, gate 활성화는 수행하지 않았다.
