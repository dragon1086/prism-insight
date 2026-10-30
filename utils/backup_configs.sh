#!/bin/bash
# On backup failure, preserve older backups; retention runs only after success.
# Low-space recovery is an operator decision, not permission to delete the last good copy.
set -euo pipefail
umask 077

################################################################################
# PRISM-INSIGHT 정기 백업 스크립트
# 작성일: 2025-12-05
# 설명: 중요 설정 파일 및 데이터베이스를 자동으로 백업
################################################################################

# 백업 설정
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_BASE_DIR=~/prism_backups
BACKUP_DIR=$BACKUP_BASE_DIR/$DATE
PROJECT_DIR=$HOME/prism-insight
LOG_FILE=$BACKUP_BASE_DIR/backup.log
EVENT_RETENTION=${PRISM_BTC_EVENT_RETENTION:-1}
case "$EVENT_RETENTION" in
    0|1) ;;
    *) EVENT_RETENTION=invalid ;;
esac
SNAPSHOT_EXTRA=()
RETENTION_EXTRA=()
if [ "$EVENT_RETENTION" = 1 ]; then
    SNAPSHOT_EXTRA=(--capture-review-event-proof)
    RETENTION_EXTRA=(--event-proof-only)
fi

# 로그 함수
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a $LOG_FILE
}

# 백업 디렉토리 생성
mkdir -p $BACKUP_DIR
log "백업 시작: $BACKUP_DIR"

# 1. 루트 경로 파일들 백업
log "루트 경로 파일 백업 중..."
cd $PROJECT_DIR

# .env 파일
if [ -f .env ]; then
    cp .env $BACKUP_DIR/
    log "✓ .env 백업 완료"
else
    log "⚠ .env 파일 없음"
fi

# mcp_agent 설정 파일들
for file in mcp_agent.*.yaml; do
    if [ -f "$file" ]; then
        cp "$file" $BACKUP_DIR/
        log "✓ $file 백업 완료"
    fi
done

# stock_tracking_db.sqlite
if [ -f stock_tracking_db.sqlite ]; then
    if python3 "$PROJECT_DIR/utils/sqlite_snapshot.py" \
        --source "$PROJECT_DIR/stock_tracking_db.sqlite" \
        --destination "$BACKUP_DIR/stock_tracking_db.sqlite.gz" --timeout 1200 --capture-references \
        --retention-cursor "$BACKUP_BASE_DIR/.btc-retention-cursor.json" ${SNAPSHOT_EXTRA[@]+"${SNAPSHOT_EXTRA[@]}"}; then
        log "✓ SQLite 온라인 스냅샷·압축·복원 해시 검증 완료"
    else
        log "CRITICAL: SQLite 스냅샷 실패; 기존 백업 보존, 원격 전송과 7일 정리 중단"
        exit 1
    fi
else
    log "오류: stock_tracking_db.sqlite 없음; 기존 DB 백업을 보존하고 종료"
    exit 1
fi

# 2. trading 디렉토리 백업
log "trading 디렉토리 백업 중..."
mkdir -p $BACKUP_DIR/trading/config

if [ -f trading/config/kis_devlp.yaml ]; then
    cp trading/config/kis_devlp.yaml $BACKUP_DIR/trading/config/
    log "✓ trading/config/kis_devlp.yaml 백업 완료"
else
    log "⚠ trading/config/kis_devlp.yaml 파일 없음"
fi

# 3. examples 디렉토리 백업
log "examples 디렉토리 백업 중..."
mkdir -p $BACKUP_DIR/examples/streamlit

if [ -f examples/streamlit/config.py ]; then
    cp examples/streamlit/config.py $BACKUP_DIR/examples/streamlit/
    log "✓ examples/streamlit/config.py 백업 완료"
else
    log "⚠ examples/streamlit/config.py 파일 없음"
fi

