# 카카오톡 그룹봇 "프리즘 라운지" 설계

> **작성일**: 2026-07-22 | **개정**: 2026-07-22 (수신 방식을 Gateway로 확정) | **상태**: 설계 확정(구현 전)
> **대상**: 사내 카카오톡 봇 공모전 (공모 2026-07-22 ~ 2026-08-09 24:00, 결과발표 2026-08-17)
> **기반 자산**: 이 리포지토리(PRISM-INSIGHT) — `telegram_ai_bot.py`, `stock_analysis_orchestrator.run_full_pipeline`, `messaging/` 시그널 스트림, `analysis_queue`, `archive_api.py`(FastAPI 패턴)

## 0. 공모전 공지 (원문 요지)

**주제**: 그룹채팅방에서 재미있게·유용하게·자주 쓸 수 있는 챗봇 개발 (카테고리 무관)

**일정**
- 공모 기간: 2026-07-22(수) ~ 2026-08-09(일) 24:00  → **가용 기간 약 2.5주. 스코프는 공격적으로 MVP화한다.**
- 결과 발표: 2026-08-17(월)

**시상**: 🥇 대상 300만(1팀) / 🥈 최우수 150만(1팀) / 🥉 우수 100만(1팀) / 🎖️ 열정러상 10만(최대 5팀).
선정작은 협의에 따라 **실 서비스 출시 가능성** 있음.

**참여 대상**: 카카오 및 공동체 크루(개인/팀). 공동체는 VPN IP 등록 신청 필요(개인 IP 불가).

**참여 절차**
1. 디벨로퍼스에서 앱 생성 → 봇 생성 → 인증 토큰 확인
2. REST API 문서 참고하여 기능 구현 (튜토리얼만으로도 빠른 제작 가능)
3. 생성한 봇은 카카오톡 **검색**으로 발견 가능
4. **채널채팅이나 그룹채팅에 초대해 테스트** (단, 테스트 방 개수 제한 있음)

**주의**: 테스트 채팅방에 카카오/공동체가 아닌 **외부 사용자 초대 금지**. 크루 간 업무 목적 사용 무관.

**심사 기준** (내부 10여 명 심사) — 본 설계의 최적화 목표:
1. **완성도·사용성** — 시나리오가 매끄럽고 UX가 뛰어난가, 유의미한 사용성이 있는가
2. **상호작용** — 그룹채팅방 내 멤버 간 상호작용/채팅방 활성화
3. **지속성** — 매일/자주 사용하는가
4. **창의성** — 창의적이고 새로운 기능인가

**제출 방법**: 아지트 쓰레드 댓글에 양식 기재 → 본 문서 §13 체크리스트 참조.

## 1. 배경 및 목표

`telegram_ai_bot.py`는 개인이 봇과 1:1로 대화하는 주식 분석 도구다. 이를 카카오톡으로
이식하되, **§0 공모전 심사기준**에 최적화한다.

기준 ②가 "그룹채팅방 상호작용"을 명시하므로, 1:1 유틸리티 복제(B안)가 아니라
**그룹 투자클럽 컴패니언(A안)** 으로 재설계한다.

### 비목표 (Non-goals / YAGNI)
- US 시장 지원 (MVP 이후)
- 번역·저널·theme/signal(Firecrawl) 명령 (MVP 이후)
- 채널채팅(Carousel 가능) 대응 — 대상은 **일반/팀 채팅방**으로 고정 (mention 우선)

## 2. 카카오 Bot REST API 사실 근거

문서: `https://developers.kakao.com/docs/in/bot/rest-api` (2026-07-22 확인)

이 API는 구형 오픈빌더 1:1 챗봇 + 유료 친구톡 모델이 **아니다.** 일반채팅·오픈채팅·
팀채팅·채널채팅 방에 봇을 초대해 쓰는 신형 봇 플랫폼이다.

