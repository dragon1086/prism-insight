# 카카오톡 그룹봇 "프리즘 라운지" 설계

> **작성일**: 2026-07-22 | **상태**: 설계 확정(구현 전) | **대상 공모전**: 사내 봇 공모전

## 1. 배경 및 목표

`telegram_ai_bot.py`는 개인이 봇과 1:1로 대화하는 주식 분석 도구다. 이를 카카오톡으로
이식하되, **사내 봇 공모전 심사기준**에 최적화한다.

심사기준:
1. **완성도·사용성** — 시나리오가 매끄럽고 UX가 뛰어난가, 유의미한 사용성이 있는가
2. **상호작용** — 그룹채팅방 내 멤버 간 상호작용/채팅방 활성화
3. **지속성** — 매일/자주 사용하는가
4. **창의성** — 창의적이고 새로운 기능인가

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
- 스킬서버용 **공개 HTTPS 도메인**

## 3. 아키텍처

```
[기존 stock_analysis_orchestrator.run_full_pipeline]
        │  (Kakao를 전혀 모름, 무수정)
        ▼ publish
[messaging/redis_signal_publisher.py  ·  gcp_pubsub_signal_publisher.py]
   signal payload: {type(BUY/SELL/EVENT), ticker, company_name, price,
                    target_price, buy_score, market}
        │ subscribe (신규 구독자 1개 추가)
        ▼
┌─────────────────────────────────────────────┐
│  Kakao 스킬서버 (별도 경량 FastAPI, 신규)      │
│   - webhook 수신·라우팅                        │
│   - signal_bridge: 스트림 구독 → send_message  │
│   - analysis_queue enqueue (기존 재사용)        │
│   - PDF 정적 서빙 (/reports/{file})            │
│   - 예측게임·리더보드                           │
└───────────────┬─────────────────────────────┘
   kapi.kakao.com/v1/bot/{send_message, callback, commands}
                ▼
        [일반/팀 그룹채팅방]
```

**핵심 원칙**: 파이프라인은 손대지 않는다. 카카오봇은 기존 시그널 스트림의 **새 구독자**이자,
기존 `analysis_queue`의 새 **enqueuer**일 뿐이다. 텔레그램 봇/파이프라인과 완전히 독립적으로 배포·운영.

## 4. 컴포넌트 (단일 책임)

신규 코드는 모두 `kakao/` 패키지 하위에 둔다.

| 모듈 | 책임 | 의존 |
|---|---|---|
| `kakao/webhook_app.py` | FastAPI 앱. 웹훅 라우팅, 동기 응답, PDF 정적 라우트(`/reports/{file}`) | handlers, kakao_client |
| `kakao/kakao_client.py` | Kakao API 클라이언트: `send_message(target_key, skill_response)`, `callback(token, skill_response)`, `update_commands()`, `update_guide()`. BOT_TOKEN 인증, 지수 백오프 재시도(텔레그램 v2.9.0 패턴 이식), 타임아웃 | aiohttp |
| `kakao/templates.py` | SkillResponse 빌더: `simple_text`, `list_card`, `basic_card`, `item_card`, `quick_replies`, `mention`. **제약 강제**: 1000자, 리스트 5개, 출력 3개, 라벨 14자, mention 15명 | 없음 |
| `kakao/signal_bridge.py` | 시그널 스트림 구독 → 아침 온램프 카드 생성 → 등록 룸에 `send_message` 팬아웃. `message_id` 기반 멱등 처리 | messaging(재사용), kakao_client, templates, room_registry |
| `kakao/room_registry.py` | `botGroupKey` → 룸 설정(구독 여부, 옵트인) CRUD. SQLite 영속 | db |
| `kakao/prediction.py` | 예측게임 로직: 등록·정산·점수. 익일 종가 기준 상승/하락 판정 | db, 가격조회(기존 pykrx 경로 재사용) |
| `kakao/handlers/report.py` | `/report` 또는 "리포트" 발화 처리. 즉시 ack → analysis_queue enqueue → 완료 후 send_message | analysis_queue(재사용), kakao_client, templates |
| `kakao/handlers/ask.py` | `/ask` 자연어 Q&A. 5분 내면 콜백, 무거우면 send_message | 기존 ask 로직 재사용 |
| `kakao/handlers/prediction.py` | 예측 등록·조회 웹훅 핸들러 | prediction, templates |
| `kakao/handlers/leaderboard.py` | 리더보드 조회·정산 후 mention 발송 | prediction, kakao_client, templates |
| `kakao/handlers/help.py` | 도움말·시작 안내 | templates |

## 5. 비동기 전략 (5초/5분 제약) — 가장 중요

카카오 웹훅은 ~5초 내 응답해야 하고 콜백 토큰은 5분 만료된다. 리포트 생성은 수 분 걸린다.

| 작업 소요 | 전략 |
|---|---|
| **< 5초** | 동기 웹훅 응답 (quickReply 메뉴, 예측 등록, 리더보드 조회, 도움말) |
| **5초 ~ 5분** | `useCallback` 응답으로 즉시 "준비중" 반환 + 콜백 토큰 획득 → 완료 시 `POST /v1/bot/callback` |
| **> 5분 (풀 리포트)** | 즉시 simpleText ack("분석 시작했습니다, 완료되면 알려드릴게요") → analysis_queue 완료 시 **`send_message`** 로 결과 푸시 (시간 제한 없음) |

리포트는 세 번째 경로를 기본으로 한다. 콜백 5분 초과 위험을 회피한다.

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
  요약 ListCard + `PDF 열기`(webLink, 스킬서버 서빙 URL) send_message
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
- 동작: 5분 내 답변 가능하면 콜백, 무거우면 send_message. 기존 ask 로직 재사용

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
- Kakao API 호출: 지수 백오프 재시도, 타임아웃 명시
- 콜백 토큰 5분 만료 → `send_message` fallback
- PDF 누락 → 텍스트 요약 fallback
- 시그널 재수신 → `message_id` 멱등으로 중복 발송 차단
- 웹훅 파싱 실패 → 안전한 기본 안내 응답(도움말 유도)

## 10. 배포
- 별도 경량 스킬서버: FastAPI + uvicorn, 공개 HTTPS(리버스 프록시/도메인)
- 기존 Redis에 구독 연결, `kapi.kakao.com` 호출
- 환경변수: `KAKAO_BOT_TOKEN`, `KAKAO_BOT_PUBLIC_BASE_URL`(PDF), Redis 접속 정보
- PDF 정적 서빙 경로는 `PDF_REPORTS_DIR`를 마운트

## 11. 테스트 전략
- 단위: `templates` 제약 강제(글자수·개수), `signal_bridge` 포맷, `prediction` 정산 점수
- 통합: mock 웹훅 요청 → SkillResponse JSON 검증, `send_message` mock
- 기존 `tests/test_gcp_pubsub_signal*.py`를 구독자 테스트 레퍼런스로 활용

## 12. 미해결/추후 결정
- 정산 배치 실행 시각(장 마감 후) 및 영업일 캘린더 소스 확정
- 룸 등록(옵트인) UX: 봇 초대 시 자동 등록 vs 명시적 `/시작` 명령
- 예측 라운드 주기(일간 고정 vs 설정 가능)
