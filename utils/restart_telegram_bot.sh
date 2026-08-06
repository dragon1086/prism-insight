#!/usr/bin/env bash
#
# 텔레그램 봇 멱등 재기동.
#
# 몇 번을 실행하든 결과는 "정확히 1개 인스턴스"로 수렴한다.
#
# 왜 필요한가
# -----------
# 2026-08-01~02, 재기동을 kill 단계와 start 단계로 나눠 실행하다가 start만 5번
# 착지했다. 기존 프로세스를 죽이지 않은 채 새로 띄우기를 반복한 결과 인스턴스가
# 5개까지 쌓였고, 텔레그램이 다음을 뱉으며 봇이 사실상 마비됐다:
#
#   telegram.error.Conflict: terminated by other getUpdates request;
#   make sure that only one bot instance is running
#
# 원인은 SSH 너머로 kill/start를 따로 호출한 것. 이 스크립트는 둘을 하나의
# 원자적 단위로 묶어 그 실패 양식을 구조적으로 차단한다.
#
# SSH 행잉 방지
# -------------
# `ssh host "nohup cmd &"` 는 자식이 SSH 채널의 stdout/stderr 를 물고 있으면
# 채널이 안 닫혀 그대로 매달린다. setsid 로 새 세션을 만들고 세 fd 를 전부
# 끊어야 즉시 반환된다. 호출부에서도 `ssh -n` 을 쓸 것.
#
# 사용법
#   ssh -n app-server 'bash /home/prism/prism-insight/utils/restart_telegram_bot.sh'
#   bash utils/restart_telegram_bot.sh --status   # 죽이지 않고 상태만
set -uo pipefail

APP_DIR="${APP_DIR:-/home/prism/prism-insight}"
ENTRY="telegram_ai_bot.py"
LOG="${APP_DIR}/telegram_bot.log"
PY="${PY:-python3}"

# The bot imports report generation modules before loading its .env file, so
# formal-report model defaults must exist in the process environment at launch.
# Explicit operator overrides still win.
export REPORT_MODEL="${REPORT_MODEL:-gpt-5.6-luna}"
export REPORT_EFFORT="${REPORT_EFFORT:-high}"

# pyenv python 이 ENTRY 를 직접 실행하는 프로세스만 매칭한다.
# `-bash -c ... nohup python3 telegram_ai_bot.py ...` 형태의 래퍼 셸까지
# 잡으면 애먼 것을 죽이게 되므로 정규식을 실행 이미지에 고정한다.
find_pids() {
    ps -eo pid,args \
        | awk -v e="$ENTRY" '$0 ~ ("(^|/)python[0-9.]*[[:space:]]+" e "$") {print $1}'
}

status() {
    local pids
    pids=$(find_pids | tr '\n' ' ' | sed 's/ *$//')
    local n=0
    [ -n "$pids" ] && n=$(printf '%s\n' $pids | grep -c .)
    echo "instances=${n} pids=[${pids}]"
    return 0
}

if [ "${1:-}" = "--status" ]; then
    status
    exit 0
fi

echo "[restart] before: $(status)"

# ---- 1. 전부 종료 (TERM -> 확인 -> 남으면 KILL) -----------------------------
pids=$(find_pids)
if [ -n "$pids" ]; then
    echo "[restart] SIGTERM -> $(echo $pids | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null

    for _ in $(seq 1 15); do
        sleep 1
        [ -z "$(find_pids)" ] && break
    done

    leftover=$(find_pids)
    if [ -n "$leftover" ]; then
        echo "[restart] 15초 후에도 생존 -> SIGKILL: $(echo $leftover | tr '\n' ' ')"
        # shellcheck disable=SC2086
        kill -KILL $leftover 2>/dev/null
        sleep 2
    fi
else
    echo "[restart] 실행 중인 인스턴스 없음"
fi

# 여기서 0개가 아니면 기동하지 않는다. 확인 못 한 채 띄우면 사고가 재현된다.
remaining=$(find_pids)
if [ -n "$remaining" ]; then
    echo "[restart] FATAL: 종료 실패, 기동 중단. 생존 PID: $(echo $remaining | tr '\n' ' ')" >&2
    exit 1
fi
echo "[restart] 전부 종료 확인"

# ---- 2. 정확히 1개 기동 (완전 분리) ----------------------------------------
cd "$APP_DIR" || { echo "[restart] FATAL: cd $APP_DIR 실패" >&2; exit 1; }
{
    echo "===== RESTART $(date -Is) branch=$(git branch --show-current 2>/dev/null) head=$(git rev-parse --short HEAD 2>/dev/null) ====="
} >> "$LOG"

setsid nohup "$PY" "$ENTRY" >> "$LOG" 2>&1 < /dev/null &
disown 2>/dev/null || true

# ---- 3. 기동 확인 -----------------------------------------------------------
for _ in $(seq 1 15); do
    sleep 1
    [ -n "$(find_pids)" ] && break
done

final=$(find_pids | tr '\n' ' ' | sed 's/ *$//')
count=0
[ -n "$final" ] && count=$(printf '%s\n' $final | grep -c .)

echo "[restart] after: instances=${count} pids=[${final}]"

if [ "$count" -ne 1 ]; then
    echo "[restart] FATAL: 인스턴스가 1개가 아니다 (${count}개). 즉시 확인 필요." >&2
    exit 1
fi

echo "[restart] OK — 단일 인스턴스 기동 완료"
exit 0
