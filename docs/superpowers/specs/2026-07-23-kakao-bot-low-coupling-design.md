# Kakao 그룹봇 저결합 아키텍처 설계

> 상태: Phase 1 + Phase 3 operational foundation implemented
> 라이브 게이트: Kakao REST/Gateway 실계정 contract smoke 미수행
> 작성일: 2026-07-23
> 선행 문서: `2026-07-22-kakao-group-bot-design.md`

## 1. 목표

카카오 그룹봇을 기존 PRISM-INSIGHT에 추가하되 다음을 보장한다.

1. `telegram_ai_bot.py`를 Kakao 코드가 import하지 않는다.
2. `analysis_manager.py`의 프로세스 내부 Queue와 Telegram 결과 전송 구조를 재사용하지 않는다.
3. 기존 분석·PDF·시그널·가격조회 기능은 명시적인 포트를 통해서만 호출한다.
4. Kakao API, Gateway, 메시지 포맷, 룸 상태는 전용 패키지 안에 격리한다.
5. Kakao 장애나 배포가 Telegram 및 매매 실행 경로에 영향을 주지 않는다.
6. 수신 이벤트와 발송 작업은 재시작 후에도 중복 또는 유실을 통제할 수 있어야 한다.
7. 기본 자동 알림은 **KR 오후 배치** 하나로 제한하고, 다른 시장·세션은 룸별 명시적 구독으로만 활성화한다.

## 2. 비목표

- Telegram 봇 전면 리팩터링
- 기존 매매 파이프라인의 순서 또는 주문 로직 변경
- 미국 시장의 대화형 리포트·예측 기능
- Kakao 외 다른 메신저를 위한 범용 프레임워크 구축
- 분산 시스템 수준의 정확히 한 번 처리 보장

목표는 추상화를 크게 만드는 것이 아니라 Kakao와 Prism 사이에 작고 안정적인 경계를 두는 것이다.

## 3. 핵심 결정

### 3.1 Kakao는 별도 애플리케이션이다

Kakao 봇은 `telegram_ai_bot.py` 안의 기능이나 핸들러가 아니다. 동일 저장소에 위치하지만 독립적으로 실행하고 중단할 수 있는 별도 애플리케이션이다.

권장 실행 단위:

- `kakao-gateway`: Gateway 연결, 이벤트 수신, 명령 라우팅
- `kakao-worker`: 분석 작업, 시그널 집계, 예측 정산
- `kakao-sender`: 영속 outbox 발송
- `kakao-web`: 만료형 PDF 링크 제공

MVP에서는 worker와 sender를 한 프로세스로 합칠 수 있다. 단, 코드 경계와 데이터 계약은 분리한다.

### 3.2 의존 방향은 한 방향이다

허용되는 의존 방향:

```text
Kakao transport adapters
        ↓
Kakao application use cases
        ↓
Kakao domain + ports
        ↑
Prism adapters / persistence adapters
```

금지되는 의존:

```text
kakao → telegram_ai_bot
kakao → analysis_manager.analysis_queue
kakao domain → aiohttp / sqlite3 / Kakao SDK
prism trading core → kakao
```

### 3.3 재사용 대상은 로직이고 런타임 상태가 아니다

재사용:

- 리포트 생성 함수
- PDF 생성 함수
- 종목코드 해석 및 가격조회 기능
- 시그널 payload
- 투자 유의문구

재사용하지 않음:

- Telegram `Update`, `Context`, `chat_id`
- Telegram 인스턴스의 `pending_requests`, `result_queue`
- `analysis_manager.analysis_queue`
- Telegram 메시지 수정 및 파일 전송 UX
- Telegram 사용자 제한 상태

### 3.4 자동 알림의 단위는 시그널이 아니라 배치 캠페인이다

Kakao가 개별 BUY/SELL 메시지나 cron 실행 여부를 보고 배치를 추측하지 않는다. Prism orchestrator가 배치 완료 또는 정책상 휴식을 명시적인 캠페인 이벤트로 발행한다.

지원 슬롯:

- `KR / MORNING`
- `KR / AFTERNOON`
- `US / MORNING`
- `US / AFTERNOON`

룸 구독 기본값:

- `KR / AFTERNOON`: ON
- `KR / MORNING`: OFF
- `US / MORNING`: OFF
- `US / AFTERNOON`: OFF

단, 새로 발견된 룸은 승인 전까지 모든 구독이 비활성이다. 위 기본값은 관리자가 룸을 승인할 때 적용되는 초기 프로필이다.

