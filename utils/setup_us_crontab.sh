#!/bin/bash

# =============================================================================
# PRISM-INSIGHT US Stock Market Crontab Setup Script
# =============================================================================
# This script configures crontab for US stock market analysis.
# US Market Hours: 09:30-16:00 EST (23:30-06:00 KST)
#
# IMPORTANT: Time conversion (Server runs on KST)
# - EST (Nov-Mar): EST = KST - 14 hours
#   - 09:30 EST = 23:30 KST (same day)
#   - 16:00 EST = 06:00 KST (next day)
# - EDT (Mar-Nov): EDT = KST - 13 hours
#   - 09:30 EDT = 22:30 KST (same day)
#   - 16:00 EDT = 05:00 KST (next day)
#
# Default times use EST. Adjust for EDT during daylight saving (Mar-Nov).
# =============================================================================

set -e

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# =============================================================================
# Configuration Variables (Modify as needed)
# =============================================================================

# Project path (default: parent of utils directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(dirname "$SCRIPT_DIR")}"

# Python path auto-detection
detect_python_path() {
    if command -v pyenv &> /dev/null && [ -d "$HOME/.pyenv" ]; then
        echo "$HOME/.pyenv/shims/python"
    elif [ -f "$PROJECT_DIR/venv/bin/python" ]; then
        echo "$PROJECT_DIR/venv/bin/python"
    elif [ -f "$PROJECT_DIR/.venv/bin/python" ]; then
        echo "$PROJECT_DIR/.venv/bin/python"
    else
        echo "$(command -v python3 || command -v python)"
    fi
}

PYTHON_PATH="${PYTHON_PATH:-$(detect_python_path)}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
USER_HOME="${HOME:-/home/$(whoami)}"

# =============================================================================
# US Market Schedule Times (KST - Server Time)
# =============================================================================
# Adjust these if your server is in a different timezone
# Yahoo Finance has 15-20 min data delay - schedules adjusted accordingly
# US market has no price limits - 3 runs per day for better coverage

# === EST (Standard Time: November - March) ===
# Morning batch: 10:15 EST = 00:15 KST (45 min after open for data delay)
US_MORNING_BATCH_TIME_EST="15 0"
# Midday batch: 12:30 EST = 02:30 KST (lunch time monitoring)
US_MIDDAY_BATCH_TIME_EST="30 2"
# Afternoon batch: 16:30 EST = 06:30 KST (30 min after close)
US_AFTERNOON_BATCH_TIME_EST="30 6"
# Performance tracker: 17:30 EST = 07:30 KST
US_PERFORMANCE_TRACKER_TIME_EST="30 7"
# Dashboard refresh: 18:00 EST = 08:00 KST
US_DASHBOARD_TIME_EST="0 8"

# === EDT (Daylight Time: March - November) ===
# Morning batch: 10:15 EDT = 23:15 KST (previous day)
US_MORNING_BATCH_TIME_EDT="15 23"
# Midday batch: 12:30 EDT = 01:30 KST
US_MIDDAY_BATCH_TIME_EDT="30 1"
# Afternoon batch: 16:30 EDT = 05:30 KST
US_AFTERNOON_BATCH_TIME_EDT="30 5"
# Performance tracker: 17:30 EDT = 06:30 KST
US_PERFORMANCE_TRACKER_TIME_EDT="30 6"
# Dashboard refresh: 18:00 EDT = 07:00 KST
US_DASHBOARD_TIME_EDT="0 7"

# Choose which timezone to use (default: EST)
# Change to EDT during daylight saving time (March-November)
TIMEZONE_MODE="${TIMEZONE_MODE:-EST}"

if [ "$TIMEZONE_MODE" = "EDT" ]; then
    US_MORNING_BATCH_TIME="$US_MORNING_BATCH_TIME_EDT"
    US_MIDDAY_BATCH_TIME="$US_MIDDAY_BATCH_TIME_EDT"
    US_AFTERNOON_BATCH_TIME="$US_AFTERNOON_BATCH_TIME_EDT"
    US_PERFORMANCE_TRACKER_TIME="$US_PERFORMANCE_TRACKER_TIME_EDT"
    US_DASHBOARD_TIME="$US_DASHBOARD_TIME_EDT"
else
    US_MORNING_BATCH_TIME="$US_MORNING_BATCH_TIME_EST"
    US_MIDDAY_BATCH_TIME="$US_MIDDAY_BATCH_TIME_EST"
    US_AFTERNOON_BATCH_TIME="$US_AFTERNOON_BATCH_TIME_EST"
    US_PERFORMANCE_TRACKER_TIME="$US_PERFORMANCE_TRACKER_TIME_EST"
    US_DASHBOARD_TIME="$US_DASHBOARD_TIME_EST"
fi