| 항목 | 사실 | 설계 영향 |
|---|---|---|
| **선제적 전송** | `POST /v1/bot/send_message`, "봇이 있는 방에 먼저 메시지를 보낼 수 있습니다". 일반/오픈/팀채팅은 `botGroupKey`, 채널채팅은 `botUserKey`. 무료. | 텔레그램 채널 발송의 **무료 등가물**. 시간 제한 없음. |
| **콜백(비동기)** | `POST /v1/bot/callback`, 콜백 토큰 **5분** 만료. | 5초~5분 작업용. 5분 초과 작업은 send_message로 우회. |
| **말풍선** | SimpleText(1000자, 500자 초과 시 "전체보기"), SimpleImage, TextCard, BasicCard, ListCard(items 최대 5), ItemCard, CommerceCard, Carousel(**채널채팅만**). 출력 최대 3개. | 그룹채팅은 Carousel 불가 → 다건은 ListCard/복수 카드. |
| **QuickReply** | 최대 10개. `action: message` → 선택 발화 전송. | 명령 메뉴·게임 선택지 UX. |
| **mention** | SimpleText에서 `{{#mentions.{userKey}}}`, `extra.mentions`로 매핑. 최대 15명. **채널채팅 미지원**(그룹/오픈/팀채팅 지원). | 리더보드 호명의 핵심. 대상 채팅방을 그룹계열로 고정한 이유. |
| **버튼 액션** | message, webLink, phone, share, invite, inviteMember, mention, operator, settings, guide. | PDF는 webLink. 봇 확산은 invite/inviteMember. |
| **명령어** | `POST /v1/bot/commands/update` 최대 20개 등록. `/v1/bot/guide` 도움말. | 명령 메뉴 노출. |
| **인증** | `Authorization: KakaoAK ${BOT_TOKEN}`. 콜백은 추가로 `X-Bot-Callback-Token`. | 스킬서버 환경변수. |
| **파일 첨부** | 없음. | PDF는 공개 URL webLink로만 제공. |

### 사전 조건 (사용자가 준비)
- 카카오톡 **비즈니스 채널** 생성
- **봇 생성** 및 봇 인증 토큰 발급
- 상시 실행 환경(데몬/컨테이너) — Gateway WS 연결 유지용 (§2.5)
- 공개 HTTPS 도메인 — **PDF webLink 서빙용으로만** 필요. 기존 `analysis.stocksimulation.kr` 재사용(신규 도메인 불필요). Gateway 채택으로 **inbound 웹훅 URL은 불필요**

## 2.5 수신 방식 결정: Gateway (WebSocket) 채택

카카오 봇은 사용자 발화를 받는 방식이 **두 가지**다. 문서: `https://developers.kakao.com/docs/in/bot/gateway` (2026-07-22 확인)

| 축 | 웹훅 (Webhook) | **Gateway (채택)** |
|---|---|---|
| 이벤트 수신 | 카카오 → 우리서버 HTTP POST (inbound) | 봇 → 카카오 `wss://bot-gateway.kakao.com/gateway` 상시 WS 아웃바운드 |
| 공개 inbound 도메인 | 필수 | **불필요** (PDF webLink용 HTTPS만 필요) |
| 실행 형태 | 무상태 FastAPI | **상시 데몬** (heartbeat·재연결 상태머신 필수) |
| 방 생명주기 이벤트 | 없음 | **ENTRANCE/CHAT_JOIN/LEAVE 제공** |
| 동시 사용 | — | 웹훅 URL 등록 시 Gateway 사용 불가 (**배타적**) |

**채택 이유**: (1) 공모전 **VPN inbound IP 등록 마찰 회피** — 봇이 밖으로 다이얼하므로 inbound 허용 불필요, (2) `ENTRANCE` 이벤트로 봇 초대 즉시 룸 자동 등록(§12 미해결 해소, 기준③ 유리), (3) inbound TLS·라우팅 결정 제거.

### Gateway 프로토콜 사실 근거

