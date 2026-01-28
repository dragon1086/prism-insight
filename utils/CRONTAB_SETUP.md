# 📅 PRISM-INSIGHT Crontab Setup Guide

> 📖 [한국어 문서](CRONTAB_SETUP_ko.md)

## Overview
PRISM-INSIGHT uses crontab to automate stock market analysis. This document explains how to set up automatic execution schedules on your system.

## 🚀 Quick Start

### 1. Simple Setup (Recommended)
```bash
# Grant execution permission
chmod +x setup_crontab_simple.sh

# Run script
./setup_crontab_simple.sh
```

### 2. Advanced Setup
```bash
# Grant execution permission
chmod +x setup_crontab.sh

# Interactive setup
./setup_crontab.sh

# Or automatic setup using environment variables
PROJECT_DIR=/opt/prism-insight PYTHON_PATH=/usr/bin/python3 ./setup_crontab.sh --non-interactive
```

## 📋 Execution Schedule

### Default Schedule (Korea Time)

| Time | Task | Description |
|------|------|------|
| 02:00 | Config Backup | Backup important configs and database |
| 03:00 (Sun) | Memory Compression | Weekly trading memory compression & cleanup |
| 03:00 | Log Cleanup | Delete old log files |
| 07:00 | Data Update | Update stock information before market opens |
| 09:30 | Morning Analysis | Detect and analyze surging stocks after market opens |
| 11:05 | Dashboard Refresh | Update dashboard JSON data (morning) |
| 15:40 | Afternoon Analysis | Comprehensive analysis after market closes |
| 17:00 | Performance Tracker | Update 7/14/30 day performance tracking |
| 17:10 | Dashboard Refresh | Update dashboard JSON data (afternoon) |
| 18:00 | Portfolio Report | (Optional) Daily trading performance report |

### Schedule Details

#### 1. **Morning Analysis (09:30)**
- Based on 10-minute data after market opens
- Detect gap-up and volume surge stocks
- Real-time market trend analysis

#### 2. **Afternoon Analysis (15:40)**
- Comprehensive analysis after market closes
- Analyze intraday gains and closing strength
- Generate detailed AI reports

#### 3. **Data Update (07:00)**
- Update stock master information
- Collect previous day's trading data
- Check system readiness

#### 4. **Log Cleanup (03:00)**
- Delete logs older than 30 days
- Manage disk space
- System optimization

#### 5. **Config Backup (02:00)**
- Backup .env, mcp_agent.*.yaml files
- Backup stock_tracking_db.sqlite
- Backup trading/config/kis_devlp.yaml
- Backup examples/streamlit/config.py
- Auto-delete backups older than 7 days

#### 6. **Memory Compression (Sundays 03:00)**
- Compress trading journal memory
- Cleanup low-confidence principles/intuitions
- Archive old Layer 3 journals
- Token accumulation prevention

#### 7. **Performance Tracker (17:00)**
- Update 7/14/30 day price tracking
- Calculate returns for analyzed stocks
- Track missed opportunities and avoided losses

#### 8. **Dashboard Refresh (11:05, 17:10)**
- Generate dashboard_data.json from database
- Update trading insights data
- Generate English translation (optional)

## 🛠️ Manual Setup

### 1. Edit Crontab
```bash
crontab -e
```

### 2. Set Environment Variables
```bash
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
PYTHONPATH=/path/to/prism-insight
```

