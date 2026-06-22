# ChatGPT Plus (OAuth/Codex) 주간 쿼터 모니터링

전 배치를 ChatGPT 구독(OAuth)으로 돌리기 시작하면서(KR 오전/오후 + US 3개),
주간 한도 소진(429)을 **사전 감지**하기 위한 쿼터 모니터다.
기존 워치독 `tools/oauth_healthcheck.py`에 통합했다(전송 인프라/쿨다운 재사용).

## 1. 쿼터 소스 (조사 결과 — 실측)

ChatGPT/Codex 백엔드(`https://chatgpt.com/backend-api/codex/responses`)는
**성공(200) 응답 헤더**로 Codex CLI가 표시하는 "주간 한도 X% 사용" 데이터를 그대로 노출한다.
서버에서 OAuth 호출 1회(`gpt-5.4-mini`, low effort)로 실측한 헤더:

```
x-codex-plan-type: plus                       # 플랜 (JWT claim은 free로 지연표시 — 실제는 plus)
x-codex-active-limit: premium
x-codex-primary-used-percent: 10              # 5시간 창 사용률
x-codex-primary-window-minutes: 300           # = 5h
x-codex-primary-reset-after-seconds: 10010
x-codex-primary-reset-at: 1782113124          # unix
x-codex-secondary-used-percent: 2             # ★ 주간 창 사용률 (목표)
x-codex-secondary-window-minutes: 10080       # = 7일
x-codex-secondary-reset-after-seconds: 596810
x-codex-secondary-reset-at: 1782699924        # unix
x-codex-credits-has-credits: False
x-codex-credits-balance:
x-codex-credits-unlimited: False
```

매핑:
- **primary** = 5시간 한도(소위 "5h"), window 300분.
- **secondary** = **주간 한도(7일)**, window 10080분 — 사용자가 보고받고 싶어한 그 수치.
- 잔량% = `100 - used_percent`. 리셋은 `*-reset-at`(절대시각) + `*-reset-after-seconds`(상대).

→ 별도 usage 엔드포인트나 429 파싱 없이도 **사전(proactive) 잔량**을 얻는다.
   429일 때도 헤더가 따라오면 동일 파싱 + "소진·리셋시각" 보고(폴백 내장).

## 2. 구현

파일: `tools/oauth_healthcheck.py` (확장, 신규 파일 없음)

- `_probe_quota()` — TokenManager로 토큰/account_id 획득 → Codex에 1회 경량 호출
  → `x-codex-*` 헤더 파싱하여 dict 반환(429 포함). 헤더 없으면 실패 사유 반환.
- `_format_quota_report()` — 한국어 요약 생성. 주간/5시간 사용·잔량·리셋(KST + 상대시간),
  plan_type/active_limit, 크레딧. 잔량 < `OAUTH_QUOTA_WARN_REMAINING_PCT`(기본 20%) 또는 429 시 ⚠️ 강조.
- `_run_quota(force_send)` — 프로브 후 텔레그램 전송. 위험 시 `_alert()`(쿨다운),
  정상 시 상태 라인 전송. 전송은 기존 `_send_telegram`(OAUTH_ALERT_BOT_TOKEN/CHAT_ID) 재사용.
- 엔트리포인트 플래그:
  - `--quota` : 매번 현황 전송 + 위험시 경보.
  - `--quota-dry-run` : 전송 없이 stdout에 메시지 미리보기.

신규 env(선택):
- `OAUTH_QUOTA_PROBE_MODEL` (기본 `gpt-5.4-mini` — Codex 호환 최경량, 쿼터 거의 안 씀)
- `OAUTH_QUOTA_WARN_REMAINING_PCT` (기본 `20`)

## 3. 검증 (서버 실측)

dry-run (전송 없음):
```
📊 ChatGPT 쿼터 현황
플랜: plus (active=premium)

🗓 주간(7일): 사용 2% · 잔량 98%
   리셋: 06/29 11:25 (6.9일 후)
⏱ 5시간: 사용 10% · 잔량 90%
   리셋: 06/22 16:25 (2.8시간 후)
```

실제 전송: `OAUTH_ALERT_CHAT_ID=-1002989735551 ... --quota`
→ `status=200 plan=plus week_used=2% 5h_used=10% danger=False`, 테스트방 전송 성공.
`.env`의 `OAUTH_ALERT_CHAT_ID`가 이미 `-1002989735551`(테스트방)이라 cron 오버라이드 불필요.

## 4. 제안 cron (사용자가 직접 적용 — 본 작업에서 crontab 미변경)

```
# 쿼터 현황 (매시간; --quota는 항상 상태라인 전송, 위험시 경보)
CRON_TZ=Asia/Seoul
0 * * * * cd /root/prism-insight && /root/.pyenv/shims/python tools/oauth_healthcheck.py --quota >> /root/prism-insight/logs/oauth_health.log 2>&1
```
배치 전후 감시를 더 촘촘히 원하면 KR/US 배치 직전·직후 시각에 추가 라인을 둘 수 있다.

## 5. 한계

- 프로브가 호출 1회분 쿼터를 소모(극소, low effort + "ok" 입력). 매시간 ≈ 하루 24콜.
- `x-codex-*` 헤더는 OpenAI 비공식 — 헤더명/스키마 변경 시 깨질 수 있음(헤더 없으면 실패 사유 보고).
- 토큰 갱신 지연으로 JWT의 `chatgpt_plan_type`은 free로 보일 수 있으나, **헤더 `x-codex-plan-type`은 실제값(plus)** 이라 이를 신뢰원으로 사용.
- 매시간 정상 현황도 전송 → 소음이면 cron 주기를 늘리거나 `_run_quota`에서 정상시 전송 생략하도록 조정 가능.
- 프로덕션 crontab/배포는 **변경하지 않음**(PR 링크만 보고).
