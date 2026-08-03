# Kakao 봇 실계정 계약 검증 결과 (2026-07-27)

> 상태: Phase 0 완료 · 상호작용 수단 확정 대기
> 관련: `2026-07-23-kakao-bot-low-coupling-design.md`, `2026-07-22-kakao-group-bot-design.md`
> 검증 환경: 테스트 봇(`prism-test`) + 실제 팀채팅방 + 로컬 런타임

설계 문서의 페이로드 가정은 공식 문서 요약에서 나왔고, fixture도 그 가정 위에서 작성돼
있었다. 실제 봇 토큰으로 붙여보니 **가정과 실제가 여러 곳에서 달랐다.** 이 문서는 실측으로
확정된 사실만 기록한다. 추론은 "미확정"으로 명시한다.

## 1. 관측된 실제 페이로드

```
MESSAGE_CREATE
  id(int), isChannelChatroom(bool), botGroupKey, botUserKey,
  content(str), timestamp(ns 9자리), callbackToken, type

ENTRANCE
  botGroupKey, inviter{botUserKey}, params, timestamp, type

LEAVE
  botGroupKey, timestamp, type
```

설계 가정과 다른 점:

- **`id`는 정수다.** 문자열이 아니다. 기존 매퍼는 문자열만 받아 모든 `MESSAGE_CREATE`가
  매핑 실패했다.
- **`userKey`도 `sender` 객체도 없다.** 채널채팅·그룹채팅 양쪽 모두. 사용자 식별자는
  **`botUserKey`가 유일**하다.
- **`ENTRANCE`/`LEAVE`에는 `id`가 없다.** 이 때문에 룸 자동 발견이 조용히 실패하고 있었다.
- `ENTRANCE`에는 **`inviter.botUserKey`**가 있다. 누가 초대했는지 알 수 있다.
- `timestamp`는 나노초 9자리. Python `fromisoformat`이 마이크로초로 절삭해 정상 파싱한다.

### botUserKey는 방 무관 안정 식별자

같은 사용자가 1:1 채널채팅과 팀채팅방에서 발화했을 때 `botUserKey`가 **완전히 동일**했다.
공식 문서의 멘션 스펙도 `extra.mentions.{key}.type`을 `botUserKey`로 고정한다.

→ **Phase 4 예측게임 리더보드의 사용자 키로 `botUserKey`를 그대로 쓴다.**

## 2. 그룹방에서는 멘션된 메시지만 전달된다

통제 실험(같은 방, 같은 세션, 연속 3회):

- 그냥 채팅 → **미전달**
- `@prism-test` 멘션 채팅 → **전달**
- 1~2분 후 그냥 채팅 → **미전달**

이전에 관측된 "간헐적 전달"은 전부 멘션 유무로 설명된다. Gateway 구현 결함이 아니다.

부수 효과:

- 설계 §8의 "그룹 일반 대화에는 반응하지 않는다"가 **플랫폼 차원에서 보장**된다.
  애플리케이션에서 필터링할 필요가 없다.
- 단톡방 잡담이 봇에 유입되지 않으므로 LLM 비용·요율 위험이 크게 낮다.
- **`content`에서 멘션 접두어가 제거된 채로 온다.** 파싱 시 `@봇이름`을 벗길 필요가 없다.

## 3. 수신 불가 — 이미지와 답장

- **이미지**: 사진을 보내도 이벤트가 발생하지 않는다. 페이로드에 이미지 필드도, 별도
  이벤트 타입도 없다. 카카오는 사진에 캡션도 붙일 수 없다.
- **답장(reply)**: 봇 메시지에 답장해도 전달되지 않는다(멘션이 아니므로). 답장 대상을
  가리키는 필드도 없다.

→ **이미지 분석과 답장 기반 후속질문은 구현 불가.** 텔레그램의 `handle_photo`와
`conversation_contexts` 방식은 이식 대상에서 제외한다.

반대로 **발신은 가능**하다. `SimpleImage.imageUrl`로 봇이 이미지를 보낼 수 있다.
(차트 이미지 첨부는 가능하다는 뜻)

## 4. send_message는 방 초대가 전제조건

공식 문서: "채널채팅은 채널 친구여야 하며, 일반채팅·오픈채팅은 방에 초대되어야 합니다."

검색으로 친구추가만 한 1:1 방에 `send_message`를 시도하면 실패한다:

```
HTTP 400  code=-880  "봇이 초대되지 않은 방입니다."
```

`botGroupKey`로 보내든 `botUserKey`로 보내든 동일하게 실패했다. 즉 키 선택 문제가 아니라
방 소속 문제다. 봇이 초대된 팀채팅방에서는 `botGroupKey`로 정상 발송(HTTP 200)됐다.

→ **룸 온보딩 절차에 "봇 초대"를 전제로 명시해야 한다.**

## 5. 검증 완료된 경로

실제 팀채팅방에서 확인:

- Gateway 핸드셰이크 HELLO → IDENTIFY → READY
- `ENTRANCE`로 룸 자동 발견 → `PENDING` 등록
- `LEAVE` 처리
- 콜백 답장(`/v1/bot/callback`) `HTTP 200`
- 선제적 발송(`/v1/bot/send_message`) `HTTP 200`
- 캠페인 E2E: 큐 → consumer → room별 outbox → sender → 실제 발송
- 멱등성: 동일 캠페인 재발행 거부, 중복 발송 없음
- 승인 게이트: 미승인 방은 `deliveries=0`으로 차단
- 토큰이 로그에 남지 않음 (`_redact()` 실동작)