### 3. Add Schedules
```bash
# -----------------------------------------------------------------------------
# 백업 및 유지보수 (Backup & Maintenance)
# -----------------------------------------------------------------------------

# Daily config & database backup at 2 AM
0 2 * * * chmod +x /path/to/prism-insight/utils/backup_configs.sh && /path/to/prism-insight/utils/backup_configs.sh

# Weekly trading memory compression at 3 AM on Sundays
0 3 * * 0 cd /path/to/prism-insight && python compress_trading_memory.py >> logs/compression.log 2>&1

# Log cleanup (daily at 3 AM)
0 3 * * * cd /path/to/prism-insight && utils/cleanup_logs.sh

# -----------------------------------------------------------------------------
# 주식 분석 배치 (Stock Analysis Batch)
# -----------------------------------------------------------------------------

# Data update before market opens (Mon-Fri 7 AM)
0 7 * * 1-5 cd /path/to/prism-insight && python update_stock_data.py >> logs/update.log 2>&1

# Morning analysis (Mon-Fri 9:30 AM)
30 9 * * 1-5 cd /path/to/prism-insight && python stock_analysis_orchestrator.py --mode morning >> logs/morning.log 2>&1

# Afternoon analysis (Mon-Fri 3:40 PM)
40 15 * * 1-5 cd /path/to/prism-insight && python stock_analysis_orchestrator.py --mode afternoon >> logs/afternoon.log 2>&1

# -----------------------------------------------------------------------------
# 대시보드 및 성과 추적 (Dashboard & Performance Tracking)
# -----------------------------------------------------------------------------

# Dashboard JSON refresh - Morning (Mon-Fri 11:05 AM)
5 11 * * 1-5 cd /path/to/prism-insight && python examples/generate_dashboard_json.py >> logs/generate_dashboard_json.log 2>&1

# Performance tracker daily update (Mon-Fri 5 PM) - must run before evening dashboard refresh
0 17 * * 1-5 cd /path/to/prism-insight && python performance_tracker_batch.py >> logs/performance_tracker.log 2>&1

# Dashboard JSON refresh - Afternoon (Mon-Fri 5:10 PM)
10 17 * * 1-5 cd /path/to/prism-insight && python examples/generate_dashboard_json.py >> logs/generate_dashboard_json.log 2>&1
```

## 🔧 Environment-Specific Setup

### Rocky Linux / CentOS / RHEL
```bash
# Set SELinux context (if needed)
sudo semanage fcontext -a -t bin_t "/path/to/prism-insight/.*\.py"
sudo restorecon -Rv /path/to/prism-insight/

# Firewall settings (when using Telegram bot)
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### Ubuntu / Debian
```bash
# When using system Python
sudo apt-get install python3-venv python3-pip

# Set permissions
chmod +x *.sh
chmod +x *.py
```

### macOS
```bash
# Recommend using Homebrew Python
brew install python3

# Use launchd (instead of crontab)
# Create ~/Library/LaunchAgents/com.prism-insight.plist
```

## 🐍 Python Environment-Specific Setup

### Using pyenv
```bash
# When .python-version file exists
PYTHON_PATH="$HOME/.pyenv/shims/python"
```

### Using venv
```bash
# Run after activating virtual environment
source /path/to/venv/bin/activate && python script.py
```

### Using conda
```bash
# Activate conda environment
eval "$(conda shell.bash hook)"
conda activate prism-insight
```

## 📊 Log Checking

### Real-time Log Monitoring
```bash
# Morning analysis log
tail -f logs/stock_analysis_morning_$(date +%Y%m%d).log

# Afternoon analysis log
tail -f logs/stock_analysis_afternoon_$(date +%Y%m%d).log

