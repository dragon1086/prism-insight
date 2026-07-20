# Issue #412 Phase 4-b2b-3 — production effect adapters 계획

> 기준: main `f9d2c958` (PR #471 bounded replay core 병합)
> 브랜치: `feature/issue-412-phase4b2b3-adapters`
> 운영 기본값: `POSITION_PENDING_KR_ENABLED=false`
> 배포·gate ON·운영 DB replay: 별도 승인 전까지 금지한다.

## 1. 목적과 완료 경계

PR #470은 KR pending exit의 CLOSED 전이와 JOURNAL/TELEGRAM/REDIS/GCP effect 생성을 같은
transaction에 묶었고, PR #471은 각 effect를 독립적으로 claim/retry/complete하는 bounded replay core를
추가했다. 이번 slice는 production adapter를 그 계약에 연결한다.

완료 조건은 다음과 같다.

1. Journal row는 `exit_intent_id`로 멱등 생성되며 process 재시작에도 중복 LLM 실행/insert를 피한다.
2. Telegram queue의 pending exit 항목은 대응하는 effect id를 보존하고 실제 전송 성공 뒤에만 완료된다.
3. Redis/GCP payload에는 deterministic exit event id가 포함되고 non-empty remote message id가 있을 때만
   해당 effect가 DELIVERED가 된다.
4. batch, hardstop, trend pending exit가 동일한 delivery helper와 성공 판정을 사용한다.
5. Journal/Telegram/Redis/GCP 중 하나의 실패는 이미 커밋된 CLOSED나 다른 effect 성공을 되돌리지 않는다.
   즉 **외부 서비스 실패는 거래 전체 실패가 아니라 해당 effect만 PENDING/DEAD인 부분 실패**다.
6. 실제 별도 OS process 경쟁과 lease 만료 후 재시작에서 effect당 단일 claim과 미완료 effect만 재처리됨을
   검증한다.
7. readiness preflight는 미해결 outbox를 read-only로 탐지하며 gate ON blocker로 판정한다.
8. replay CLI는 기본 dry-run이고 명시적 실행 옵션 없이는 외부 호출이나 DB 상태 변경을 하지 않는다.

## 2. cleanup/구현 원칙

1. 기존 `ExitEffectStore`와 replay core를 확장하고 새 queue/store/dependency를 만들지 않는다.
2. exact effect claim helper를 추가해 즉시 전송 경로가 unrelated backlog를 claim하지 않게 한다.
3. SQLite transaction은 claim/finalize 때만 짧게 잡고 network/LLM 실행 중에는 lock을 보유하지 않는다.
4. batch/hardstop/trend의 중복 Redis/GCP 호출을 공통 `StockTrackingAgent` helper로 모은다.
5. Telegram의 기존 문자열 queue API는 유지하고 parallel effect-id metadata를 lazy-init해 `__new__` 기반
   legacy test double도 깨뜨리지 않는다.
6. 기존 non-pending/US/legacy gate-OFF 경로는 기존 publisher와 queue 동작을 유지한다.
7. 새 dependency와 새 credential key를 추가하지 않는다.
8. 외부 성공 뒤 SQLite finalize 전 crash 가능성 때문에 exactly-once는 주장하지 않는다. Journal은 unique key,
   Redis/GCP는 event id, Telegram은 remote message id와 event reference로 중복 조사 가능성을 높인다.

## 3. 상태 및 실패 계약

- CLOSED commit 이후 adapter 실패는 order intent, position, holdings, trading history를 변경하지 않는다.
- 성공한 effect는 다른 effect 실패와 무관하게 DELIVERED로 남는다.
- timeout, exception, false/None, 빈 remote id는 해당 effect만 bounded backoff로 PENDING에 재예약한다.
- max-attempt 도달 effect만 DEAD가 되며 다른 effect와 거래 자체는 성공 상태를 유지한다.
- 명시적으로 비활성화된 optional Journal은 외부 재시도 대상이 아니므로 adapter가
  `disabled-by-config` 결과로 종결하고 audit에서 식별 가능하게 한다.
- Telegram token/chat id, Redis, GCP 설정이 없으면 성공으로 가장하지 않고 해당 effect를 미해결로 둔다.
- readiness에서는 PENDING, IN_PROGRESS, DEAD를 모두 gate ON blocker로 본다.

## 4. TDD slices

### Slice A — exact delivery core

- exact effect id/type/owner claim, 이미 DELIVERED인 row skip, active lease 충돌 skip.
- success/failure/cancellation 전이와 Redis/GCP remote id 필수 계약.
- handler 실행 중 DB transaction 미보유.

### Slice B — Journal 멱등성

- nullable `exit_intent_id` migration과 partial unique index의 기존 DB 호환.
- 같은 exit intent 재호출은 LLM/principle extraction과 INSERT를 반복하지 않음.
- unique 경쟁 시 기존 row를 성공으로 인정.
- 기존 caller가 id 없이 만드는 journal 동작 보존.

### Slice C — Telegram queue linkage

- pending exit queue item만 effect id를 보존하고 다른 message와 metadata 정렬 유지.
- bot/chat id 없음, 전송 exception, split message 일부 실패는 DELIVERED 처리하지 않음.
- 실제 primary-channel send 성공 뒤 remote Telegram message id 기록.
- batch run-end flush에서 여러 exit의 성공/실패를 effect별로 독립 기록.

### Slice D — Redis/GCP와 caller 통합

- publisher payload에 `event_id`를 optional로 추가해 기존 caller payload를 보존.
- batch/hardstop/trend pending 경로가 공통 helper를 사용.
- Redis 실패/GCP 성공 및 반대 조합에서 성공 effect만 DELIVERED.
- gate-OFF/legacy path는 기존 broadcast 동작 유지.

### Slice E — restart/readiness/CLI

- `multiprocessing` spawn 기반 두 process 경쟁에서 effect당 한 claim.
- 첫 process가 finalize 전 종료한 뒤 lease 만료 시 두 번째 process가 미완료 effect만 전달.
- readiness는 outbox table 누락을 unknown, 미해결 effect를 blocker, 전부 DELIVERED를 clean으로 판정.
- audit/replay 출력은 raw account, token, credential, message body를 노출하지 않음.
- replay CLI는 default dry-run이며 execute mode도 bounded limit/effect filter를 요구.

## 5. `.env` 계약

PR #470과 PR #471의 `.env*` diff는 비어 있으며 새 key는 없다. 이번 adapter slice도 새 key를 추가하지 않고
기존 설정을 재사용한다.

- `TELEGRAM_BOT_TOKEN`: Telegram Bot 인증 토큰. 값이 없으면 TELEGRAM effect는 성공 처리하지 않는다.
- `TELEGRAM_CHANNEL_ID`: 기본 전송 대상 chat/channel id. hardstop/trend의 기존 `CHAT_ID` override가 있으면
  그것을 우선하고, 없으면 이 값을 사용한다.
- `TELEGRAM_CHANNEL_ID_EN`, `TELEGRAM_CHANNEL_ID_JA`, `TELEGRAM_CHANNEL_ID_ZH`,
  `TELEGRAM_CHANNEL_ID_ES`: 기존 번역 채널용 optional id. primary TELEGRAM effect의 성공 기준은 기본 채널이다.
- `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`: Redis REST publisher 접속 정보. 둘 중 필요한 값이
  없거나 publisher가 message id를 반환하지 않으면 REDIS effect는 미완료다.
- `GCP_PROJECT_ID`, `GCP_PUBSUB_TOPIC_ID`, `GCP_CREDENTIALS_PATH`: GCP Pub/Sub 대상과 credential 경로.
  publish 성공으로 non-empty message id를 받아야 GCP effect가 완료된다.
- `ENABLE_TRADING_JOURNAL`: 기존 Journal 기능 스위치. false이면 거래 CLOSED는 유지되고 JOURNAL effect는
  `disabled-by-config`로 종결한다.
- `POSITION_PENDING_KR_ENABLED`: KR pending 주문/exit gate. 이번 PR은 기본값 false를 바꾸거나 운영에서
  활성화하지 않는다.

토큰, credential JSON, 실제 channel/account 값은 commit·로그·audit에 기록하지 않는다.

## 6. 비목표와 안전선

- 운영 서버 배포, bot restart, cron 등록, 운영 DB replay, gate ON을 하지 않는다.
- 미국/legacy 즉시 매매 경로를 pending state machine으로 전환하지 않는다.
- 외부 provider가 지원하지 않는 exactly-once를 보장한다고 주장하지 않는다.
- 미해결 DEAD effect를 자동으로 무한 재시도하거나 자동 삭제하지 않는다.
- readiness blocker가 하나라도 있으면 gate ON을 권장하지 않는다.

## 7. 검증과 PR 완료 조건

1. 신규 exact delivery, journal, Telegram, publisher, caller, multiprocessing, readiness/CLI 테스트.
2. 기존 KR pending entry/exit, batch, hardstop, trend, journal, publisher, readiness 회귀.
3. Ruff, format check, Python compile, Bandit, `git diff --check`.
4. GitHub Actions와 repository check를 확인하고 실패를 해소한 뒤 병합.
5. `tasks/handoff.md`에 PR/commit/검증/운영 미실행/환경변수 무변경을 기록.
