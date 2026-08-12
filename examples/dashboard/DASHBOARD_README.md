# Dashboard Setup Guide

> 📖 [한국어 문서](DASHBOARD_README_ko.md)

This document guides you through the installation and execution of the dashboard frontend.

## Prerequisites

- Node.js and npm installed
- Python environment configured
- Access to project root directory
- PM2 installed (`npm install -g pm2`)

## Port Information

- **Dashboard**: Port 3000 (Next.js default port)
- **Streamlit App**: Port 8501 (Streamlit default port)

> The two services use different ports, so they can run **without port conflicts** simultaneously.

## Stance on the dashboard domain

The dashboard app server exposes the public Stance API at
`https://analysis.stocksimulation.kr/api/stance/v1`. It proxies only the supported
Stance routes to the internal single-worker service; the ledger remains private on
`127.0.0.1:8800`.

```bash
cp .env.example .env.production

# .env.production
STANCE_INTERNAL_URL=http://127.0.0.1:8800
STANCE_PUBLIC_REGISTRATION=true
STANCE_REGISTRATION_TOKEN=<same-long-random-secret-as-the-internal-service>
```

The registration secret is server-only. Never prefix it with `NEXT_PUBLIC_`, return it
from an API response, or put it in browser code. The Stance tab collects registration
details and displays the resulting `stk_...` integration key once.

Public surface:

- `/?tab=stance` — registration and leaderboard
- `/api/stance/v1/stances` — submit a decision
- `/api/stance/v1/portfolio` — authenticated strategy snapshot
- `/api/stance/v1/keys/rotate` — rotate an integration key
- `/api/stance/v1/leaderboard`, `/markets`, `/health` — public reads

Keep the internal Stance service bound to loopback with exactly one worker. Add an
edge or reverse-proxy rate limit for `/api/stance/v1/strategies`; the app also applies
a small per-process registration guard.

### If Port Change is Needed

If port 3000 is already in use, you can change the port as follows:

```bash
# Change port in development mode
PORT=3001 npm run dev

# Change port in production mode
PORT=3001 npm start

# Change port with PM2
PORT=3001 pm2 start npm --name "dashboard" -- start
```

## Setup Instructions

### 1. Data Auto-Refresh Setup (Crontab)

Add the following to crontab to periodically refresh dashboard data:

```bash
# Edit crontab
crontab -e

# Add the following content
# Refresh dashboard data daily at 11:05 AM
05 11 * * * cd /project-root/examples && python generate_dashboard_json.py >> /project-root/logs/generate_dashboard_json.log 2>&1

# Refresh dashboard data daily at 05:05 PM
05 17 * * * cd /project-root/examples && python generate_dashboard_json.py >> /project-root/logs/generate_dashboard_json.log 2>&1
```

> **Note**: Replace `/project-root` with the actual absolute path of your project.

### 2. Navigate to Dashboard Directory

```bash
cd examples/dashboard
```

### 3. Install Dependencies

```bash
npm install react-is --legacy-peer-deps
```

### 4. Build Project

```bash
npm run build
```

### 5. Run Dashboard with PM2

```bash
# Start application with PM2 (default port 3000)
pm2 start npm --name "dashboard" -- start

# To start on a specific port
PORT=3001 pm2 start npm --name "dashboard" -- start

# Check PM2 process list
pm2 list

# Check logs
pm2 logs dashboard

# Setup auto-start on server reboot
pm2 startup
pm2 save
```

## PM2 Key Commands

```bash
# Check dashboard status
pm2 status

# Restart dashboard
pm2 restart dashboard

# Stop dashboard
pm2 stop dashboard

# Delete dashboard
pm2 delete dashboard

# View real-time logs
pm2 logs dashboard --lines 100

# Monitoring
pm2 monit
```

## Troubleshooting

- If errors occur during dependency installation, use the `--legacy-peer-deps` flag.
- After crontab setup, check the log file (`/project-root/logs/generate_dashboard_json.log`) to verify the script is running normally.
- Ensure the logs directory (`/project-root/logs`) exists. If not, create it:
  ```bash
  mkdir -p /project-root/logs
  ```
- If PM2 is not installed, install it with the following command:
  ```bash
  npm install -g pm2
  ```
- If port 3000 is already in use:
  ```bash
  # Check port usage
  lsof -i :3000

  # Or run on a different port
  PORT=3001 pm2 start npm --name "dashboard" -- start
  ```

## Verify Execution

Once the dashboard is running successfully, you can access it in your browser at:

- Default port: `http://localhost:3000`
- Custom port: `http://localhost:{your_custom_port}`

You can also check the process status in PM2 dashboard:
```bash
pm2 status
```

## Service Structure

```
Project
├── examples/
│   ├── dashboard/          # Next.js dashboard (port 3000)
│   └── streamlit/          # Streamlit app (port 8501)
└── ...
```

Both services run independently and can operate simultaneously without port conflicts.