# Check all logs
tail -f logs/*.log
```

### Log Analysis
```bash
# Check today's errors
grep ERROR logs/*$(date +%Y%m%d)*.log

# Number of successful analyses
grep "분석 완료" logs/*.log | wc -l

# Last 5 days log summary
for i in {0..4}; do
    date -d "$i days ago" +%Y%m%d
    grep -c "완료" logs/*$(date -d "$i days ago" +%Y%m%d)*.log
done
```

## 🔍 Troubleshooting

### 1. Crontab Not Running
```bash
# Check cron service
sudo systemctl status crond  # RHEL/CentOS
sudo systemctl status cron   # Ubuntu/Debian

# Restart service
sudo systemctl restart crond
```

### 2. Python Not Found
```bash
# Check PATH
which python3

# Use full path in crontab
/usr/bin/python3 script.py
```

### 3. Permission Errors
```bash
# Grant execution permission
chmod +x *.py *.sh

# Check ownership
ls -la

# Change ownership if needed
chown -R $USER:$USER /path/to/prism-insight
```

### 4. Timezone Issues
```bash
# Check system timezone
timedatectl

# Set Korea timezone
sudo timedatectl set-timezone Asia/Seoul

# Specify timezone in crontab
TZ=Asia/Seoul
30 9 * * 1-5 command
```

## 📝 Maintenance

### Backup
```bash
# Backup crontab
crontab -l > crontab_backup_$(date +%Y%m%d).txt

# Restore
crontab crontab_backup_20250113.txt
```

### Temporary Pause
```bash
# Stop all
crontab -r

# Comment out specific task only
crontab -e
# 30 9 * * 1-5 ...  <- Add #
```

### Testing
```bash
# Manual execution test
cd /path/to/prism-insight
python stock_analysis_orchestrator.py --mode morning

# Simulate cron environment
env -i SHELL=/bin/bash PATH=/usr/bin:/bin python script.py

# Check next execution time
crontab -l | grep -v "^#" | cut -f 1-5 -d ' ' | while read schedule; do
    echo "$schedule -> $(date -d "$schedule" 2>/dev/null || echo "Daily/Weekly repeat")"
done
```

## 🎯 Best Practices

### 1. **Setup Log Rotation**
```bash
# Create /etc/logrotate.d/prism-insight
/path/to/prism-insight/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 640 user group
    sharedscripts
}
```

### 2. **Setup Error Notifications**
```bash
# Email notification on error
MAILTO=your-email@example.com
30 9 * * 1-5 /path/to/script.py || echo "Morning analysis failed" | mail -s "PRISM-INSIGHT Error" $MAILTO
```

### 3. **Health Check**
```bash
# Execution status monitoring script
#!/bin/bash
# health_check.sh

LAST_RUN=$(find logs -name "*$(date +%Y%m%d)*.log" -mmin -60 | wc -l)
if [ $LAST_RUN -eq 0 ]; then
    echo "Warning: No execution record within the last hour"
    # Notification logic
fi
```

### 4. **Resource Limits**
```bash
# CPU/memory usage limits
30 9 * * 1-5 nice -n 10 ionice -c 3 timeout 3600 python script.py

# nice: Lower CPU priority
# ionice: Lower I/O priority
# timeout: Maximum execution time limit (1 hour)
```

## 📚 References

### Cron Expression Guide

| Field | Values | Description |
|------|-----|------|
| Minute | 0-59 | On the hour: 0 |
| Hour | 0-23 | 9 AM: 9 |
| Day | 1-31 | Every day: * |
| Month | 1-12 | Every month: * |
| Day of Week | 0-7 | Mon-Fri: 1-5 (0,7=Sunday) |

### Special Characters

- `*` : All values
- `,` : Value list (e.g., 1,3,5)
- `-` : Range (e.g., 1-5)
- `/` : Interval (e.g., */5 = every 5 minutes)

### Useful Examples

```bash
# Every 30 minutes
*/30 * * * * command

# Every hour from 9 AM to 6 PM on weekdays
0 9-18 * * 1-5 command

# Every Monday at 8 AM
0 8 * * 1 command

# 1st and 15th of every month
0 0 1,15 * * command

# Quarterly (1st of Jan, Apr, Jul, Oct)
0 0 1 1,4,7,10 * command
```

## ⚠️ Important Notes

1. **Market Holiday Handling**
   - Implement holiday check logic inside scripts
   - Reference KRX market closure calendar

2. **Timezone Settings**
   - Adjust time if server timezone is not KST
   - For UTC servers: Consider 9-hour difference

3. **Permission Management**
   - Use environment variables for sensitive information (API keys, etc.)
   - Be careful not to expose personal information in log files

4. **Backup Policy**
   - Regular database backups
   - Archive important logs

## 🤝 Getting Help

If you encounter problems or need assistance:

1. Inquire on [GitHub Issues](https://github.com/yourusername/prism-insight/issues)
2. Get community support on [Telegram channel](https://t.me/stock_ai_agent)
3. Check log files (`logs/` directory)
4. Refer to the troubleshooting section in this document

---

*Last Updated: January 2026*