# Other schedule times (KST)
US_LOG_CLEANUP_TIME="30 3"           # 03:30 KST (daily)
US_MEMORY_COMPRESSION_TIME="0 4"     # 04:00 KST (Sunday only)

# =============================================================================
# Functions
# =============================================================================

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

validate_environment() {
    log_info "Validating environment..."

    if [ ! -d "$PROJECT_DIR" ]; then
        log_error "Project directory not found: $PROJECT_DIR"
        exit 1
    fi

    if [ ! -f "$PYTHON_PATH" ]; then
        log_error "Python executable not found: $PYTHON_PATH"
        exit 1
    fi

    # Check for US-specific files
    local us_files=(
        "prism-us/us_stock_analysis_orchestrator.py"
        "prism-us/us_trigger_batch.py"
        "prism-us/check_market_day.py"
    )

    for file in "${us_files[@]}"; do
        if [ ! -f "$PROJECT_DIR/$file" ]; then
            log_warn "US module file not found: $file"
        fi
    done

    # Create log directory
    if [ ! -d "$LOG_DIR" ]; then
        log_info "Creating log directory: $LOG_DIR"
        mkdir -p "$LOG_DIR"
    fi

    log_success "Environment validation complete"
}

generate_path() {
    local paths=()

    if [ -d "$USER_HOME/.pyenv" ]; then
        paths+=("$USER_HOME/.pyenv/plugins/pyenv-virtualenv/shims")
        paths+=("$USER_HOME/.pyenv/shims")
        paths+=("$USER_HOME/.pyenv/bin")
    fi

    paths+=("/usr/local/sbin")
    paths+=("/usr/local/bin")
    paths+=("/usr/sbin")
    paths+=("/usr/bin")
    paths+=("/sbin")
    paths+=("/bin")

    if [ -d "$USER_HOME/.local/bin" ]; then
        paths+=("$USER_HOME/.local/bin")
    fi

    if [ -d "$USER_HOME/.cargo/bin" ]; then
        paths+=("$USER_HOME/.cargo/bin")
    fi

    local IFS=':'
    echo "${paths[*]}"
}

generate_crontab_entries() {
    cat << EOF
# =============================================================================
# PRISM-INSIGHT US Stock Market Auto-Execution Schedule
# Generated by setup_us_crontab.sh on $(date)
# Timezone Mode: $TIMEZONE_MODE
# =============================================================================
#
# US Market Hours: 09:30-16:00 EST (23:30-06:00 KST)
# US market has NO price limits - 3 runs per day for better signal coverage
# Yahoo Finance data delay: 15-20 min (schedules adjusted accordingly)
#
# Time Conversion Reference (Server: KST):
# - EST (Nov-Mar): 10:15 EST = 00:15 KST, 16:30 EST = 06:30 KST
# - EDT (Mar-Nov): 10:15 EDT = 23:15 KST, 16:30 EDT = 05:30 KST
#
# IMPORTANT: Update times when daylight saving changes!
# - March (to EDT): Run with TIMEZONE_MODE=EDT
# - November (to EST): Run with TIMEZONE_MODE=EST
# =============================================================================

# Environment Variables
SHELL=/bin/bash
PATH=$(generate_path)
PYTHONPATH=$PROJECT_DIR

# -----------------------------------------------------------------------------
# US Stock Analysis Batch Jobs (3 runs per day)
# -----------------------------------------------------------------------------

# US Morning batch: 45 min after market open (for Yahoo Finance data delay)
# Current mode: $TIMEZONE_MODE
# EST: 10:15 EST = 00:15 KST | EDT: 10:15 EDT = 23:15 KST
# Runs Tuesday-Saturday (Mon-Fri US time crosses midnight in KST)
$US_MORNING_BATCH_TIME * * 2-6 cd $PROJECT_DIR && $PYTHON_PATH prism-us/us_stock_analysis_orchestrator.py --mode morning >> $LOG_DIR/us_morning_\$(date +\%Y\%m\%d).log 2>&1

# US Midday batch: Lunch time monitoring for intraday movements
# Current mode: $TIMEZONE_MODE
# EST: 12:30 EST = 02:30 KST | EDT: 12:30 EDT = 01:30 KST
$US_MIDDAY_BATCH_TIME * * 2-6 cd $PROJECT_DIR && $PYTHON_PATH prism-us/us_stock_analysis_orchestrator.py --mode midday >> $LOG_DIR/us_midday_\$(date +\%Y\%m\%d).log 2>&1

# US Afternoon batch: 30 min after market close
# Current mode: $TIMEZONE_MODE
# EST: 16:30 EST = 06:30 KST | EDT: 16:30 EDT = 05:30 KST
$US_AFTERNOON_BATCH_TIME * * 2-6 cd $PROJECT_DIR && $PYTHON_PATH prism-us/us_stock_analysis_orchestrator.py --mode afternoon >> $LOG_DIR/us_afternoon_\$(date +\%Y\%m\%d).log 2>&1

# -----------------------------------------------------------------------------
# Performance Tracking & Dashboard
# -----------------------------------------------------------------------------

# US Performance tracker: 1 hour after market close
# Tracks 7/14/30 day performance of analyzed stocks
# EST: 17:30 EST = 07:30 KST | EDT: 17:30 EDT = 06:30 KST
$US_PERFORMANCE_TRACKER_TIME * * 2-6 cd $PROJECT_DIR && $PYTHON_PATH prism-us/us_performance_tracker_batch.py >> $LOG_DIR/us_performance_\$(date +\%Y\%m\%d).log 2>&1

# US Dashboard refresh: After performance tracking
# EST: 18:00 EST = 08:00 KST | EDT: 18:00 EDT = 07:00 KST
$US_DASHBOARD_TIME * * 2-6 cd $PROJECT_DIR/examples && $PYTHON_PATH generate_us_dashboard_json.py >> $LOG_DIR/us_dashboard.log 2>&1

# -----------------------------------------------------------------------------
# Maintenance (US Module)
# -----------------------------------------------------------------------------

# Log cleanup for US logs (daily at 03:30 KST) - keep 30 days
$US_LOG_CLEANUP_TIME * * * find $LOG_DIR -name "us_*.log" -mtime +30 -delete

# Memory compression for US trading data (Sunday 04:00 KST)
$US_MEMORY_COMPRESSION_TIME * * 0 cd $PROJECT_DIR && $PYTHON_PATH prism-us/compress_us_trading_memory.py >> $LOG_DIR/us_compression.log 2>&1 || true

# =============================================================================
# Optional: Uncomment to enable additional features
# =============================================================================

# US Portfolio report after market close
# 45 6 * * 2-6 cd $PROJECT_DIR && $PYTHON_PATH prism-us/trading/us_portfolio_reporter.py >> $LOG_DIR/us_portfolio_\$(date +\%Y\%m\%d).log 2>&1

EOF
}