현재 Market Pulse 정책을 그대로 따른다.

- `UPTREND`, `UNDER_PRESSURE`: KR/US morning과 afternoon 실행
- `CORRECTION`: KR/US morning 휴식, afternoon 실행
- Market Pulse OFF 또는 조회 실패: 분석은 fail-open으로 실행하고 캠페인 국면은 `UNKNOWN`

Kakao는 이 정책을 다시 구현하지 않는다. Prism이 발행한 `COMPLETED` 또는 `SKIPPED` 상태만 소비한다.

## 4. 패키지 구조

```text
kakao_bot/
├── __main__.py
├── config.py
├── domain/
│   ├── models.py
│   ├── commands.py
│   ├── prediction.py
│   └── errors.py
├── application/
│   ├── inbound_service.py
│   ├── report_service.py
│   ├── signal_campaign_service.py
│   ├── prediction_service.py
│   ├── settlement_service.py
│   └── delivery_service.py
├── ports/
│   ├── analysis.py
│   ├── artifacts.py
│   ├── market_data.py
│   ├── signal_feed.py
│   ├── repositories.py
│   └── messenger.py
├── adapters/
│   ├── kakao/
│   │   ├── gateway.py
│   │   ├── rest_client.py
│   │   ├── event_mapper.py
│   │   └── templates.py
│   ├── prism/
│   │   ├── report_adapter.py
│   │   ├── market_data_adapter.py
│   │   └── batch_campaign_mapper.py
│   ├── local_campaign_consumer.py
│   ├── persistence/
│   │   ├── sqlite.py
│   │   └── migrations/
│   └── web/
│       └── report_app.py
└── runtime/
    ├── gateway_main.py
    ├── worker_main.py
    ├── sender_main.py
    └── scheduler_main.py
```

`kakao_bot/domain`과 `kakao_bot/application`은 Kakao REST JSON이나 Prism 내부 객체를 알지 못한다.

## 5. 경계 계약

### 5.1 수신 메시지

```python
@dataclass(frozen=True)
class InboundMessage:
    event_id: str
    sequence: int
    room_id: str
    user_id: str
    nickname: str | None
    text: str
    callback_token: str | None
    occurred_at: datetime
```

Kakao Gateway payload는 `event_mapper`에서 위 모델로 변환한다. application 계층에는 원본 JSON을 전달하지 않는다.

### 5.2 분석 작업

```python
@dataclass(frozen=True)
class AnalysisJob:
    job_id: str
    room_id: str
    user_id: str
    ticker: str
    market: str
    requested_at: datetime

@dataclass(frozen=True)
class AnalysisResult:
    job_id: str
    status: str
    ticker: str
    company_name: str
    summary: str | None
    artifact_id: str | None
    error_code: str | None
```

`AnalysisPort`는 `submit(job)`과 `get_result(job_id)`만 제공한다. Kakao는 리포트 생성 함수나 저장 경로를 직접 알지 못한다.

### 5.3 배치 캠페인 이벤트

```python
@dataclass(frozen=True)
class BatchCampaign:
    campaign_id: str
    market: str
    session: str
    regime: str
    status: str
    trade_date: date
    candidates: tuple[CampaignCandidate, ...]
    skip_reason: str | None
    completed_at: datetime
```

`status`는 `COMPLETED` 또는 `SKIPPED`다. `COMPLETED` 캠페인은 최대 5개의 확정 후보를 포함한다. `SKIPPED`는 Market Pulse 등 정책상 배치 휴식 사유를 포함한다.

MVP의 canonical transport는 **로컬 채널 중립 SQLite queue**로 고정한다.
`campaign_id`는 producer가 생성하며 재기록되어도 변하지 않아야 한다.

전용 저장소:

- queue DB: `PRISM_CAMPAIGN_QUEUE_PATH`
- 기본값: `prism_campaign_queue.sqlite`
- Kakao DB와 분리하여 producer가 Kakao 테이블을 알지 못하게 한다.

GCP Pub/Sub은 KIS 실매매 추종 신호 경로에만 유지한다. Kakao 캠페인
경로는 GCP, Redis, Upstash 같은 네트워크 broker를 사용하지 않는다.

### 5.4 발송 작업

```python
@dataclass(frozen=True)
class OutboundDelivery:
    delivery_key: str
    room_id: str
    message_type: str
    payload: dict
    created_at: datetime
    expires_at: datetime | None
```