## 6. 상호작용 수단 — 실측 확정

팀채팅방에 ListCard(항목 2개 + 카드버튼 1개 + quickReply 1개)를 발송하고 육안 확인 및
탭 테스트를 수행했다.

### 렌더링 결과

- **ListCard** — 정상. 헤더, 항목 제목, 항목 설명 모두 표시된다.
- **ListItem** — 정상. 탭 가능.
- **카드 버튼(`buttons`)** — 정상. 탭 가능.
- **`quickReplies`** — **렌더링되지 않는다.** `send_message`와 `callback` 양쪽 모두에서
  버튼이 표시되지 않았다. 페이로드는 공식 규격과 일치하고 응답도 `HTTP 200`이다.
  → **팀채팅방에서 quickReply는 사용 불가.**

### 탭은 멘션 규칙을 우회한다 (가장 중요)

카드 항목이나 버튼을 탭하면 카카오가 **봇 멘션을 자동으로 붙여** 발화를 전송한다.

- 채팅방 표시: `@프리즘인사이트테스트 ① 리스트 항목 탭`
- 봇 수신 `content`: `① 리스트 항목 탭` (멘션 제거됨)

→ **사용자는 멘션을 타이핑할 필요가 없다.** 카드 탭만으로 모든 상호작용이 성립한다.
§2의 멘션 제약이 UX 문제가 되지 않는다.

### `messageText`는 무시되고 title/label이 발화가 된다

문서는 `action=message`일 때 `messageText`가 사용자 발화로 전송된다고 기술하지만,
실제로는 **ListItem의 `title`, Button의 `label`이 그대로 발화**로 전송됐다.

| 설정 | 문서상 기대 | 실제 전송 |
|---|---|---|
| item title=`① 리스트 항목 탭`, messageText=`항목탭 테스트` | `항목탭 테스트` | `① 리스트 항목 탭` |
| button label=`③ 카드버튼`, messageText=`카드버튼 테스트` | `카드버튼 테스트` | `③ 카드버튼` |

**설계 영향**: 사용자에게 보이는 문구가 곧 명령어다. 항목 제목과 버튼 라벨을 그대로
라우팅 키로 사용해야 한다. 예: 항목 제목을 `삼성전자 리포트`로 두면 그 문자열이 발화로
도착하므로 정확 매칭으로 라우팅한다.

Button `label`은 최대 14자(가로 2개 배치 시 8자) 제약이 있다.

### 사용 가능한 수단 정리

- `ListItem` 탭 — **주력**. 최대 5개, 제목이 발화가 된다.
- `Button.action=message` — 카드 버튼. 가로 2개 / 세로 3개.
- `Button.action=webLink` — PDF 링크 (설계 §10)
- `Button.action=mention` — 입력창에 멘션 자동 입력. 자유 입력이 필요한 명령
  (`평가 삼성전자 70000 6`)의 진입점으로 유용하다.
- `Button.action=invite` — 봇을 다른 채팅방에 초대 (확산, 심사기준 ③)
- 등록 명령어 `/v1/bot/commands/update` — 최대 20개, 상시 노출. 등록 성공 확인
  (`리포트`/`평가`/`예측`/`순위`/`도움말`). 채팅방 노출 형태는 미확인.
- `quickReplies` — **사용 불가**

## 7. 이 검증으로 수정된 코드

`30615be` — 실계정 페이로드 정합 (정수 id, `botUserKey` 폴백, lifecycle 합성 id,
매핑 실패 시 연결 유지, 예외 로깅 개선). 테스트 95개 통과.

## 8. 설계 변경 사항

- `/ask` **제외** — 구현이 Firecrawl 기반(`_run_firecrawl_command`)이라 비목표
  "theme/signal(Firecrawl) 명령"과 모순된다.
- `/evaluate`, `/us_evaluate` **채택** — 단 5단계 대화가 아니라 **한 줄 파싱**으로.
  그룹방에서 여러 턴을 점유하지 않게 한다.
- `/us_report` **채택** — `prism_core.report_service`가 이미 `market="us"`를 지원한다.
- 이미지 분석·답장 후속질문 **제외** — 플랫폼 미지원 (§3)

## 9. 배포 구성 (검토 결과)

- **db-server** (1vCPU/1.7GB): 오케스트레이터가 cron으로 하루 2회(09:30, 14:46) 실행.
  상주 서비스가 아니다. Redis는 미구동.
- **app-server** (1vCPU/1.7GB): 텔레그램 봇. 이 사양이 단일 워커 직렬 처리의 이유다.
- **prism-backend** (2vCPU/3.8GB, nginx 보유, 유휴): **카카오 봇 배치 대상**

캠페인 큐가 로컬 SQLite라 발행자와 소비자가 같은 파일시스템을 요구한다. 서버를 분리하려면
전송 계층만 교체한다 — 하루 2건이므로 인증된 HTTP 엔드포인트로 충분하다. `consumer` 이하는
변경 불필요. `publish_batch_campaign_best_effort`가 이미 fail-open이라 카카오 서버 장애가
오케스트레이터 배치에 영향을 주지 않는 성질도 유지된다.

**scp로 큐 파일을 동기화하는 방식은 불가**하다. 발행자가 중복 방지를 위해 기존 행을 읽고
소비자가 같은 행의 상태를 바꾸는 양방향 읽기·쓰기 구조라, 복사하면 멱등성이 깨진다.