- **핸드셰이크**: HELLO(op=10) → IDENTIFY(op=2, `d.token`=봇 인증 토큰) → READY(op=3, `d.session_id` 저장)
- **연결 유지**: HEARTBEAT(op=1)를 `heartbeat_interval`(기본 41250ms)보다 약간 짧게 반복 → HEARTBEAT_ACK(op=11). 90초 무응답 시 서버가 close(4009)
- **이벤트 수신**: DISPATCH(op=0), `s`(시퀀스, 매번 저장), `t`(이벤트 타입), `d`(페이로드)
- **이벤트 종류(t)**: `MESSAGE_CREATE`(발화), `ENTRANCE`(봇 입장), `CHAT_JOIN`(사용자 입장), `LEAVE`(봇 퇴장)
- **MESSAGE_CREATE 페이로드**: `id`, `content`, `timestamp`, `isChannelChatroom`, `botGroupKey`, `botUserKey`, `callbackToken`
- **답장**: WS로 안 보냄. `callbackToken`(**5분 만료**)을 헤더에 넣어 **메시지 콜백 API** 호출. 토큰 없으면 답장 불가 → 스킵
- **재연결**: READY의 `session_id` + 마지막 DISPATCH `s` 저장 후 RESUME(op=6). Close code — 1001(서버 재시작→RESUME), 4001(인증 실패→중단), 4003(잘못된 페이로드→중단), 4004(**다른 연결로 대체됨=중복 IDENTIFY**), 4009(세션 만료→새 IDENTIFY). 재시도는 지수 백오프(1→2→4…최대 30초). **이벤트 버퍼는 연결당 최근 100개만 보관** → 장기 중단 후 RESUME 시 그 사이 이벤트 유실 가능

### ⚠️ 봇 1개 = Gateway 연결 1개 (중대 제약)

같은 `BOT_TOKEN`으로 두 번째 IDENTIFY를 보내면 카카오가 기존 연결을 **close(4004)로 끊는다**. 따라서 **Gateway WS 데몬은 전체 시스템에서 단 하나의 프로세스에만 둔다**(§3). 여러 서버에서 동시 연결 금지.

**수신 ≠ 발신**: Gateway 연결은 이벤트 *수신* 전용이다. 메시지 *발신*(send_message 선제전송, 콜백 답장)은 `kapi.kakao.com` 대상 **무상태 outbound REST**라 WS 연결과 무관하게 어느 프로세스에서든 호출 가능하다.

### 봇 1개 = REST(아웃바운드) + 수신 방식 택1(WS)

디벨로퍼스의 "카카오톡 봇" 문서 구조상 봇 1개(BOT_TOKEN 1개)는 다음으로 구성된다:

- **REST API** — `send_message`·`callback`·`commands` 등 **아웃바운드/관리** 면. 인증 `봇 인증 토큰`, 권한 "봇 생성". **수신 방식과 무관하게 항상 사용 가능**
- **이벤트 수신** — 게이트웨이(WS) **XOR** 웹훅(HTTP) 중 **하나만**. 배타 제약은 *여기(인바운드)에만* 적용

즉 "WS/HTTP 택1"은 **인바운드 이벤트 전송 채널** 한정이며, `send_message`(아웃바운드)는 이 제약에 걸리지 않는다. **봇 2개를 만들 필요 없이 1개로 수신(WS)+발신(REST)을 모두 처리**한다 (공모전 출품작도 1개).

**증거 (`/docs/in/bot/tutorial` 에코봇)**: 같은 BOT_TOKEN으로 `wss://bot-gateway.kakao.com/gateway`(수신) + `https://kapi.kakao.com/v1/bot/callback`(답장, REST 아웃바운드)를 동시에 사용한다. `send_message`는 `callback`과 동일 REST 면이라 함께 동작한다. `[높음]`

## 3. 아키텍처

봇 1개당 Gateway 연결은 **하나만** 허용되므로(§2.5, close 4004), Gateway WS 데몬은 **상호작용 서버(A)에만** 둔다. orchestrator 서버(B)는 기존처럼 시그널 publish만 하고 Gateway에 연결하지 않는다.