application use case는 Kakao REST API를 직접 호출하지 않고 outbox에 `OutboundDelivery`를 기록한다.

## 6. 런타임 흐름

### 6.1 Gateway 수신

```text
Gateway DISPATCH
  → event_mapper
  → inbound_events INSERT(event_id UNIQUE)
  → room/user/command 검증
  → use case 실행
  → 업무 상태 + outbox INSERT를 한 트랜잭션으로 commit
  → Gateway sequence 전진
```

규칙:

- 중복 `event_id`는 업무 로직을 다시 실행하지 않는다.
- sequence는 업무 상태와 outbox가 커밋된 뒤 전진시킨다.
- callback token은 로그에 남기지 않는다.
- callback token은 영속 저장하지 않는 것을 기본으로 한다.
- 즉시 ack가 필요하면 현재 프로세스에서 한 번만 callback을 시도한다.
- 분석 결과, 시그널, 리더보드는 모두 영속 outbox를 통한 `send_message`로 발송한다.

### 6.2 `/report`

```text
MESSAGE_CREATE
  → 방 승인, quota, ticker 검증
  → AnalysisJob 생성
  → analysis_jobs INSERT
  → 즉시 callback ack
  → worker가 AnalysisPort 실행
  → AnalysisResult 저장
  → 만료형 PDF 링크 생성
  → 완료 메시지를 outbox에 기록
  → sender가 send_message
```

Kakao worker는 `analysis_manager`를 사용하지 않는다. `PrismReportAdapter`가 채널 중립 리포트 서비스를 호출한다.

안전한 도입 순서:

1. 현재 `analysis_manager.py`의 리포트 생성 동작을 회귀 테스트로 고정한다.
2. 생성·캐시·PDF 부분만 `prism_core/report_service.py`로 추출한다.
3. 기존 Telegram worker가 새 서비스를 호출하도록 어댑트한다.
4. Kakao `PrismReportAdapter`도 같은 서비스를 호출한다.

이렇게 하면 생성 로직은 공유하지만 큐와 발송 상태는 공유하지 않는다.

### 6.3 배치 캠페인 알림

개별 시그널을 받는 즉시 각 방에 보내지 않는다. Prism이 분석을 마치고 후보를 확정한 뒤 `BatchCampaign`을 발행한다.

```text
local SQLite campaign queue
  → BatchCampaign normalize
  → campaign INSERT(campaign_id UNIQUE)
  → 승인 상태와 market/session 구독 확인
  → 활성 room별 delivery_key 생성
  → outbox INSERT
  → 성공 또는 이미 처리된 이벤트 CONSUMED
```

delivery key 예시:

```text
campaign:{campaign_id}:{room_id}
```

한 방의 실패가 다른 방 발송을 롤백하지 않도록 방별 outbox 레코드를 둔다.

Local queue 소비 정책:

- 정상 처리 또는 동일 `campaign_id` 재처리: `CONSUMED`
- 계약 위반 JSON: 오류를 기록하고 `DEAD`
- 일시적인 DB/런타임 오류: lease 해제 후 지연 재시도

`SKIPPED` 캠페인은 후보 카드로 발송하지 않는다. 룸이 `rest_notice`를 별도로 구독한 경우에만 다음과 같은 짧은 안내를 보낸다.

```text
현재 조정장으로 오전 분석은 쉬어갑니다.
오후 종가 확인 후 선별 결과를 알려드리겠습니다.
```

### 6.4 예측 등록과 정산

예측 단위는 명시적인 round다.

```text
round_id = KR-{trade_date}
```

필수 규칙:

- Asia/Seoul 기준
- 장 시작 전 또는 명시된 cutoff 전까지만 등록
- 동일 사용자·방·종목·라운드는 한 번만 등록
- UP은 정산가가 기준가보다 높을 때 HIT
- DOWN은 정산가가 기준가보다 낮을 때 HIT
- 동가는 PUSH로 처리하고 점수 변화 없음
- 거래정지·가격 누락은 PENDING 유지 후 제한 횟수 재시도
- 정산 결과와 점수 UPSERT는 한 트랜잭션
- `result IS NULL`인 행만 정산해 배치 재실행을 안전하게 한다

거래소 캘린더와 EOD 가격 소스는 구현 전에 하나로 확정한다.

## 7. 데이터 모델

Kakao 데이터는 기존 trading DB와 분리한 `kakao_bot.sqlite`에 저장한다.