# 백업 파일 권한 설정 (보안)
find $BACKUP_DIR -type d -exec chmod 700 {} \;
find $BACKUP_DIR -type f -exec chmod 600 {} \;

# 백업 크기 확인
BACKUP_SIZE=$(du -sh $BACKUP_DIR | cut -f1)
log "백업 완료: $BACKUP_SIZE"

# Only the verified database, its manifest and Git history go offsite, never settings.
if python3 "$PROJECT_DIR/utils/btc_offsite_archive.py" \
    --repo "$PROJECT_DIR" --snapshot "$BACKUP_DIR/stock_tracking_db.sqlite.gz" \
    | tee "$BACKUP_DIR/offsite_ack.json" | tee -a "$LOG_FILE"; then
    log "✓ 별도 서버 DB·Git 복원 검증 ACK 완료"
else
    log "CRITICAL: 별도 서버 백업 미확인/비활성; 기존 백업 보존, 보존기간 삭제 중단"
    exit 1
fi

# Drop only the exact uploaded local gzip after independently rechecking its archive.
if PYTHONPATH="$PROJECT_DIR/prism-btc:$PROJECT_DIR" PRISM_DISABLE_SIGNAL_PUBLISH=1 \
    python3 "$PROJECT_DIR/tools/remove_uploaded_btc_backup.py" "$BACKUP_DIR" | tee -a "$LOG_FILE"; then
    log "검증된 원격 사본을 확인해 로컬 DB 압축 중복을 정리했습니다"
else
    log "⚠ 원격 사본 재확인 실패; 로컬 DB 압축본을 보존합니다"
fi

# One transient timer starts a bounded cleanup in the existing idle window.
CURRENT_SECOND=$((10#$(date +%S)))
RETENTION_DELAY=$(((90 - CURRENT_SECOND) % 60))
if [ "$RETENTION_DELAY" -lt 10 ]; then
    RETENTION_DELAY=$((RETENTION_DELAY + 60))
fi
PYTHON_BIN=$(command -v python3)
if [ "$EVENT_RETENTION" = invalid ]; then
    log "⚠ 잘못된 BTC 정리 설정; 백업은 보존하고 정리는 건너뜁니다"
elif systemd-run --quiet --collect --on-active="${RETENTION_DELAY}s" --timer-property=AccuracySec=1s \
    --unit="prism-btc-retention-${DATE}" --setenv="PATH=$PATH" \
    --setenv="PRISM_DISABLE_SIGNAL_PUBLISH=1" \
    --setenv="PYTHONPATH=$PROJECT_DIR/prism-btc:$PROJECT_DIR" \
    /usr/bin/bash -c 'exec "$1" "$2" "$3" "${@:4}"' -- \
    "$PYTHON_BIN" "$PROJECT_DIR/tools/run_btc_review_retention.py" "$BACKUP_DIR" ${RETENTION_EXTRA[@]+"${RETENTION_EXTRA[@]}"}; then
    log "7일 정리 1회 등록; 실행 결과는 별도 확인"
else
    log "⚠ 7일 정리 등록 실패; 다음 백업에서 재시도"
fi

# 7일 이상 된 백업 삭제
log "오래된 백업 정리 중..."
find $BACKUP_BASE_DIR -maxdepth 1 -type d -mtime +7 ! -path $BACKUP_BASE_DIR -exec rm -rf {} \; 2>/dev/null
REMAINING=$(find $BACKUP_BASE_DIR -maxdepth 1 -type d ! -path $BACKUP_BASE_DIR | wc -l)
log "현재 유지 중인 백업: ${REMAINING}개"

# 백업 목록 생성
log "백업 파일 목록:"
ls -lh $BACKUP_DIR >> $LOG_FILE 2>&1

log "=========================================="
echo ""
echo "백업이 완료되었습니다!"
echo "백업 위치: $BACKUP_DIR"
echo "백업 크기: $BACKUP_SIZE"
echo "로그 파일: $LOG_FILE"
