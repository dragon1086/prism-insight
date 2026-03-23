# ChatGPT OAuth Proxy Setup Guide

## Prerequisites

- ChatGPT Plus ($20/mo) or Pro ($200/mo) subscription
- Python 3.10+
- prism-insight project installed with `aiohttp`

## Quick Start

### 1. Login (one-time)

```bash
python -m cores.chatgpt_proxy.oauth_login
```

This opens your browser for ChatGPT login. After authentication, tokens are saved to `~/.config/prism-insight/chatgpt_auth.json`.

### 2. Check Status

```bash
python -c "import asyncio; from cores.chatgpt_proxy.oauth_login import status; asyncio.run(status())"
```

### 3. Run with OAuth

```bash
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### 4. Run without OAuth (standard API key)

```bash
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

Or explicitly:

```bash
python stock_analysis_orchestrator.py --mode morning --no-telegram --no-proxy
```

## How It Works

1. An in-process aiohttp proxy starts on `localhost:18741`
2. `OPENAI_BASE_URL` is set to point at the proxy
3. All OpenAI API calls from mcp-agent go through the proxy
4. The proxy translates Chat Completions API to ChatGPT Responses API
5. OAuth tokens are injected automatically (with auto-refresh)

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `PRISM_OPENAI_AUTH_MODE` | (not set) | Set to `chatgpt_oauth` to enable proxy |
| `PRISM_CHATGPT_PROXY_PORT` | `18741` | Proxy server port |

## Token Management

- Tokens stored at: `~/.config/prism-insight/chatgpt_auth.json`
- File permissions: `0o600` (owner-only)
- Access tokens auto-refresh when within 5 minutes of expiry
- Refresh tokens last weeks/months
- If refresh fails, re-run the login command

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No auth file found" | Run `python -m cores.chatgpt_proxy.oauth_login` |
| "Token refresh failed" | Re-run login (refresh token may have expired) |
| Port 18741 in use | Set `PRISM_CHATGPT_PROXY_PORT=18742` |
| Proxy failed to start | Check logs; system falls back to API key automatically |
| Rate limiting | ChatGPT subscription has usage limits; wait and retry |

## Logout

```bash
python -c "import asyncio; from cores.chatgpt_proxy.oauth_login import logout; asyncio.run(logout())"
```