최소 테이블:

- `schema_migrations`
- `kakao_rooms`
- `kakao_users`
- `kakao_gateway_state`
- `kakao_inbound_events`
- `kakao_analysis_jobs`
- `kakao_signal_inbox`
- `kakao_signal_campaigns`
- `kakao_prediction_rounds`
- `kakao_predictions`
- `kakao_scores`
- `kakao_outbox`
- `kakao_report_links`

필수 DB 정책:

- WAL mode
- foreign keys ON
- busy timeout
- 모든 시간은 UTC 저장, 화면과 거래일 계산만 Asia/Seoul 사용
- 상태값 CHECK 제약
- nullable 최소화
- 조회와 정산 키에 index
- schema migration을 프로세스 시작 전에 한 번만 실행

## 8. 룸 승인과 비용 통제

`ENTRANCE`는 룸을 발견하는 이벤트이지 자동 방송 동의가 아니다.

기본값:

- `approval_status = PENDING`
- 승인 전 모든 배치 구독 OFF
- 승인 시 초기 프로필은 KR 오후만 ON
- KR 오전, US 오전, US 오후는 명시적 opt-in
- 휴식 안내(`rest_notice`)는 OFF
- 승인된 룸에서만 명령 실행
- 룸 관리자만 구독 설정 변경

제한:

- `/report`: 사용자별 일일 제한 + 룸별 일일 제한
- `/ask`: 사용자별 일일 제한 + 입력 길이 제한
- 룸별 동시 분석 작업 수 제한
- 전체 queue depth 제한
- 일일 비용 상한
- `KAKAO_REPORT_ENABLED`, `KAKAO_ASK_ENABLED`, `KAKAO_SIGNAL_ENABLED`, `KAKAO_PREDICTION_ENABLED`

그룹 일반 대화에는 반응하지 않는다. 기본 주소 방식은 slash command, quickReply, 명시적 bot mention으로 제한한다.

## 9. Kakao API 발송 정책

재시도는 endpoint와 실패 유형별로 구분한다.

- 연결 전 실패: 제한 재시도 가능
- HTTP 429: `Retry-After` 준수
- HTTP 5xx: jitter가 포함된 지수 백오프
- HTTP 4xx: 재시도하지 않고 분류·알림
- callback: 남은 TTL 안에서만 처리
- timeout 후 성공 여부가 불명확한 POST: 무조건 즉시 재전송하지 않음
- send_message: outbox 상태와 delivery key로 재처리 통제

callback은 짧은 ack나 메뉴에만 사용한다. 장기 작업 완료는 항상 send_message outbox를 사용한다.

## 10. PDF 보안

원본 파일명을 URL에 노출하지 않는다.

```text
GET /kakao/reports/{opaque_token}
```

정책:

- 랜덤 opaque token
- DB에서 artifact path로 매핑
- 만료시각 필수
- `.pdf`만 허용
- resolve된 경로가 `pdf_reports` 하위인지 확인
- directory listing 금지
- `Content-Type: application/pdf`
- 적절한 `Content-Disposition`
- 보존기간 만료 후 링크와 파일 정리
- 접근 로그에 토큰 전체를 기록하지 않음

## 11. Gateway singleton과 배포

Gateway는 app-server의 단일 systemd unit으로 실행한다.

```text
prism-kakao-gateway.service
prism-kakao-worker.service
prism-kakao-web.service
```

Gateway 정책:

- OS file lock 또는 systemd 단일 unit으로 중복 시작 차단
- 배포는 stop-old → 종료 확인 → start-new 순서
- SIGTERM에서 heartbeat 종료 및 WS graceful close
- READY 이후에만 ready 상태
- close 4004는 자동 무한 재시작하지 않고 종료 + 운영 알림
- `Restart=on-failure`와 start-limit 사용

Kakao 배포와 재시작은 Telegram 프로세스를 재시작하지 않아야 한다.

## 12. 관측성

필수 health:

- `/healthz`: 프로세스 생존
- `/readyz`: DB migration 완료, Gateway READY, outbox worker 정상

필수 metric/log:

- Gateway 연결 상태와 마지막 HEARTBEAT_ACK 시각
- 마지막 수신 sequence
- reconnect 횟수와 close code
- inbound 중복 수
- 분석 queue depth와 oldest job age
- callback latency와 만료 수
- REST 상태 코드별 실패 수
- outbox pending/retrying/dead 수
- 룸별 팬아웃 성공률
- 예측 미정산 backlog