```
[Server B · orchestrator 호스트]
  run_full_pipeline (Kakao를 전혀 모름, 무수정)
        │  publish
        ▼
[messaging/redis_signal_publisher.py · gcp_pubsub_signal_publisher.py]
   signal payload: {type(BUY/SELL/EVENT), ticker, company_name, price,
                    target_price, buy_score, market}
        │ subscribe (신규 구독자 1개 추가)
        ▼
┌───────────────────────────────────────────────────────┐
│  Server A · telegram_ai_bot 호스트 (Kakao 데몬 공존)     │
│  ┌─────────────────────────────────────────────────┐  │
│  │ Kakao Gateway 데몬 (신규, 단일 프로세스)           │  │
│  │  - wss://bot-gateway.kakao.com/gateway 상시 연결   │  │
│  │  - DISPATCH 수신·라우팅 (MESSAGE_CREATE/ENTRANCE…) │  │
│  │  - heartbeat·RESUME·지수백오프 재연결               │  │
│  │  - signal_bridge: 스트림 구독 → send_message 팬아웃 │  │
│  │  - analysis_queue enqueue (기존 재사용)             │  │
│  │  - 예측게임·리더보드                                │  │
│  └─────────────────────────────────────────────────┘  │
│  PDF 정적 서빙 (/kakao/reports/{file}) — 공개 HTTPS     │
└───────────────┬───────────────────────────────────────┘
   (outbound REST) kapi.kakao.com/v1/bot/{send_message, callback, commands}
                ▼
        [일반/팀 그룹채팅방]
```

**핵심 원칙**:
1. 파이프라인은 손대지 않는다. 카카오봇은 기존 시그널 스트림의 **새 구독자**이자 `analysis_queue`의 새 **enqueuer**일 뿐이다.
2. **Gateway 데몬은 Server A 단일 프로세스.** B를 포함해 어디서도 중복 연결 금지(4004).
3. **수신은 WS(A 단일), 발신은 무상태 REST(어디서든).** B가 방에 직접 쏘고 싶으면 send_message REST 호출도 가능하지만, 기본은 B publish → A signal_bridge → send_message.

## 4. 컴포넌트 (단일 책임)

신규 코드는 모두 `kakao/` 패키지 하위에 둔다.

| 모듈 | 책임 | 의존 |
|---|---|---|
| `kakao/gateway_daemon.py` | **상시 WS 데몬**. `wss://bot-gateway.kakao.com/gateway` 연결·핸드셰이크(HELLO/IDENTIFY/READY)·heartbeat·RESUME 재연결·close code 분기. DISPATCH 수신 → 이벤트(`t`)별 handler 라우팅. **단일 프로세스 강제** | handlers, kakao_client |
| `kakao/pdf_app.py` | 경량 FastAPI. PDF 정적 라우트(`/reports/{file}`)만 담당 (공개 HTTPS webLink용) | 없음 |
| `kakao/kakao_client.py` | Kakao API 클라이언트: `send_message(target_key, skill_response)`, `callback(token, skill_response)`, `update_commands()`, `update_guide()`. BOT_TOKEN 인증, 지수 백오프 재시도(텔레그램 v2.9.0 패턴 이식), 타임아웃 | aiohttp |
| `kakao/templates.py` | SkillResponse 빌더: `simple_text`, `list_card`, `basic_card`, `item_card`, `quick_replies`, `mention`. **제약 강제**: 1000자, 리스트 5개, 출력 3개, 라벨 14자, mention 15명 | 없음 |
| `kakao/signal_bridge.py` | 시그널 스트림 구독 → 아침 온램프 카드 생성 → 등록 룸에 `send_message` 팬아웃. `message_id` 기반 멱등 처리 | messaging(재사용), kakao_client, templates, room_registry |
| `kakao/room_registry.py` | `botGroupKey` → 룸 설정(구독 여부, 옵트인) CRUD. SQLite 영속 | db |
| `kakao/prediction.py` | 예측게임 로직: 등록·정산·점수. 익일 종가 기준 상승/하락 판정 | db, 가격조회(기존 pykrx 경로 재사용) |
| `kakao/handlers/report.py` | `/report` 또는 "리포트" 발화 처리. 즉시 ack → analysis_queue enqueue → 완료 후 send_message | analysis_queue(재사용), kakao_client, templates |
| `kakao/handlers/ask.py` | `/ask` 자연어 Q&A. 5분 내면 콜백, 무거우면 send_message | 기존 ask 로직 재사용 |
| `kakao/handlers/prediction.py` | 예측 등록·조회 이벤트 핸들러 | prediction, templates |
| `kakao/handlers/leaderboard.py` | 리더보드 조회·정산 후 mention 발송 | prediction, kakao_client, templates |
| `kakao/handlers/help.py` | 도움말·시작 안내 | templates |

