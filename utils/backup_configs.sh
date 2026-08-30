#!/bin/bash

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
    cp stock_tracking_db.sqlite $BACKUP_DIR/
    log "✓ stock_tracking_db.sqlite 백업 완료"
else
    log "⚠ stock_tracking_db.sqlite 파일 없음"
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
