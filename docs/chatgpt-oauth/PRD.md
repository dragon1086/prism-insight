# ChatGPT OAuth Proxy for PRISM-INSIGHT

> **Version**: 3.0 (FINAL) | **Date**: 2026-03-23
> **Status**: CONFIRMED — Ready for implementation
> **Complexity**: MEDIUM

---

## Context

PRISM-INSIGHT uses mcp-agent's `AugmentedLLM` (OpenAI provider) to orchestrate 13+ AI agents. Each agent call goes through `AsyncOpenAI` with standard Chat Completions API. The goal is to route these calls through ChatGPT Plus OAuth instead of paying per-token API fees.

### Why Previous Approaches Failed

**Option C (httpx Transport Hook) — DEAD:**

1. **`ensure_serializable()` strips `http_client`** — At `augmented_llm_openai.py:303`, the request config is round-tripped through `json.dumps/json.loads`. `httpx.AsyncClient` is not JSON-serializable and is silently lost. The `hasattr(request.config, "http_client")` check at line 971 returns `False` after reconstruction.

2. **`AsyncOpenAI.__aexit__()` closes shared `http_client`** — At `_base_client.py:1468-1473`, `close()` calls `await self._client.aclose()`. Since mcp-agent uses `async with AsyncOpenAI(http_client=...)` at line 968, the shared client gets closed after every request.

Both require complex monkey-patching to work around — fragile and unmaintainable.

### Key Insight: Strings Survive, Objects Don't

`base_url` (str) and `api_key` (str) both survive `ensure_serializable` perfectly. `OpenAISettings` in mcp-agent's `config.py:427-431` reads from environment variables via `validation_alias=AliasChoices("base_url", "OPENAI_BASE_URL", "openai__base_url")`. This means environment variable injection is the correct, zero-patch approach.

---

## Architecture: aiohttp In-Process Proxy

```
[mcp-agent / OpenAI SDK]
    |
    |  POST http://localhost:18741/v1/chat/completions
    |  (standard OpenAI SDK behavior via OPENAI_BASE_URL override)
    |
    v
[aiohttp.web.Application — same asyncio event loop]
    |
    | 1. Parse Chat Completions request body
    | 2. Translate to Responses API format (api_translator.py)
    | 3. Get fresh OAuth token from TokenManager
    | 4. Inject Authorization header
    | 5. Forward to https://chatgpt.com/backend-api/codex/responses
    | 6. Collect SSE stream, translate response back
    | 7. Return Chat Completions response
    |
    v
[OpenAI SDK processes normal-looking response]
```

### Why aiohttp (Not Starlette)

- `aiohttp` is ALREADY a project dependency — zero new packages
- Runs as `asyncio.create_task()` in the same event loop — no subprocess, no thread
- Minimal surface: 2 routes (`POST /v1/chat/completions`, `GET /health`)
- No starlette, no uvicorn, no ASGI complexity

### Multi-MCPApp Injection (Zero Monkey-Patching)

```python
# Before ANY MCPApp creation:
os.environ["OPENAI_BASE_URL"] = "http://localhost:18741"
os.environ["OPENAI_API_KEY"] = "chatgpt-oauth-placeholder"

# OpenAISettings picks these up automatically via AliasChoices.
# ALL MCPApp instances (main orchestrator + per-section) inherit them.
# Strings → no serialization issues with ensure_serializable().
```

---

## Work Objectives

1. Route all mcp-agent OpenAI calls through ChatGPT Plus OAuth with zero code changes to the 17 agent files
2. Zero new dependencies (aiohttp already present)
3. Zero monkey-patching of mcp-agent internals
4. Graceful fallback to standard API if proxy unavailable
5. Support both KR and US orchestrators

---

## Guardrails

### Must Have
- All 13+ agents work without any modification to their files
- OAuth token auto-refresh before expiry
- Proxy starts/stops cleanly with the orchestrator lifecycle
- `--no-proxy` flag to bypass and use standard API
- Error responses translated to OpenAI-compatible error format
- Health check endpoint for monitoring

### Must NOT Have
- No monkey-patching of `augmented_llm_openai.py` or any mcp-agent file
- No new pip dependencies (aiohttp already installed)
- No subprocess or separate process for the proxy
- No modification to any agent file in `cores/agents/`
- No starlette, no uvicorn, no ASGI
- No persistent state in the proxy (stateless request translation)

---

## File Structure

```
prism-insight/
├── cores/
│   ├── chatgpt_proxy/
│   │   ├── __init__.py              # Public API: start_proxy(), stop_proxy()
│   │   ├── proxy_server.py          # aiohttp.web app (2 routes)
│   │   ├── api_translator.py        # Chat Completions <-> Responses API
│   │   ├── token_manager.py         # OAuth token lifecycle
│   │   └── oauth_login.py           # One-time browser login flow
│   └── agents/                      # UNTOUCHED — zero changes
├── stock_analysis_orchestrator.py    # Add proxy startup + env vars
└── prism-us/
    └── us_stock_analysis_orchestrator.py  # Add proxy startup + env vars
```

