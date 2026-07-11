# Subscriber 로컬 운영 하네스 (AI 에이전트 / 사람 겸용)

> **대상**: prism-insight 트레이딩 시그널을 자기 KIS 계좌로 미러링하는 로컬 subscriber 스택을
> 처음 구축하는 사람, 또는 그 사람을 대신해 셋팅하는 AI 코딩 에이전트.
>
> **형식**: Phase 순서대로 실행한다. 각 Phase 끝의 **[검증 게이트]** 를 통과하기 전에는
> 다음 Phase로 넘어가지 않는다. GCP Pub/Sub 구독 자체(토픽 권한, 구독 생성)는
> [docs/EXTERNAL_SUBSCRIBER_GUIDE.md](../../docs/EXTERNAL_SUBSCRIBER_GUIDE.md)가 선행 문서다.

---

## 0. 안전 불변식 — AI 에이전트라면 반드시 지킬 것

1. **비밀값을 절대 노출하지 않는다.** `.env`, `trading/config/kis_devlp.yaml`,
   `trading/config/KIS_*.token`, GCP 서비스계정 JSON의 **내용을 채팅·로그·커밋에 출력 금지**.
   존재 여부/키 이름 확인은 허용, 값 확인은 금지.
2. **LIVE 전환은 사용자의 명시적 승인 후에만.** 두 곳이 해당된다:
   - `kis_devlp.yaml`의 `default_mode: demo → real`
   - `.env`의 `FILL_CHASER_LIVE=true`
3. **demo → SHADOW → LIVE 순서를 건너뛰지 않는다.**
4. **loop_a / loop_b를 로컬 cron에 등록하지 않는다.** 이유는 §7 참고.
5. 실행 전용 체크아웃은 **main 브랜치 고정**. feature 브랜치 코드로 실주문을 내지 않는다.
6. 이 문서의 cron 예시 시간을 그대로 복사하지 않는다. **반드시 §5의 타임존 환산을 먼저 수행**한다.

---

## 1. 사전 요구사항

- macOS 또는 Linux, Python 3.10+, `git`, `tmux`, cron (macOS/Linux 기본)
- 한국투자증권(KIS) 오픈API 앱키/시크릿, 해외주식 거래가 열린 계좌
- GCP 프로젝트 + prism 시그널 구독 권한 (선행: EXTERNAL_SUBSCRIBER_GUIDE.md 1~4단계 완료)
- (선택) 텔레그램 봇 토큰 — healthcheck 장애 경보 수신용

---

## 2. 실행 전용 체크아웃 만들기

개발용 체크아웃과 실행용 체크아웃을 **물리적으로 분리**한다.
(실제 사고 사례: 개발 체크아웃이 feature 브랜치의 stale 코드로 실주문을 냄)

```bash
git clone https://github.com/dragon1086/prism-insight.git ~/work/prism-subscriber
cd ~/work/prism-subscriber
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p logs
```

**[검증 게이트]** `git -C ~/work/prism-subscriber branch --show-current` 출력이 `main`.

---

## 3. 설정 파일 (비밀값 — 전부 gitignore 대상)

| 파일 | 내용 | 비고 |
|---|---|---|
| `.env` | `GCP_PROJECT_ID`, `GCP_PUBSUB_SUBSCRIPTION_ID`, `GCP_CREDENTIALS_PATH` | 이후 Phase에서 플래그 추가 |
| `trading/config/kis_devlp.yaml` | KIS 앱키/시크릿/계좌번호, `default_mode` | **처음엔 반드시 `default_mode: demo`(모의)** |
| GCP 서비스계정 JSON | Pub/Sub 구독 인증 | 리포 **밖** 경로 권장 (`~/secrets/`, `chmod 600`) |

**[검증 게이트]** `git status --porcelain`에 위 파일들이 나타나지 않아야 한다.
나타난다면 커밋 전에 중단하고 `.gitignore`를 확인할 것.

---

## 4. Subscriber 기동 — demo 모드 먼저

```bash
REPO=~/work/prism-subscriber
tmux new-session -d -s prism_pubsub_live \
  "cd $REPO && $REPO/.venv/bin/python examples/messaging/gcp_pubsub_subscriber_example.py 2>&1 | tee -a $REPO/logs/pubsub_subscriber.log"
```

**[검증 게이트]** 60초 안에 로그에 아래 3줄이 순서대로 떠야 한다:

```
🔹 Trading mode: ...
[STARTUP_SELFCHECK] OK
Listening for messages on projects/...
```