backup_crontab() {
    local backup_file="$PROJECT_DIR/crontab_us_backup_$(date +%Y%m%d_%H%M%S).txt"

    if crontab -l &> /dev/null; then
        log_info "Backing up current crontab: $backup_file"
        crontab -l > "$backup_file"
        log_success "Backup complete"
    else
        log_info "No existing crontab found"
    fi
}

install_crontab() {
    local temp_cron="/tmp/prism_us_crontab_$$"

    # Get existing crontab
    if crontab -l &> /dev/null; then
        crontab -l > "$temp_cron"

        # Remove existing US entries
        sed -i '/PRISM-INSIGHT US Stock/,/^$/d' "$temp_cron"
        echo "" >> "$temp_cron"
    else
        > "$temp_cron"
    fi

    # Add new entries
    generate_crontab_entries >> "$temp_cron"

    # Install crontab
    crontab "$temp_cron"
    rm -f "$temp_cron"

    log_success "US crontab installed successfully!"
}

verify_installation() {
    log_info "Verifying installed crontab:"
    echo "----------------------------------------"
    crontab -l | grep -A 30 "PRISM-INSIGHT US" || log_warn "No US entries found"
    echo "----------------------------------------"
}

uninstall_crontab() {
    log_info "Removing PRISM-INSIGHT US crontab entries..."

    local temp_cron="/tmp/prism_us_crontab_remove_$$"

    if crontab -l &> /dev/null; then
        crontab -l > "$temp_cron"
        sed -i '/PRISM-INSIGHT US Stock/,/^$/d' "$temp_cron"

        if [ -s "$temp_cron" ]; then
            crontab "$temp_cron"
        else
            crontab -r
        fi

        rm -f "$temp_cron"
        log_success "US crontab entries removed"
    else
        log_info "No crontab to remove"
    fi
}

show_help() {
    cat << EOF
Usage: $0 [options]

Options:
  -h, --help        Show this help
  -i, --install     Install US crontab (default)
  -u, --uninstall   Remove US crontab entries
  -s, --show        Show current US crontab
  -b, --backup      Backup current crontab
  --est             Use EST times (Nov-Mar, default)
  --edt             Use EDT times (Mar-Nov, daylight saving)
  --non-interactive Skip interactive prompts

Environment Variables:
  PROJECT_DIR     Project directory path
  PYTHON_PATH     Python executable path
  LOG_DIR         Log directory path
  TIMEZONE_MODE   EST or EDT (default: EST)

Examples:
  # Install with EST times (winter)
  $0 --est

  # Install with EDT times (summer/daylight saving)
  $0 --edt

  # Install with custom paths
  PROJECT_DIR=/opt/prism-insight TIMEZONE_MODE=EDT $0 --non-interactive

US Market Schedule (in KST):
  Morning batch (45 min after open):
    - EST: 00:15 KST (10:15 EST)
    - EDT: 23:15 KST (10:15 EDT)

  Midday batch (lunch time):
    - EST: 02:30 KST (12:30 EST)
    - EDT: 01:30 KST (12:30 EDT)

  Afternoon batch (30 min after close):
    - EST: 06:30 KST (16:30 EST)
    - EDT: 05:30 KST (16:30 EDT)

  Performance tracker:
    - EST: 07:30 KST (17:30 EST)
    - EDT: 06:30 KST (17:30 EDT)

  Dashboard refresh:
    - EST: 08:00 KST (18:00 EST)
    - EDT: 07:00 KST (18:00 EDT)

EOF
}