## 5. 비동기 전략 (5분 제약) — 가장 중요

Gateway는 웹훅과 달리 **동기 HTTP 응답 채널이 없다.** 모든 답장은 DISPATCH의 `callbackToken`(**5분 만료**)으로 콜백 API를 호출하거나, `send_message`(시간 제한 없음)로 보낸다. 판단 기준은 "5초"가 아니라 **작업이 큐(analysis_queue)를 타느냐**다 — 큐 대기시간이 콜백 5분 예산에 포함되기 때문.

| 작업 성격 | 큐 경유 | 전략 |
|---|---|---|
| 즉답 (quickReply 메뉴, 예측 등록, 리더보드 조회, 도움말) | ❌ | 처리 즉시 **콜백 API**(5분 내 여유) |
| **큐 타는 모든 것 (`/report`, 무거운 `/ask`)** | ✅ | 즉시 콜백으로 ack("분석 시작했습니다, 완료되면 알려드릴게요") → 완료 시 **`send_message`**로 결과 푸시 |

**핵심 규칙**: 큐에 들어가는 순간 콜백에 의존하지 않는다. 큐 대기 + 처리시간은 예측 불가능하게 5분을 넘길 수 있으므로(리포트는 안내 문구상 5–10분 소요), 큐 경유 작업은 무조건 `send_message` 경로로 결과를 푸시한다. 이는 텔레그램 봇의 기존 패턴(`telegram_ai_bot.py`: 즉시 대기 메시지 → `analysis_queue.put` → 완료 시 `send_report_result`)과 1:1로 대응한다.

> **주의**: Gateway엔 메시지 *수정*(텔레그램 `edit_text`) 개념이 없다. ack 후 결과는 방에 **새 말풍선**으로 추가된다. "지금 N번째 대기중" 같은 진행 갱신이 불가하므로, ack 문구에 "순서대로 처리되며 완료 시 알려드림"을 처음부터 명시한다.

## 6. 기능 상세 (MVP 4종)

### 6.1 아침 시그널 온램프 (기준 ③ 지속성)
- 트리거: 파이프라인이 아침에 시그널 publish → `signal_bridge` 수신
- 동작: 등록된 각 룸에 온램프 카드 `send_message`
  - ListCard: 오늘의 시그널 종목(최대 5) — 종목명, 점수/사유
  - QuickReplies: `📊 리포트 보기`, `🎯 예측 등록`, `🏆 순위`
- 멱등: `message_id` 저장, 재시작 시 중복 발송 방지

### 6.2 /report 온디맨드 분석 (기준 ①)
- 트리거: quickReply `📊 리포트 보기` 또는 "리포트 삼성전자" 발화
- 동작: 즉시 ack → `AnalysisRequest` enqueue(기존 백그라운드 워커 재사용) → 완료 시
  요약 ListCard + `PDF 열기`(webLink, PDF 서빙 URL) send_message
- PDF 없으면 텍스트 요약 fallback (`send_report_result` 패턴 이식)

### 6.3 종목 예측 게임 + 리더보드 (기준 ②④)
- 등록: quickReply `🎯 예측 등록` → 종목 선택 → `상승`/`하락` quickReply →
  `kakao_predictions`에 저장 (base_price = 등록 시점 가격, settle_date = 익일)
