#!/bin/bash

# 프로젝트 루트 디렉토리 자동 감지
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# .pyenv 환경 활성화 (스크립트 시작 부분에 추가 - 반드시 추가!)
PYENV_ROOT="$HOME/.pyenv"
export PYENV_ROOT
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv 1>/dev/null 2>&1; then
    eval "$(pyenv init -)"
fi

# 로그 파일 설정
LOG_FILE="$PROJECT_ROOT/stock_scheduler.log"

# 로그 함수
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# 현재 디렉토리를 스크립트 디렉토리로 변경
cd "$PROJECT_ROOT" || exit 1

# Python 실행 파일 찾기 (우선순위: venv > pyenv > system)
# 영업일 체크도 이 Python으로 실행되므로 반드시 먼저 결정해야 한다
if [ -f "$PROJECT_ROOT/venv/bin/python" ]; then
    PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"
    log "가상환경 Python 사용: $PYTHON_BIN"
elif [ -f "$HOME/.pyenv/shims/python" ]; then
    PYTHON_BIN="$HOME/.pyenv/shims/python"
    log "pyenv Python 사용: $PYTHON_BIN"
else
    PYTHON_BIN="python3"
    log "시스템 Python 사용: $PYTHON_BIN"
fi

# 시장 영업일 체크
log "주식 시장 영업일 체크 시작"
"$PYTHON_BIN" "$PROJECT_ROOT/check_market_day.py"
MARKET_CHECK=$?

if [ $MARKET_CHECK -ne 0 ]; then
    log "오늘은 주식 시장 영업일이 아닙니다. 스크립트 실행을 건너뜁니다."
    exit 0
fi

# 실행할 프로그램 모드
MODE=$1
TODAY=$(date +%Y%m%d)

# 로그 파일 지정 (날짜별)
BATCH_LOG_FILE="$PROJECT_ROOT/logs/stock_analysis_${MODE}_${TODAY}.log"
mkdir -p "$PROJECT_ROOT/logs"

# 로그 출력
log "실행 모드: $MODE, 로그 파일: $BATCH_LOG_FILE"

# 백그라운드에서 스크립트 실행
log "$MODE 배치 백그라운드 실행 시작"
nohup "$PYTHON_BIN" "$PROJECT_ROOT/stock_analysis_orchestrator.py" --mode "$MODE" > "$BATCH_LOG_FILE" 2>&1 &

# 실행된 프로세스 ID 저장
PID=$!
log "프로세스 ID: $PID 로 실행됨"

# PID 파일 생성 (나중에 상태 확인 용도)
echo $PID > "$PROJECT_ROOT/logs/stock_analysis_${MODE}_${TODAY}.pid"

log "$MODE 배치 실행 요청 완료"
exit 0