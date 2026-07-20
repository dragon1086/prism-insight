# Issue #412 Phase 4-b2b-3 — exit effect replay core 계획

> 기준: main `0358d004` (PR #470 atomic outbox foundation 병합)
> 브랜치: `feature/issue-412-phase4b2b3-replay`
> 운영 기본값: `POSITION_PENDING_KR_ENABLED=false`
> 배포·gate ON·실제 외부 replay: 이 작업에서는 금지한다.

## 1. 목적과 이번 완료 경계

PR #470은 CLOSED와 같은 transaction에서 JOURNAL/TELEGRAM/REDIS/GCP 후보를 PENDING으로 남긴다.
이번 slice는 여러 process가 같은 SQLite를 사용해도 한 effect만 claim하고, 성공은 effect별로 완료하며,
실패·cancellation은 CLOSED를 건드리지 않고 재시도 가능 상태로 돌려놓는 **bounded replay core**를 만든다.

완료 조건은 다음과 같다.

1. caller-owned `BEGIN IMMEDIATE` 안에서 ready/expired effect를 제한된 개수만 claim한다.
2. claim은 owner와 lease expiry를 기록하고 attempt count를 정확히 1 증가시킨다.
3. 두 connection/process가 경쟁해도 같은 effect를 동시에 claim하지 않는다.
4. Redis/GCP는 비어 있지 않은 remote message id가 있을 때만 DELIVERED가 된다.
5. Journal/Telegram은 명시적 success 결과가 있을 때만 DELIVERED가 된다.
6. handler exception, false/None 결과, `CancelledError`는 effect를 PENDING으로 재예약하고 CLOSED,
   trading_history, legacy holding, position 상태를 수정하지 않는다.
7. max-attempt에 도달한 실패는 DEAD로 격리하며 자동 무한 retry를 금지한다.
8. 기본 dry-run/read-only audit CLI가 status/effect count와 오래된 미완료 row를 account fingerprint로만
   JSON 보고하며 DB bytes를 변경하지 않는다.

## 2. 조사 결론과 분할 이유

- Redis/GCP publisher는 성공 시 message id, 실패·미설정 시 `None`을 반환한다. 따라서 message id가
  없는 호출을 성공으로 기록하면 안 된다.
- batch Telegram은 계좌별 exit 직후가 아니라 `run()` 끝에서 portfolio summary와 함께 queue를 한 번
  flush한다. 현재 queue에는 exit intent linkage가 없어 개별 TELEGRAM effect를 안전하게 완료 처리할 수
  없다.
- hardstop/trend는 per-sell Telegram flush를 하지만 batch와 다른 완료 규칙을 먼저 넣으면 caller drift가
  커진다.
- trading journal에는 exit intent unique key가 없어 LLM 실행/insert 뒤 process crash가 나면 replay가
  중복 row를 만들 수 있다.

따라서 이번 slice는 transport/JournalManager adapter를 연결하지 않는다. 먼저 generic replay core와
read-only audit를 실제 다중 connection 회귀로 잠근 뒤, 다음 adapter slice에서 journal unique linkage,
Telegram queue linkage, Redis/GCP event id/remote id 완료 기록을 같은 계약 위에 연결한다.

## 3. cleanup/구현 계획

1. PR #470의 `ExitEffectStore`를 재사용하고 새 store/ORM/dependency를 만들지 않는다.
2. schema에 이미 있는 `status`, `attempt_count`, `next_attempt_at`, `lease_owner`,
   `lease_expires_at`, `remote_id`, `last_error`, `completed_at`만 사용한다.
3. store는 계속 transaction-neutral하게 유지하고 commit/rollback은 replay runner가 짧게 소유한다.
4. network/LLM handler 실행 중 SQLite transaction이나 write lock을 잡지 않는다.
5. handler timeout은 lease보다 짧게 강제해 정상 runner가 살아 있는 동안 lease-expiry 중복 claim이
   발생하지 않게 한다.
6. generic async runner는 injected handler mapping만 받으며 production credential/config를 import하지 않는다.
7. retry delay는 deterministic bounded exponential backoff로 계산하고 저장 error는 type/name 위주로 제한한다.
8. audit CLI는 SQLite read-only URI와 parameterized query만 사용하고 account id/name/payload message를
   출력하지 않는다.
9. replay는 기존 DB와 outbox table이 없으면 즉시 실패하며 잘못된 경로에 새 SQLite를 만들지 않는다.

## 4. 상태 전이 계약

- `PENDING -> IN_PROGRESS`
  - `next_attempt_at`이 없거나 현재 이하인 row만 claim.
  - lease가 만료된 `IN_PROGRESS`도 재claim 가능.
  - claim 시 attempt count +1.
- `IN_PROGRESS -> DELIVERED`
  - 동일 lease owner만 가능.
  - Redis/GCP는 non-empty remote id 필수.
  - lease/error/next-attempt를 비우고 completed timestamp 기록.
- `IN_PROGRESS -> PENDING`
  - 동일 owner만 가능.
  - bounded backoff의 next-attempt와 redacted error type 기록.
- `IN_PROGRESS -> DEAD`
  - attempt count가 max attempts 이상인 실패만 가능.
  - 수동 조사 전 자동 claim 금지.
- `DELIVERED`와 `DEAD`는 generic runner가 다시 claim하지 않는다.

어떤 effect 전이도 order intent, position, holdings, trading history를 변경하지 않는다.

## 5. TDD slices

### Slice A — store lifecycle

- active transaction 밖 claim/complete/reschedule 거부.
- limit/order/effect filter, future next-attempt 제외.
- owner mismatch, missing Redis/GCP remote id 거부.
- expired lease reclaim과 attempt 증가.
- max-attempt DEAD 격리.

### Slice B — bounded async runner

- network handler 실행 시 DB connection이 transaction을 잡고 있지 않음.
- success는 개별 DELIVERED, false/None/exception은 PENDING.
- cancellation도 재예약 후 원래 `CancelledError` 재전파.
- 두 runner 경쟁에서 effect당 handler 호출 최대 1회.
- 한 effect 실패가 다른 effect 완료를 전체 rollback하지 않음.

### Slice C — read-only audit CLI

- table/status/effect count와 oldest unresolved age/order.
- account fingerprint 외 raw account/payload/message 미노출.
- missing/corrupt schema는 success가 아니라 unknown/exit 2.
- audit 전후 DB bytes 및 `total_changes` 불변.

## 6. 비목표와 안전선

- 실제 Journal/Telegram/Redis/GCP handler를 등록하거나 호출하지 않는다.
- 기존 batch/hardstop/trend immediate effect 순서와 message queue를 변경하지 않는다.
- 기존 PENDING row를 실제 운영 DB에서 claim/replay하지 않는다.
- readiness preflight 판정이나 gate 값을 변경하지 않는다.
- 운영 server 배포, cron 등록, bot 재시작을 하지 않는다.
- exactly-once를 주장하지 않는다. 외부 성공 후 DB DELIVERED 전 crash gap은 adapter별 idempotency가
  추가될 때까지 남는다.

## 7. 다음 adapter slice 진입 조건

1. Journal `exit_intent_id` unique migration과 기존 row 호환을 검증한다.
2. Telegram queue item에 exit effect id를 연결하고 batch run-end flush의 개별 성공/실패를 보존한다.
3. Redis/GCP payload에 deterministic event id를 전달하고 non-empty message id만 완료 처리한다.
4. hardstop/trend/batch가 같은 effect runner helper를 사용하도록 작은 caller별 회귀를 먼저 추가한다.
5. 실제 multi-process integration에서 cancellation 후 재시작이 미완료 effect만 처리함을 검증한다.

## 8. 검증과 롤백

- 신규 store/runner/audit 테스트와 기존 KR pending entry/exit, hardstop, trend, position/intent 회귀를
  실행한다.
- Ruff, format check, `py_compile`, `git diff --check`, Codacy/CI를 통과한다.
- 문제 시 runner/audit와 store lifecycle 메서드만 되돌린다. PR #470의 atomic enqueue와 기존 즉시
  효과 경로에는 영향이 없어야 한다.

구현 검증(2026-07-20): replay/outbox/audit 신규 22개를 포함해 KR pending entry·exit,
hardstop/trend, position/intent/report 관련 **238 passed**. 신규·변경 범위 Ruff 및 format check,
Python compile, Bandit, `git diff --check`를 통과했다. 테스트 import용 git-ignore KIS placeholder 외
운영 설정 변경은 없으며, 실제 DB/outbox claim, Journal/Telegram/Redis/GCP 호출, 배포, cron 등록,
gate 활성화는 수행하지 않았다.