- **정산**: 익일 종가 기준 단순 상승/하락 판정. 적중 시 +점수. 정산 배치 job이 수행
- 리더보드: 정산 후 룸에 mention 리더보드 send_message
  - SimpleText + `{{#mentions.userKey}}` 로 상위 멤버 호명 (최대 15명)
  - 예: "🏆 이번 라운드 1위 {{#mentions.u1}} (+15점)"

### 6.4 /ask 자연어 Q&A (기준 ①②)
- 트리거: 종목·테마 관련 자연어 발화
- 동작: **큐를 안 타는 즉답이면 콜백(5분 내), 큐를 타면 send_message**(§5 규칙). 기존 ask 로직 재사용

## 7. 데이터 모델 (SQLite, 프로젝트 관례)

```sql
CREATE TABLE kakao_rooms (
    bot_group_key TEXT PRIMARY KEY,
    room_name     TEXT,
    opt_in_signals INTEGER DEFAULT 1,   -- 아침 온램프 수신 여부
    created_at    TEXT
);

CREATE TABLE kakao_predictions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_group_key TEXT,
    bot_user_key  TEXT,                 -- mention 대상 식별
    nickname      TEXT,
    ticker        TEXT,
    direction     TEXT,                 -- 'UP' | 'DOWN'
    base_price    REAL,
    predicted_at  TEXT,
    settle_date   TEXT,                 -- 익일(영업일)
    result        TEXT,                 -- NULL | 'HIT' | 'MISS'
    points        INTEGER DEFAULT 0
);

CREATE TABLE kakao_scores (
    bot_group_key TEXT,
    bot_user_key  TEXT,
    nickname      TEXT,
    total_points  INTEGER DEFAULT 0,
    updated_at    TEXT,
    PRIMARY KEY (bot_group_key, bot_user_key)
);
```

가격 조회는 기존 KR 데이터 경로(pykrx)를 재사용한다. 정산 배치는 영업일 캘린더를 고려한다.

## 8. Q2 재사용 매핑 (run_full_pipeline 채널 발송 재사용)

**결론**: 리포트 생성/포맷팅/PDF 등 로직은 100% 재사용. 발송은 드롭인이 아니라
시그널 스트림 구독 + send_message로 재구성한다. 원문 그대로의 채널 덤프는 하지 않는다
(일방향이라 심사기준 ②④에 불리). 대신 산출물을 그룹 상호작용의 온램프로 재활용한다.

| 텔레그램 | 카카오 |
|---|---|
| `run_full_pipeline` 채널 발송 | 시그널 스트림(무수정) → `signal_bridge` → `send_message` |
| `analysis_queue` / `AnalysisRequest` / 백그라운드 워커 | **그대로 재사용** (/report) |
| `send_document(PDF)` | PDF 정적 서빙 + webLink 버튼 |
| `reply_text` / `send_message` | SkillResponse simpleText/카드 |
| `InlineKeyboardMarkup` | quickReplies + 카드 버튼 |
| `ConversationHandler` 다단계 입력 | quickReply 유도 + `bot_user_key` 세션 상태 |
| 지수 백오프 재시도(v2.9.0) | `kakao_client`에 동일 패턴 이식 |

## 9. 에러 처리
- **Gateway 상시성 (최우선)**: WS 데몬은 supervisor(systemd/pm2 등)로 상시 감시·자동 재기동. 데몬 다운 시 심사 중 봇이 **무응답**(에러도 없음)이 되고, 이벤트 버퍼 100개 초과분은 복구 불가
- **Gateway 재연결**: HEARTBEAT_ACK 누락 감지 → 재연결. close code 분기(1001·네트워크→RESUME, 4009→새 IDENTIFY, 4001/4003/4004→중단·점검), 지수 백오프(최대 30초). `session_id`·마지막 `s` 보존
- Kakao API 호출: 지수 백오프 재시도, 타임아웃 명시
- 콜백 토큰 5분 만료 → `send_message` fallback. `callbackToken` 없는 이벤트는 답장 스킵
- PDF 누락 → 텍스트 요약 fallback
- 시그널 재수신 → `message_id` 멱등으로 중복 발송 차단
- DISPATCH 파싱 실패 → 안전한 기본 안내 응답(도움말 유도)

