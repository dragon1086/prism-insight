#!/bin/bash
set -e

# =============================================================================
# PRISM-INSIGHT Docker Entrypoint Script
# =============================================================================

echo "========================================"
echo "  PRISM-INSIGHT Container Starting"
echo "  Timezone: $TZ"
echo "  Date: $(date)"
echo "========================================"

# Ensure log directory exists
mkdir -p /app/prism-insight/logs

# -----------------------------------------------------------------------------
# Cron Setup
# -----------------------------------------------------------------------------

# Check if ENABLE_CRON is set (default: true)
ENABLE_CRON="${ENABLE_CRON:-true}"

if [ "$ENABLE_CRON" = "true" ]; then
    echo "[INIT] Setting up cron..."

    # Install crontab from docker/crontab file
    if [ -f /app/prism-insight/docker/crontab ]; then
        crontab /app/prism-insight/docker/crontab
        echo "[INIT] Crontab installed successfully"

        # Show installed crontab
        echo "[INIT] Installed cron jobs:"
        crontab -l | grep -v "^#" | grep -v "^$" | head -20
    else
        echo "[WARN] Crontab file not found: /app/prism-insight/docker/crontab"
    fi

    # Start cron service
    service cron start
    echo "[INIT] Cron service started"
else
    echo "[INIT] Cron disabled (ENABLE_CRON=false)"
fi

# -----------------------------------------------------------------------------
# Environment Check
# -----------------------------------------------------------------------------

echo ""
echo "[INIT] Environment Check:"
echo "  - Python: $(python3 --version)"
echo "  - Node.js: $(node --version)"
echo "  - Working Dir: $(pwd)"

# Check if .env has been configured
if grep -q "your_telegram_bot_token" /app/prism-insight/.env 2>/dev/null; then
    echo ""
    echo "[WARN] .env file contains example values!"
    echo "[WARN] Please configure your API keys in .env"
fi

# -----------------------------------------------------------------------------
# Run Command or Keep Container Alive
# -----------------------------------------------------------------------------

echo ""
echo "[INIT] Initialization complete"
echo "========================================"

# If arguments are passed, execute them
if [ $# -gt 0 ]; then
    echo "[EXEC] Running: $@"
    exec "$@"
else
    # Keep container running (for cron and interactive use)
    echo "[IDLE] Container is running. Cron jobs will execute on schedule."
    echo "[IDLE] Use 'docker exec' to interact with the container."
    echo "[IDLE] Press Ctrl+C to stop."

    # Keep alive while logging cron output
    tail -f /var/log/cron.log 2>/dev/null || tail -f /dev/null
fi