토큰, callback token, 원본 Authorization 헤더는 모든 로그에서 마스킹한다.

## 13. 테스트 및 릴리스 게이트

### 단위

- Gateway payload → `InboundMessage` 변환
- 명령 라우팅
- template 제한
- quota
- 예측 cutoff와 동가/휴장/가격 누락
- signed report link
- retry matrix

### 통합

- 동일 DISPATCH 두 번 수신 시 업무 한 번
- 업무 commit 전 crash와 commit 후 crash 복구
- outbox 발송 성공 전후 crash
- callback 만료, 429, 4xx, 5xx
- 동일 예측 동시 등록
- 정산 배치 두 번 실행
- SQLite lock과 busy timeout
- 미승인 방 및 opt-out 방

### Gateway

- HELLO → IDENTIFY → READY
- RESUME 성공
- 4009 후 새 IDENTIFY
- 4004 시 clean exit
- heartbeat ACK 누락
- 100개 replay buffer 초과 시 운영 경고
- 두 프로세스 중복 시작 차단

### E2E

- 실제 테스트 방 입장
- `/report` ack와 완료 카드
- PDF 링크 열기와 만료
- 예측 등록·정산·mention
- KR 오후 배치 캠페인 한 번 발송
- 봇 LEAVE 후 발송 중단

릴리스 조건:

1. 모든 HIGH 위험 시나리오 테스트 통과
2. 테스트 방에서 실제 Kakao 렌더 캡처 확보
3. feature flag 기본 OFF 상태로 배포
4. 승인된 테스트 방 하나만 canary 활성화
5. 24시간 오류·중복·queue backlog 확인 후 확대

## 14. 구현 단계

### 2026-07-23 구현 스냅샷

완료:

- 로컬 SQLite campaign queue publisher/consumer
- Gateway protocol, event mapper, SQLite session/sequence resume state
- ENTRANCE/MESSAGE_CREATE/CHAT_JOIN/LEAVE 원자 처리
- 룸 승인, KR 오후 기본 구독, 승인 취소 발송 차단
- durable outbox, campaign renderer, REST sender runtime
- campaign consumer runtime, 운영 admin CLI, systemd 템플릿
- Gateway → local campaign queue → outbox → sender 로컬 E2E

실계정 대기:

- Kakao REST 단건 smoke
- Gateway opcode/close code/실제 payload fixture 캡처
- orchestrator와 Kakao runtime의 운영 파일 권한 smoke
- 운영 서버 systemd 배치

### Phase 0: 계약 고정

- Kakao REST/Gateway 요청·응답 fixture 보관
- callback/send_message 실제 스모크
- close code와 rate-limit 응답 확인
- 원문 설계의 미해결 항목 결정

### Phase 1: 독립 골격

- 패키지와 dependency rule
- SQLite migrations
- Gateway handshake와 singleton
- inbound dedupe와 outbox
- room 승인과 도움말

### Phase 2: `/report`

- 기존 생성 동작 회귀 테스트
- 채널 중립 `ReportService` 추출
- Kakao analysis job과 completion delivery
- 만료형 PDF 링크

### Phase 3: 배치 캠페인

- orchestrator의 `COMPLETED`/`SKIPPED` 캠페인 계약
- 로컬 SQLite campaign queue publisher
- Kakao 전용 local queue consumer
- KR 오후 기본 구독
- KR 오전·US 오전·US 오후 선택 구독
- room별 outbox fanout

### Phase 4: 예측

- round/cutoff
- 등록·정산 원자성
- 리더보드 mention

### Phase 5: 운영

- systemd/nginx
- metrics/alerts
- backup/restore
- canary, rollback, 제출 캡처

## 15. 완료 기준

이 설계는 다음이 충족될 때 구현 준비 완료로 전환한다.

- Kakao 코드가 Telegram 런타임을 import하지 않는다.
- Prism 재사용 기능이 port와 adapter로만 연결된다.
- 모든 inbound 이벤트와 outbound delivery에 안정적인 식별자가 있다.
- 재시작과 이벤트 재전달 테스트에서 중복 업무와 중복 점수가 발생하지 않는다.
- 미승인 방은 어떠한 고비용 기능이나 broadcast도 실행할 수 없다.
- Kakao 프로세스 장애·재배포가 Telegram 및 trading 프로세스에 영향을 주지 않는다.
- Gateway, worker, sender, PDF의 health와 rollback 절차가 문서화되어 있다.
