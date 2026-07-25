# Kakao 봇 운영 준비 및 실연결 Runbook

> 상태: 코드·로컬 E2E 완료, Kakao 실계정 연결 대기
> 대상 브랜치: `feat/kakao-bot-campaign-foundation`

## 1. 사용자가 해야 하는 일

### 필수

1. 카카오톡 비즈니스 채널을 준비한다.
2. 해당 채널에서 그룹봇을 생성하고 **봇 인증 토큰**을 발급한다.
3. 이벤트 수신 방식은 **Gateway**를 선택한다.
   - 같은 봇에 Webhook inbound를 동시에 설정하지 않는다.
   - 동일 토큰으로 Gateway 프로세스를 두 개 실행하지 않는다.
4. 봇을 테스트용 일반/오픈/팀 채팅방에 초대한다.
5. 운영 서버의 secret 환경에 `KAKAO_BOT_TOKEN`을 저장한다.

토큰을 Telegram, Git, 문서, 이슈에 붙여 넣지 않는다. 권장 위치는 권한
`0600`의 `/etc/prism/kakao-bot.env` 또는 클라우드 Secret Manager다.

### 로컬 실행 조건

- Prism orchestrator와 Kakao campaign consumer가 같은 호스트 또는 같은
  영속 filesystem을 사용해야 한다.
- 두 프로세스가 `PRISM_CAMPAIGN_QUEUE_PATH` 파일을 읽고 쓸 수 있어야 한다.
- GCP Pub/Sub은 KIS 실매매 추종 경로에만 유지하며 Kakao에는 사용하지 않는다.

## 2. 이미 구현된 범위

- Gateway HELLO/IDENTIFY/READY/HEARTBEAT/RESUME
- singleton file lock과 graceful shutdown
- Gateway payload 격리형 event mapper
- 이벤트 dedupe와 룸 lifecycle 원자 처리
- SQLite session/sequence 영속화
- 룸 승인 전 전체 구독 OFF
- 승인 시 KR 오후만 ON
- 로컬 SQLite campaign queue publisher/consumer
- room별 durable outbox
- Kakao SkillResponse renderer
- REST retry/rate-limit/ambiguous POST 정책
- sender/consumer 독립 런타임
- 룸 승인·구독 관리 CLI
- 외부 호출 없는 로컬 end-to-end 테스트

## 3. 로컬 queue 준비

별도 cloud 리소스는 필요 없다. 운영 디렉터리를 만들고 orchestrator와
Kakao consumer가 같은 queue 파일을 사용하도록 한다.

```bash
sudo install -d -o prism -g prism -m 0700 /var/lib/prism-kakao
sudo install -o prism -g prism -m 0600 /dev/null \
  /var/lib/prism-kakao/prism_campaign_queue.sqlite
sudo install -o prism -g prism -m 0600 /dev/null \
  /var/lib/prism-kakao/kakao_bot.sqlite
```

queue DB와 Kakao DB는 분리한다. producer는 Kakao 룸/outbox 테이블을 모르고,
channel-neutral queue에만 기록한다.

`PRISM_CAMPAIGN_QUEUE_PATH`는 Kakao 환경 파일뿐 아니라 KR/US orchestrator
서비스 환경에도 동일한 절대경로로 설정한다.

## 4. 운영 환경 파일

운영 디렉터리와 secret 파일을 먼저 만든다.

```bash
sudo install -d -o prism -g prism -m 0700 /var/lib/prism-kakao
sudo install -d -o root -g prism -m 0750 /etc/prism
sudo install -o root -g prism -m 0640 /dev/null /etc/prism/kakao-bot.env
```