**Total new files**: 5 (in `cores/chatgpt_proxy/`)
**Modified files**: 2 (orchestrators only — add ~15 lines each)

---

## API Translation Reference

### Request: Chat Completions -> Responses API

```python
# INPUT (what OpenAI SDK sends):
{
    "model": "gpt-4o",
    "messages": [
        {"role": "system", "content": "You are a stock analyst."},
        {"role": "user", "content": "Analyze Samsung Electronics"}
    ],
    "temperature": 0.7,
    "max_tokens": 4096
}

# OUTPUT (what we send to ChatGPT Responses API):
{
    "model": "gpt-4o",
    "input": [
        {"role": "system", "content": "You are a stock analyst."},
        {"role": "user", "content": "Analyze Samsung Electronics"}
    ],
    "temperature": 0.7,
    "max_output_tokens": 4096,
    "stream": false
}
```

Key field mappings:
| Chat Completions | Responses API |
|-----------------|---------------|
| `messages` | `input` |
| `max_tokens` | `max_output_tokens` |
| `stop` | `stop` (same) |
| `temperature` | `temperature` (same) |
| `n` | Not supported (ignore, default 1) |
| `tools` | `tools` (passthrough — format is compatible) |
| `tool_choice` | `tool_choice` (passthrough) |

### Tool Calling Translation (CRITICAL — agents use MCP tools extensively)

15+ agents bind to MCP servers (perplexity, kospi_kosdaq, sqlite, firecrawl, yahoo_finance, etc.). mcp-agent sends `tools` in requests and expects `tool_calls` in responses. The proxy MUST faithfully translate these.

**Request — tool definitions passthrough:**
```python
# Chat Completions tools (sent by mcp-agent):
"tools": [
    {
        "type": "function",
        "function": {
            "name": "get_stock_info",
            "description": "Get stock information",
            "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}}
        }
    }
]

# Responses API tools (flattened):
"tools": [
    {
        "type": "function",
        "name": "get_stock_info",
        "description": "Get stock information",
        "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}}
    }
]
```

**Response — tool calls translation:**
```python
# Responses API output with function_call:
{
    "output": [
        {
            "type": "function_call",
            "name": "get_stock_info",
            "call_id": "call_abc123",
            "arguments": "{\"ticker\": \"005930\"}"
        }
    ]
}

# Translated to Chat Completions:
{
    "choices": [{
        "index": 0,
        "message": {
            "role": "assistant",
            "content": null,
            "tool_calls": [{
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "get_stock_info",
                    "arguments": "{\"ticker\": \"005930\"}"
                }
            }]
        },
        "finish_reason": "tool_calls"
    }]
}
```

**Multi-turn tool result passthrough:**
```python
# Chat Completions tool result message:
{"role": "tool", "tool_call_id": "call_abc123", "content": "Samsung Electronics: 72,500 KRW"}

# Responses API tool result in input:
{"type": "function_call_output", "call_id": "call_abc123", "output": "Samsung Electronics: 72,500 KRW"}
```

### Response: Responses API -> Chat Completions

```python
# INPUT (Responses API returns):
{
    "id": "resp_abc123",
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "Samsung Electronics shows..."}
            ]
        }
    ],
    "usage": {
        "input_tokens": 150,
        "output_tokens": 800,
        "total_tokens": 950
    }
}

# OUTPUT (what we return to OpenAI SDK):
{
    "id": "chatcmpl-resp_abc123",
    "object": "chat.completion",
    "created": 1711180800,
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Samsung Electronics shows..."
            },
            "finish_reason": "stop"
        }
    ],
    "usage": {
        "prompt_tokens": 150,
        "completion_tokens": 800,
        "total_tokens": 950
    }
}
```

### Error Translation

```python
# Responses API error:
{"error": {"message": "Rate limited", "type": "rate_limit", "code": 429}}

# Translated to Chat Completions error format:
{"error": {"message": "Rate limited", "type": "rate_limit_error", "code": "rate_limit_exceeded"}}
```

### Streaming Translation

For streaming responses, the proxy collects the full SSE stream from Responses API, then returns a single non-streaming response. This simplifies implementation significantly since mcp-agent's `AugmentedLLM` processes the complete response anyway (it doesn't stream to users during agent execution).

If streaming is later needed:
```
Responses API SSE: response.output_text.delta → {"delta": {"content": chunk}}
Responses API SSE: response.completed → {"finish_reason": "stop"}
```

---

## Task Flow

