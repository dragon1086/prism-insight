#!/bin/bash

# 프로젝트 루트 디렉토리 자동 감지
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"  # utils의 부모 디렉토리

# 보관 기간 설정
DAYS_TO_KEEP_LOGS=7      # 로그 파일: 7일
DAYS_TO_KEEP_REPORTS=30  # PDF/MD 보고서: 30일

# utils 디렉토리 생성 (없는 경우)
mkdir -p "$PROJECT_ROOT/utils"

# 스크립트 실행 시간 기록
echo "$(date): 로그 정리 시작" >> "$PROJECT_ROOT/utils/log_cleanup.log"

# =============================================================================
# 1. 로그 파일 패턴 (7일 보관)
# =============================================================================
LOG_PATTERNS=(
    # 한국 주식
    "ai_bot_*.log*"
    "trigger_results_morning_*.json"
    "trigger_results_afternoon_*.json"
    "*stock_tracking_*.log"
    "orchestrator_*.log"
    # 미국 주식
    "us_orchestrator_*.log"
    "trigger_results_us_*.json"
)

# 프로젝트 루트에서 7일 이상 된 로그 파일 삭제
for PATTERN in "${LOG_PATTERNS[@]}"; do
    find "$PROJECT_ROOT" -maxdepth 1 -name "$PATTERN" -type f -mtime +$DAYS_TO_KEEP_LOGS -exec rm {} \;
done

# =============================================================================
# 2. prism-us 디렉토리 내 로그 파일 (7일 보관)
# =============================================================================
PRISM_US_DIR="$PROJECT_ROOT/prism-us"

if [ -d "$PRISM_US_DIR" ]; then
    # US 성과 추적 로그
    find "$PRISM_US_DIR" -maxdepth 1 -name "us_performance_tracker_*.log" -type f -mtime +$DAYS_TO_KEEP_LOGS -exec rm {} \;

    # US 스케줄러 로그 (일요일에 내용 비우기)
    if [ $(date +%u) -eq 7 ]; then
        US_SCHEDULER_LOG="$PRISM_US_DIR/us_stock_scheduler.log"
        if [ -f "$US_SCHEDULER_LOG" ]; then
            > "$US_SCHEDULER_LOG"
            echo "$(date): prism-us/us_stock_scheduler.log 내용을 비웠습니다." >> "$PROJECT_ROOT/utils/log_cleanup.log"
        fi
    fi
fi

# =============================================================================
# 3. logs 디렉토리 내의 누적 로그파일 처리 (일요일에 내용 비우기)
# =============================================================================
LOGS_DIR="$PROJECT_ROOT/logs"
if [ -d "$LOGS_DIR" ] && [ $(date +%u) -eq 7 ]; then
    LOG_ACCUMULATING_PATTERN="stock_analysis_*.log"
    find "$LOGS_DIR" -name "$LOG_ACCUMULATING_PATTERN" -type f -exec sh -c '> {}' \;
    echo "$(date): logs 디렉토리의 누적 로그파일 내용을 비웠습니다." >> "$PROJECT_ROOT/utils/log_cleanup.log"
fi

# =============================================================================
# 4. PDF/MD 보고서 파일 (30일 보관)
# =============================================================================

# 한국 주식 PDF 보고서
KR_PDF_DIR="$PROJECT_ROOT/pdf_reports"
if [ -d "$KR_PDF_DIR" ]; then
    DELETED_KR_PDF=$(find "$KR_PDF_DIR" -name "*.pdf" -type f -mtime +$DAYS_TO_KEEP_REPORTS -exec rm {} \; -print | wc -l)
    if [ "$DELETED_KR_PDF" -gt 0 ]; then
        echo "$(date): 한국 PDF 보고서 ${DELETED_KR_PDF}개 삭제 (30일 경과)" >> "$PROJECT_ROOT/utils/log_cleanup.log"
    fi
fi

# 미국 주식 PDF 보고서
US_PDF_DIR="$PRISM_US_DIR/pdf_reports"
if [ -d "$US_PDF_DIR" ]; then
    DELETED_US_PDF=$(find "$US_PDF_DIR" -name "*.pdf" -type f -mtime +$DAYS_TO_KEEP_REPORTS -exec rm {} \; -print | wc -l)
    if [ "$DELETED_US_PDF" -gt 0 ]; then
        echo "$(date): 미국 PDF 보고서 ${DELETED_US_PDF}개 삭제 (30일 경과)" >> "$PROJECT_ROOT/utils/log_cleanup.log"
    fi
fi

# 미국 주식 MD 보고서
US_REPORTS_DIR="$PRISM_US_DIR/reports"
if [ -d "$US_REPORTS_DIR" ]; then
    DELETED_US_MD=$(find "$US_REPORTS_DIR" -name "*.md" -type f -mtime +$DAYS_TO_KEEP_REPORTS -exec rm {} \; -print | wc -l)
    if [ "$DELETED_US_MD" -gt 0 ]; then
        echo "$(date): 미국 MD 보고서 ${DELETED_US_MD}개 삭제 (30일 경과)" >> "$PROJECT_ROOT/utils/log_cleanup.log"
    fi
fi

# =============================================================================
# 5. 텔레그램 메시지 파일 (30일 보관)
# =============================================================================

# 한국 주식 텔레그램 메시지
KR_TELEGRAM_DIR="$PROJECT_ROOT/telegram_messages"
if [ -d "$KR_TELEGRAM_DIR" ]; then
    find "$KR_TELEGRAM_DIR" -type f -mtime +$DAYS_TO_KEEP_REPORTS -exec rm {} \;
fi

# 미국 주식 텔레그램 메시지
US_TELEGRAM_DIR="$PRISM_US_DIR/telegram_messages"
if [ -d "$US_TELEGRAM_DIR" ]; then
    find "$US_TELEGRAM_DIR" -type f -mtime +$DAYS_TO_KEEP_REPORTS -exec rm {} \;
fi

# =============================================================================
# 6. 결과 요약
# =============================================================================

# 삭제 후 남은 로그 파일 수 확인 및 기록
REMAINING_LOGS=0
for PATTERN in "${LOG_PATTERNS[@]}"; do
    COUNT=$(find "$PROJECT_ROOT" -maxdepth 1 -name "$PATTERN" 2>/dev/null | wc -l)
    REMAINING_LOGS=$((REMAINING_LOGS + COUNT))
done

# 남은 보고서 파일 수
REMAINING_PDF=0
[ -d "$KR_PDF_DIR" ] && REMAINING_PDF=$((REMAINING_PDF + $(find "$KR_PDF_DIR" -name "*.pdf" 2>/dev/null | wc -l)))
[ -d "$US_PDF_DIR" ] && REMAINING_PDF=$((REMAINING_PDF + $(find "$US_PDF_DIR" -name "*.pdf" 2>/dev/null | wc -l)))

echo "$(date): 로그 정리 완료 - 남은 로그: $REMAINING_LOGS, 남은 PDF: $REMAINING_PDF" >> "$PROJECT_ROOT/utils/log_cleanup.log"