```dotenv
KAKAO_BOT_TOKEN=secret
PRISM_CAMPAIGN_QUEUE_PATH=/var/lib/prism-kakao/prism_campaign_queue.sqlite
KAKAO_BOT_DATABASE_PATH=/var/lib/prism-kakao/kakao_bot.sqlite
KAKAO_GATEWAY_LOCK_PATH=/run/prism-kakao/gateway.lock
KAKAO_GATEWAY_LOG_LEVEL=INFO

KAKAO_REST_BASE_URL=https://kapi.kakao.com
KAKAO_SENDER_POLL_SECONDS=2
KAKAO_SENDER_BATCH_SIZE=20
KAKAO_SENDER_LEASE_SECONDS=30
KAKAO_SENDER_LOG_LEVEL=INFO
KAKAO_CONSUMER_POLL_SECONDS=2
KAKAO_CONSUMER_BATCH_SIZE=20
KAKAO_CONSUMER_LEASE_SECONDS=30
KAKAO_CONSUMER_LOG_LEVEL=INFO
```

## 5. 실연결 순서

### 5.1 Gateway만 foreground로 시작

```bash
python -m kakao_bot.runtime.gateway_main
```

기대 결과:

- HELLO 수신 후 IDENTIFY
- READY 로그
- 봇을 방에 초대하면 ENTRANCE 처리
- 토큰, callback token, 원본 payload는 로그에 나타나지 않음

### 5.2 발견된 방 확인 및 승인

```bash
python -m kakao_bot rooms
python -m kakao_bot approve ROOM_ID
python -m kakao_bot subscription ROOM_ID
```

승인 직후 기본값은 KR 오후만 ON이다. 예:

```bash
python -m kakao_bot subscription ROOM_ID \
  --us-morning on \
  --us-afternoon on \
  --rest-notices on
```

### 5.3 Consumer와 sender 시작

```bash
python -m kakao_bot.runtime.campaign_consumer_main
python -m kakao_bot.runtime.sender_main
```

outbox 상태:

```bash
python -m kakao_bot campaigns
python -m kakao_bot outbox
```

### 5.4 REST 계약 단건 smoke

실제 테스트 방에 한 건을 보내는 명시적 명령이다.

```bash
python -m kakao_bot.runtime.rest_smoke_main \
  --room-id ROOM_ID \
  --message "PRISM Kakao 봇 연결 확인" \
  --confirm-send
```

`--confirm-send`가 없으면 실제 전송하지 않는다.

### 5.5 합성 캠페인 smoke

승인된 테스트 방이 있는 상태에서 local queue에 v1 payload를 한 건 기록한다.

```bash
python -m kakao_bot.runtime.campaign_smoke_main --confirm-enqueue
```

기대 결과:

- consumer가 queue row를 `CONSUMED`로 변경
- outbox에 room별 한 건 생성
- sender가 Kakao REST `send_message` 호출
- 성공 시 `SENT`
- 같은 payload 재발행 시 중복 발송 없음

## 6. systemd

템플릿:

- `deploy/systemd/prism-kakao-gateway.service.example`
- `deploy/systemd/prism-kakao-consumer.service.example`
- `deploy/systemd/prism-kakao-sender.service.example`

운영 서버 경로와 사용자를 맞춘 뒤 `/etc/systemd/system`에 배치한다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now \
  prism-kakao-gateway \
  prism-kakao-consumer \
  prism-kakao-sender
```

Gateway는 반드시 하나만 실행한다. 배포 순서는 stop-old, 종료 확인,
start-new다.

## 7. Rollback

```bash
sudo systemctl stop \
  prism-kakao-gateway \
  prism-kakao-consumer \
  prism-kakao-sender
```

Kakao consumer/sender 장애는 기존 Telegram과 매매 실행을 중단시키지 않는다.
orchestrator의 캠페인 publish hook도 fail-open이다.

## 8. 실계정에서만 확인 가능한 최종 게이트

- REST `send_message` body/header 실제 성공 응답
- callback body/header 및 5분 TTL
- Gateway opcode, close code, RESUME payload
- 실제 방 종류별 `botGroupKey`/`botUserKey`
- rate limit 응답과 `Retry-After`
- Gateway와 Webhook의 배타 설정

이 값들은 `event_mapper.py`, `gateway_protocol.py`,
`KakaoRequestBuilder`에 각각 격리되어 있어 실측 결과가 다르더라도 Prism
도메인이나 orchestrator를 수정하지 않고 교체할 수 있다.