### Step 1: OAuth Login + Token Manager (`token_manager.py`, `oauth_login.py`)

**What**: Browser-based ChatGPT login that extracts OAuth tokens, plus a manager that caches and auto-refreshes them.

**Implementation details**:
- `oauth_login.py`: Official PKCE OAuth flow (same as Codex CLI):
  1. Generate PKCE verifier + S256 challenge (hashlib + secrets, stdlib)
  2. Open browser to `https://auth.openai.com/oauth/authorize?client_id=app_EMoamEEZ73f0CkXaXp7hrann&redirect_uri=http://localhost:1455/auth/callback&scope=openid+profile+email+offline_access&response_type=code&code_challenge=<S256>&code_challenge_method=S256&state=<random>`
  3. Local aiohttp server on port 1455 captures callback `?code=<code>&state=<state>`
  4. Exchange code for tokens: `POST https://auth.openai.com/oauth/token` with `grant_type=authorization_code&code=<code>&redirect_uri=http://localhost:1455/auth/callback&client_id=app_EMoamEEZ73f0CkXaXp7hrann&code_verifier=<verifier>`
  5. Parse response: `{access_token, refresh_token, id_token, expires_in}`
  6. Extract account_id from id_token JWT claims (base64 decode, no crypto verification needed)
- `token_manager.py`: Stores tokens in `~/.config/prism-insight/chatgpt_auth.json` (0o600 permissions)
  - Auto-refresh via `POST https://auth.openai.com/oauth/token` with `grant_type=refresh_token&refresh_token=<token>&client_id=app_EMoamEEZ73f0CkXaXp7hrann`
  - Refresh token lasts weeks/months; access token expires in hours
  - 5-minute buffer before expiry triggers refresh
  - asyncio.Lock prevents concurrent refresh races
- Token format: `{"access_token": "eyJ...", "refresh_token": "v1.MjQ...", "expires_at": 1711900000, "account_id": "org-abc123"}`

**Acceptance criteria**:
- [ ] `python -m cores.chatgpt_proxy.oauth_login` opens browser, user logs in, token saved to disk
- [ ] `TokenManager.get_token()` returns valid token without browser if cached token not expired
- [ ] `TokenManager.get_token()` triggers re-login if token expired
- [ ] Token file permissions are 600 (owner-only read/write)

### Step 2: API Translator (`api_translator.py`)

**What**: Pure-function module that converts between Chat Completions and Responses API formats. No I/O, no state, fully unit-testable.

**Implementation details**:
- `translate_request(chat_completion_body: dict) -> dict` — maps fields as documented above
- `translate_response(responses_body: dict, model: str) -> dict` — constructs Chat Completions response
- `translate_error(error_body: dict, status_code: int) -> tuple[dict, int]` — maps error format
- `collect_sse_to_response(sse_text: str) -> dict` — parses SSE stream into single Responses API object (handles `response.output_text.delta` events, concatenates text, extracts final usage)
- Handles edge cases: missing `usage` field, empty `output`, multiple output items

**Acceptance criteria**:
- [ ] Unit tests cover: standard request, streaming collection, error translation, missing fields
- [ ] `translate_request` correctly maps `max_tokens` -> `max_output_tokens`
- [ ] `translate_response` produces valid Chat Completions response that OpenAI SDK can parse
- [ ] `collect_sse_to_response` correctly concatenates streamed text chunks
- [ ] Round-trip test: translate_request -> mock API -> translate_response produces expected output

### Step 3: Proxy Server (`proxy_server.py`, `__init__.py`)

**What**: aiohttp web application with 2 routes, managed via `start_proxy()` / `stop_proxy()` lifecycle functions.

**Implementation details**:
```python
# __init__.py — public API
async def start_proxy(port: int = 18741) -> bool:
    """Start proxy as asyncio task. Returns True if started, False if already running."""

async def stop_proxy() -> None:
    """Gracefully stop proxy server."""

def inject_env(port: int = 18741) -> None:
    """Set OPENAI_BASE_URL and OPENAI_API_KEY env vars. Call BEFORE MCPApp creation."""
```

```python
# proxy_server.py — aiohttp app
app = web.Application()
app.router.add_post("/v1/chat/completions", handle_chat_completions)
app.router.add_get("/health", handle_health)

async def handle_chat_completions(request: web.Request) -> web.Response:
    body = await request.json()
    translated = api_translator.translate_request(body)
    token = await token_manager.get_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://chatgpt.com/backend-api/codex/responses",
            json=translated, headers=headers
        ) as resp:
            if resp.content_type == "text/event-stream":
                sse_text = await resp.text()
                api_response = api_translator.collect_sse_to_response(sse_text)
            else:
                api_response = await resp.json()

            if resp.status != 200:
                error, status = api_translator.translate_error(api_response, resp.status)
                return web.json_response(error, status=status)

            result = api_translator.translate_response(api_response, body.get("model", "gpt-4o"))
            return web.json_response(result)
```