## 10. 배포

**Server A (telegram_ai_bot 호스트)** 에 Kakao Gateway 데몬을 **공존**시킨다. Gateway는 아웃바운드 WS라 **inbound 웹훅 URL·TLS 라우팅이 불필요**하다. Server B(orchestrator)는 기존처럼 시그널 publish만 하고 Kakao 연결은 없다.

- **Gateway 데몬** (신규, Server A 단일 프로세스): `wss://bot-gateway.kakao.com/gateway`로 아웃바운드 연결. supervisor(systemd/pm2)로 상시 기동. **중복 실행 금지**(4004)
- **PDF 서빙**: 경량 FastAPI + uvicorn, 별도 포트(예: **:8770**), `archive_api.py` 구조 참고. webLink용으로만 공개 노출
- 리버스 프록시 경로(Streamlit 대시보드 무영향):
  ```
  https://analysis.stocksimulation.kr/
     ├─ /                     → Streamlit 대시보드 (기존)
     └─ /kakao/reports/{file} → pdf_reports 정적 서빙  ← PDF webLink URL
  ```
  > Gateway 채택으로 `/kakao/webhook` inbound 라우트는 **삭제**. inbound 방화벽 개방 불필요, **아웃바운드로 `wss://bot-gateway.kakao.com`·`kapi.kakao.com` 허용**만 확인
- PDF: docker-compose가 이미 `./pdf_reports`를 컨테이너에 마운트 → 그대로 정적 노출
- 기존 Redis에 구독 연결, `kapi.kakao.com` 호출
- 환경변수: `KAKAO_BOT_TOKEN`, `KAKAO_BOT_PUBLIC_BASE_URL`(기본 `https://analysis.stocksimulation.kr/kakao`), Redis 접속 정보

### 배포 전제조건 (Gateway)
- WS 데몬 supervisor 상시 감시 (다운 = 봇 무응답, §9)
- 봇 토큰당 단일 연결 — A/B 동시 연결 금지
- 아웃바운드 WS/HTTPS 방화벽 허용 (inbound 개방은 PDF 포트만)

## 11. 테스트 전략
- **착수 전 스모크**: 에코봇으로 Gateway 핸드셰이크(HELLO→IDENTIFY→READY)·DISPATCH 수신 확인. `send_message` 한 건 발송해 Gateway 연결 상태 선제전송 최종 확인 (§2.5 근거상 통과 예상 — 튜토리얼이 Gateway+REST 병용 증명)
- 단위: `templates` 제약 강제(글자수·개수), `signal_bridge` 포맷, `prediction` 정산 점수
- 통합: mock DISPATCH 이벤트 → 라우팅·SkillResponse JSON 검증, `send_message`/콜백 mock
- Gateway: 재연결(close 1001/4009) 시 RESUME·IDENTIFY 분기, heartbeat 타이머 단위 테스트
- 기존 `tests/test_gcp_pubsub_signal*.py`를 구독자 테스트 레퍼런스로 활용

## 12. 미해결/추후 결정
- 정산 배치 실행 시각(장 마감 후) 및 영업일 캘린더 소스 확정
- 룸 등록(옵트인) UX: **`ENTRANCE` 이벤트로 봇 초대 시 자동 등록**을 기본으로 하되, opt-in 온/오프 토글 UX 확정
- 예측 라운드 주기(일간 고정 vs 설정 가능)
- 팀 구성 (Gateway는 아웃바운드라 inbound VPN IP 등록 마찰 없음 — 아웃바운드 방화벽만 확인)
- ~~[검증 필요] Gateway 연결 상태 `send_message` 병용 가능 여부~~ → **[해소, 높음]** 튜토리얼 에코봇이 Gateway WS + callback REST 병용을 증명(§2.5). send_message는 동일 REST 면이라 §11 스모크는 최종 확인용
- **봇은 1개로 충분** — 수신(WS)과 발신(REST)이 별개 면이라 봇 2개 불필요, 공모전 출품작도 1개 (§2.5)

