# OAuth 헬스·쿼터 모니터 운영

`tools/oauth_healthcheck.py`는 OAuth 토큰 상태와 최근 인증·쿼터 오류를 읽기 전용으로 점검하고, 필요할 때 지정한 Telegram 운영 채팅으로 알립니다. 매매·DB 데이터는 변경하지 않습니다.

## db-server cron

운영 반영 시 기존 항목을 다음처럼 유지합니다. `--quota`는 상태 메시지를 보내므로 3시간마다만 실행합니다.

```cron
# OAuth 토큰·로그 오류 감시: 30분마다
*/30 * * * * cd /root/prism-insight && /root/.pyenv/shims/python tools/oauth_healthcheck.py >> logs/oauth_health.log 2>&1

# 쿼터 현황: 3시간마다 정각
0 */3 * * * cd /root/prism-insight && /root/.pyenv/shims/python tools/oauth_healthcheck.py --quota >> logs/oauth_health.log 2>&1
```

`--quota-dry-run`은 Telegram을 전송하지 않고 현재 보고 본문만 표준 출력에 표시합니다.

## 쿼터 창 해석

`x-codex-primary-*`와 `x-codex-secondary-*`는 고정된 “5시간”·“주간” 한도가 아닙니다. 보고서는 각 슬롯의 `x-codex-*-window-minutes`가 양수일 때만 해당 창을 표시하고, 그 길이로 라벨을 만듭니다. 따라서 백엔드가 7일 창을 `primary`에만 제공하고 `secondary`를 `0`으로 제공하면, 보고서는 단일 `주간(7일)` 창만 표시합니다.

제공된 창 중 하나라도 잔량이 `OAUTH_QUOTA_WARN_REMAINING_PCT`(기본 20%) 미만이거나 HTTP 429가 발생하면 경보 상태가 유지됩니다. 제공되지 않은 창은 경보 판단에 포함하지 않습니다.