**Acceptance criteria**:
- [ ] `start_proxy()` starts aiohttp server on port 18741 as asyncio task (no new thread/process)
- [ ] `stop_proxy()` cleanly shuts down the server
- [ ] `GET /health` returns `{"status": "ok", "token_valid": true/false}`
- [ ] `POST /v1/chat/completions` with valid Chat Completions body returns valid Chat Completions response
- [ ] Concurrent requests work (aiohttp handles this natively)
- [ ] Connection errors to ChatGPT return proper OpenAI-format error response
- [ ] Port conflict detected and reported clearly at startup

### Step 4: Orchestrator Integration (2 files, ~15 lines each)

**What**: Add proxy startup and environment variable injection to both orchestrators.

**Implementation details**:
```python
# In stock_analysis_orchestrator.py (KR) and us_stock_analysis_orchestrator.py (US):

import argparse
# Add --no-proxy flag
parser.add_argument("--no-proxy", action="store_true", help="Use standard OpenAI API")

async def main():
    args = parse_args()

    if not args.no_proxy:
        from cores.chatgpt_proxy import inject_env, start_proxy
        inject_env()  # Sets OPENAI_BASE_URL before any MCPApp creation
        proxy_started = await start_proxy()
        if not proxy_started:
            logger.warning("Proxy failed to start, falling back to standard API")
            # Unset env vars to fall back
            os.environ.pop("OPENAI_BASE_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)

    # ... existing MCPApp creation and agent execution (UNCHANGED) ...

    if not args.no_proxy:
        from cores.chatgpt_proxy import stop_proxy
        await stop_proxy()
```

**Acceptance criteria**:
- [ ] `python stock_analysis_orchestrator.py --mode morning` uses proxy by default
- [ ] `python stock_analysis_orchestrator.py --mode morning --no-proxy` uses standard API
- [ ] Proxy failure triggers automatic fallback with warning log
- [ ] Both KR and US orchestrators have identical proxy integration
- [ ] No changes to any file in `cores/agents/` — verified by `git diff` showing zero agent modifications
- [ ] Environment variables are set BEFORE first MCPApp instantiation

### Step 5: Testing + Validation

**What**: Verify the full flow works end-to-end without modifying any agent.

**Test plan**:
1. **Unit tests** for `api_translator.py` (pure functions, no network)
2. **Integration test**: Start proxy, send a raw Chat Completions request via `curl`, verify response format
3. **Agent smoke test**: Run `python demo.py 005930 --no-telegram` with proxy enabled, verify all agents produce reports
4. **Fallback test**: Start orchestrator with proxy port blocked, verify fallback to standard API
5. **Token refresh test**: Set token expiry to past, verify re-login triggered

**Acceptance criteria**:
- [ ] `api_translator.py` has >90% test coverage
- [ ] `curl -X POST http://localhost:18741/v1/chat/completions -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}]}'` returns valid response
- [ ] `demo.py` produces complete stock report through proxy
- [ ] Fallback works when proxy is unavailable
- [ ] No modification to any file outside of: `cores/chatgpt_proxy/`, `stock_analysis_orchestrator.py`, `us_stock_analysis_orchestrator.py`

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| ChatGPT Responses API format changes | LOW | HIGH | Version-pin API behavior in translator, add response validation |
| OAuth token invalidated early | MEDIUM | LOW | TokenManager detects 401, triggers re-login automatically |
| Port 18741 conflict | LOW | LOW | Configurable port, clear error message |
| Rate limiting by ChatGPT | MEDIUM | MEDIUM | Respect existing sequential agent execution pattern (no parallel calls) |
| aiohttp version incompatibility | LOW | LOW | Already works in project — no version change needed |
| ChatGPT blocks automated access | MEDIUM | HIGH | `--no-proxy` flag provides instant fallback to standard API |

---

## What This Plan Does NOT Cover

- Streaming passthrough (collected response is sufficient for agent use)
- Multi-user / multi-account support (single ChatGPT Plus account)
- Caching of responses (agents need fresh analysis each run)
- WebSocket transport (HTTP POST is sufficient)
- Streaming passthrough to end-users (proxy collects full response; agents process complete responses anyway)

---

## Success Criteria (Final)

1. All 13+ agents produce identical-quality reports through the proxy
2. Zero files modified in `cores/agents/` directory
3. Zero new pip dependencies added
4. Zero monkey-patching of mcp-agent code
5. `--no-proxy` flag works as instant fallback
6. Token refresh is automatic and transparent
7. Proxy adds <100ms latency per request (same-process, no network hop to separate service)