demo 모드로 **시그널 최소 1건 수신·처리를 확인**한 뒤, 사용자 승인을 받아
`default_mode: real`로 바꾸고 tmux 세션을 재시작한다(§8 스크립트 사용).

---

## 5. ⚠️ cron 타임존 규칙 — 이 하네스에서 가장 중요한 절

**cron은 로그인 셸의 `TZ`가 아니라 시스템 타임존으로 돈다.** 셸에서 `date`가 KST로 보여도
시스템이 다른 타임존이면 cron은 그 타임존으로 실행된다. (실제 사고 사례: 시스템이
`America/Los_Angeles`인 맥에 KST 기준 cron을 등록 → fill-chaser가 장중에 한 번도 안 돌았음)

```bash
# 시스템 타임존 확인 — 이 값이 cron의 기준이다
readlink /etc/localtime        # 예: /var/db/timezone/zoneinfo/America/Los_Angeles
env -i date                    # 셸 TZ 오염 없이 시스템 시간 확인
```

장 운영 시간은 KST 고정이므로 **시스템 타임존으로 직접 환산**해서 등록한다:

| 세션 (KST 고정) | UTC | America/Los_Angeles | America/New_York |
|---|---|---|---|
| KR장 월–금 09:00–15:30 | 00:00–06:30 월–금 | 16–23시 **일–목** | 19시–익일01:30 일–목 |
| US장 월–금 22:00–익일06:00 | 13:00–21:00 월–금 | 5–13시 월–금 | 8–17시 월–금 |

추가 규칙:
- **서머타임(DST)이 있는 타임존**이면 여름/겨울 시간이 1시간 어긋난다.
  양쪽을 모두 덮도록 **창을 1시간 넓게** 잡는다 (loop는 장외에 돌아도 no-op이라 무해).
- KST 기준 자정을 넘는 US 세션은 **현지 타임존으로 요일이 바뀔 수 있다.**
  요일 필드(`* * 1-5`)를 현지 요일로 다시 계산할 것. (실제 사고 사례: 금요일 밤 세션의
  토요일 새벽 KST 구간이 요일 필드에서 빠져 있었음)

**[검증 게이트]** cron 등록 후 다음 크론틱이 지나고 나서:
`ls -l logs/` 의 해당 로그 mtime이 갱신되어 있어야 한다.

---

## 6. Healthcheck cron

```cron
*/5 * * * * cd <REPO_DIR> && .venv/bin/python tools/subscriber_healthcheck.py >> logs/subscriber_healthcheck.log 2>&1
```

텔레그램 경보를 받으려면 `.env`에: `OAUTH_ALERT_BOT_TOKEN`, `SUBSCRIBER_ALERT_CHAT_ID`.

**[검증 게이트]** `tail logs/subscriber_healthcheck.log` 에 `status=ALIVE`.

---

## 7. Loop C (미체결 추격 fill-chaser) — SHADOW로 시작

**왜 로컬에서 돌아야 하나**: 지정가 매수가 미체결로 남았을 때 정정/취소하는 기능인데,
정정 대상 주문은 **구독자 본인 계좌**에 접수돼 있으므로 신호 발행측이 대신해 줄 수 없다.

**왜 loop_a(하드스톱)/loop_b(추세이탈)는 등록하지 않나**:
발행측의 loop 매도는 Pub/Sub SELL 시그널로 전파되어 subscriber가 이미 미러링한다.
또한 두 loop는 tracking agent의 holdings DB 테이블에 의존하는데 subscriber 로컬에는
그 테이블이 없어서 동작 자체가 불가능하다(`checked: 0`으로 헛돈다). 등록하면 중복+무의미.

```cron
# 시간은 반드시 §5에서 환산한 값으로 채울 것 — 아래 <...>는 자리표시자다
*/2 <KR장-현지시간> * * <KR장-현지요일> cd <REPO_DIR> && .venv/bin/python tools/loop_c_fill_chaser.py --market kr >> logs/loop_c_fill_chaser.log 2>&1
*/2 <US장-현지시간> * * <US장-현지요일> cd <REPO_DIR> && .venv/bin/python tools/loop_c_fill_chaser.py --market us >> logs/loop_c_fill_chaser.log 2>&1
```

기본값은 SHADOW(관측만, 주문 없음)다. 운영 순서:

1. SHADOW로 최소 하루 이상 장중 구동. 로그에서 `mode=SHADOW` 와 `Loop C done` 요약 확인.
2. 사용자 승인 후 `.env`에 `FILL_CHASER_LIVE=true` 추가.
3. 수동 1회 실행으로 LIVE 확인:
   ```bash
   .venv/bin/python tools/loop_c_fill_chaser.py --market us --once
   # 로그에 mode=LIVE 가 떠야 한다. 장외+미체결 0건이면 아무 액션 없이 종료되므로 안전.
   ```

세부 튜닝(`FILL_CHASER_GRACE_SEC`, `FILL_CHASER_BUY_MAX_PREMIUM_PCT` 등)은
[docs/FEATURE_FLAGS.md](../../docs/FEATURE_FLAGS.md)와 `tools/loop_c_fill_chaser.py` 상단 주석 참고.
`FILL_CHASER_ENABLED=false` 는 킬스위치다.

**[검증 게이트]** 장중 크론틱 후 `logs/loop_c_fill_chaser.log`에 `Loop C done ... mode=LIVE`.

---

## 8. 재배포 스크립트 (딸깍)

`~/work/restart_subscriber.sh` 로 저장하고 `chmod +x`:

```bash
#!/usr/bin/env bash
# main 최신화(pull) → subscriber 종료 → 재시작 → 연결 검증
set -euo pipefail
REPO="$HOME/work/prism-subscriber"
SESSION="prism_pubsub_live"
SUB_REL="examples/messaging/gcp_pubsub_subscriber_example.py"

cd "$REPO"
[ -z "$(git status --porcelain)" ] || { echo "워킹트리 dirty — 정리 후 재실행"; exit 1; }
git fetch origin main && git checkout main -q && git pull --ff-only origin main

tmux kill-session -t "$SESSION" 2>/dev/null || true
sleep 1
[ "$(pgrep -f "$SUB_REL" | wc -l | tr -d ' ')" = "0" ] || { echo "잔여 프로세스 있음 — 수동 확인"; exit 1; }

tmux new-session -d -s "$SESSION" "cd $REPO && $REPO/.venv/bin/python $SUB_REL 2>&1 | tee -a $REPO/logs/pubsub_subscriber.log"
for i in $(seq 1 60); do
  sleep 1
  tmux capture-pane -t "$SESSION" -p 2>/dev/null | grep -q "Listening for messages" && { echo "✅ 기동 완료 (${i}s)"; exit 0; }
done
echo "❌ 60초 내 Listening 미확인 — logs/pubsub_subscriber.log 확인"; exit 1
```

loop_c/healthcheck는 cron이 매 틱마다 최신 코드를 쓰므로 별도 재시작이 필요 없다.
subscriber 프로세스만 이 스크립트로 재기동하면 된다.

---

## 9. 운영 체크리스트 & 흔한 에러

| 증상 | 원인 | 대처 |
|---|---|---|
| `APBK0952 주문가능금액을 초과` | 예수금 부족. 주문 접수 자체가 거부됨 | **loop C로 해결 불가**(미체결이 아니라 거부). 예수금 충전. 수량 자동 축소는 설계상 없음(의도된 동작) |
| `EGW00123 기간이 만료된 token` | KIS 토큰 만료/불일치 | `trading/config/KIS_*.token` 삭제 후 재실행(자동 재발급) |
| restart 스크립트가 dirty로 중단 | 런타임 캐시(`prism-us/trading/data/exchange_cache.json`) 변경 등 | 캐시 파일이면 `git checkout -- <file>`, 아니면 stash 후 재실행 |
| loop 로그가 장중에 갱신 안 됨 | §5 타임존 미스매치 | `readlink /etc/localtime` 재확인 후 cron 시간 재환산 |

한눈 점검: `.venv/bin/python tools/feature_status.py` — 각 장치의 OFF/SHADOW/LIVE/미스케줄 상태를 표로 출력.

---

## 10. 완료 조건 (최종 상태 스냅샷)

- [ ] tmux `prism_pubsub_live` 에서 subscriber가 `Listening` (승인된 모드로)
- [ ] crontab: healthcheck(`*/5`) + loop_c KR/US (**시스템 타임존으로 환산된 시간**)
- [ ] loop_a / loop_b cron 항목 **없음**
- [ ] `.env` `FILL_CHASER_LIVE` 는 SHADOW 관측과 사용자 승인을 거친 뒤에만 `true`
- [ ] `git status`에 비밀 파일 미노출
- [ ] 계좌 예수금이 1회 매수 금액(기본 $1,000 상당) 이상인지 확인하는 루틴 합의