interactive_setup() {
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}   PRISM-INSIGHT US Crontab Setup Tool${NC}"
    echo -e "${BLUE}================================================${NC}"
    echo

    # Project path
    read -p "Project path [$PROJECT_DIR]: " input_dir
    PROJECT_DIR="${input_dir:-$PROJECT_DIR}"

    # Python path
    read -p "Python executable [$PYTHON_PATH]: " input_python
    PYTHON_PATH="${input_python:-$PYTHON_PATH}"

    # Timezone mode
    echo
    echo "Select timezone mode:"
    echo "  1) EST (Standard Time: November - March)"
    echo "  2) EDT (Daylight Time: March - November)"
    read -p "Choice [1]: " tz_choice
    case $tz_choice in
        2)
            TIMEZONE_MODE="EDT"
            US_MORNING_BATCH_TIME="$US_MORNING_BATCH_TIME_EDT"
            US_AFTERNOON_BATCH_TIME="$US_AFTERNOON_BATCH_TIME_EDT"
            ;;
        *)
            TIMEZONE_MODE="EST"
            US_MORNING_BATCH_TIME="$US_MORNING_BATCH_TIME_EST"
            US_AFTERNOON_BATCH_TIME="$US_AFTERNOON_BATCH_TIME_EST"
            ;;
    esac

    echo
    log_info "Configuration summary:"
    echo "  Project path: $PROJECT_DIR"
    echo "  Python path: $PYTHON_PATH"
    echo "  Timezone: $TIMEZONE_MODE"
    echo "  Morning batch: $US_MORNING_BATCH_TIME (KST)"
    echo "  Midday batch: $US_MIDDAY_BATCH_TIME (KST)"
    echo "  Afternoon batch: $US_AFTERNOON_BATCH_TIME (KST)"
    echo "  Performance tracker: $US_PERFORMANCE_TRACKER_TIME (KST)"
    echo "  Dashboard refresh: $US_DASHBOARD_TIME (KST)"
    echo

    read -p "Continue? (y/N): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        log_info "Cancelled"
        exit 0
    fi
}

# =============================================================================
# Main
# =============================================================================

main() {
    local action="install"
    local interactive=true

    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -i|--install)
                action="install"
                shift
                ;;
            -u|--uninstall)
                action="uninstall"
                shift
                ;;
            -s|--show)
                action="show"
                shift
                ;;
            -b|--backup)
                action="backup"
                shift
                ;;
            --est)
                TIMEZONE_MODE="EST"
                US_MORNING_BATCH_TIME="$US_MORNING_BATCH_TIME_EST"
                US_AFTERNOON_BATCH_TIME="$US_AFTERNOON_BATCH_TIME_EST"
                shift
                ;;
            --edt)
                TIMEZONE_MODE="EDT"
                US_MORNING_BATCH_TIME="$US_MORNING_BATCH_TIME_EDT"
                US_AFTERNOON_BATCH_TIME="$US_AFTERNOON_BATCH_TIME_EDT"
                shift
                ;;
            --non-interactive)
                interactive=false
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done

    case $action in
        install)
            if $interactive; then
                interactive_setup
            fi
            validate_environment
            backup_crontab
            install_crontab
            verify_installation

            echo
            log_success "Installation complete!"
            log_info "Verify with: crontab -l"
            log_info "Logs will be in: $LOG_DIR"
            echo
            log_warn "REMINDER: Update timezone when daylight saving changes!"
            log_warn "  - March (to EDT): Run with --edt"
            log_warn "  - November (to EST): Run with --est"
            ;;

        uninstall)
            if $interactive; then
                read -p "Remove US crontab entries? (y/N): " confirm
                if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
                    log_info "Cancelled"
                    exit 0
                fi
            fi
            backup_crontab
            uninstall_crontab
            ;;

        show)
            log_info "Current US crontab entries:"
            echo "----------------------------------------"
            crontab -l 2>/dev/null | grep -A 30 "PRISM-INSIGHT US" || log_warn "No US entries found"
            ;;

        backup)
            backup_crontab
            ;;
    esac
}

main "$@"