## 13. 제출 산출물 체크리스트 (마감 2026-08-09 24:00)

아지트 쓰레드 댓글 제출 양식에 맞춰 준비:

| # | 항목 | 준비물 | 상태 |
|---|---|---|---|
| (1) | 참여자 LDAP ID (팀원 전체) | 팀 확정 후 기재 | ☐ |
| (2) | 봇 이름 | 예: "프리즘 라운지" (확정 필요) | ☐ |
| (3) | 봇 프로필 URL | 디벨로퍼스에서 봇 생성 후 발급 | ☐ |
| (4) | 테스트 방 URL | **팀채팅 or 오픈채팅** URL (그룹계열 → mention 지원) | ☐ |
| (5) | 취지 + 주요 시나리오 설명 | **답변 샘플 문구 또는 UI 캡처 이미지 첨부 필수** — 아침 온램프 카드, /report 결과 카드, mention 리더보드 캡처 | ☐ |
| (6) | (선택) 소스코드 GitHub URL | 공개 가능 시 첨부 환영 | ☐ |

> **주의**: 테스트 방에 카카오/공동체 외부 사용자 초대 금지. 테스트 방 개수 제한 있음.
> (5)의 UI 캡처는 구현 중 실제 말풍선 렌더 결과를 캡처해 확보한다 — 심사 가시성의 핵심.

## 부록 A. 진행 맥락 (다른 세션 인수인계용)

이 설계는 다음 대화 맥락에서 도출됨:
- 출발점: 텔레그램 봇(`telegram_ai_bot.py`, 명령어 report/history/evaluate/us_*/journal/signal/theme/ask/insight)을 카카오로 이식 가능한지 + `run_full_pipeline`의 채널 발송 재사용 가능한지.
- 핵심 발견(정정 포함): 카카오 신형 Bot REST API는 `send_message`로 **봇이 있는 방에 무료 선제 전송 가능** → 텔레그램 채널 발송의 무료 등가물 존재(구형 오픈빌더/친구톡 모델과 다름). 상세는 §2.
- 방향 확정: 공모전 심사기준 ②(그룹 상호작용) 때문에 1:1 복제(B안)를 버리고 **그룹 투자클럽 컴패니언(A안)** 채택. 대상은 **일반/팀 채팅방**(mention 지원, Carousel은 채널채팅 전용이라 미사용).
- 발송 재사용 결론(§8): 파이프라인 무수정 → 기존 `messaging/` 시그널 스트림에 **구독자 1개 추가** + `analysis_queue` 재사용. 원문 채널 덤프는 지양(일방향, 심사 불리), 산출물은 그룹 상호작용 온램프로 재활용.
- 배포 확정(§10): 기존 `analysis.stocksimulation.kr` 서버에 공존. (초기엔 `/kakao/webhook` inbound 라우트 계획 → 아래 Gateway 개정으로 삭제.) PDF 서빙 `/kakao/reports/{file}`만 공개 노출.
- MVP 4기능: 아침 시그널 온램프 / 종목 예측게임+mention 리더보드 / `/report` 온디맨드 / `/ask` Q&A. 예측 정산 = **익일 종가 단순 상승·하락**.
- **수신 방식 개정(2026-07-22, §2.5)**: Gateway(WebSocket) 문서 확인 후 웹훅 대신 **Gateway 채택**. 근거 — 공모전 VPN inbound IP 등록 마찰 회피, `ENTRANCE`로 룸 자동 등록, inbound TLS·라우팅 제거. 제약 — 봇당 WS 연결 1개(중복 IDENTIFY 시 close 4004)이므로 **Gateway 데몬은 telegram_ai_bot 호스트(A) 단일 프로세스**에만 두고, orchestrator 호스트(B)는 기존처럼 publisher로만 유지. 수신은 WS(단일)·발신은 무상태 REST(어디서든). 미검증 가정 — Gateway 연결 상태의 `send_message` 병용(온램프 의존, §11 스모크로 착수 전 확인).
- 다음 단계: 이 스펙 기반으로 `writing-plans`(구현 계획) 작성 예정이었음.
