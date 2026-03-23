# ChatGPT OAuth Proxy - Architecture Design Document

> **Version**: 1.0 | **Date**: 2026-03-24

## Overview

PRISM-INSIGHT의 OpenAI API 호출을 ChatGPT Plus/Pro 구독을 통해 라우팅하는 인프로세스 프록시 아키텍처.

## Problem Statement

| | mcp-agent (기존) | ChatGPT OAuth |
|---|---|---|
| **엔드포인트** | `api.openai.com/v1/chat/completions` | `chatgpt.com/backend-api/codex/responses` |
| **API 포맷** | Chat Completions API | Responses API |
| **인증** | API Key (Bearer) | OAuth Access Token + Account-Id |
| **과금** | 토큰당 과금 | 구독 요금제 할당량 |

mcp-agent 프레임워크(pip 패키지)는 수정할 수 없고, 17개 이상의 에이전트 파일도 변경하면 안 됩니다.

## Solution: Adapter Pattern via In-Process Proxy

```
mcp-agent                    Proxy (localhost:18741)              ChatGPT
   |                              |                                  |
   | POST /v1/chat/completions   |                                  |
   |  {messages, max_tokens,     |                                  |
   |   tools}                    |                                  |
   |----------------------------> |                                  |
   |                              | 1. messages -> input             |
   |                              | 2. max_tokens -> max_output_tokens
   |                              | 3. tools flatten                 |
   |                              | 4. Bearer token inject           |
   |                              |                                  |
   |                              | POST /backend-api/codex/responses
   |                              |  {input, max_output_tokens,     |
   |                              |   tools, stream:true}           |
   |                              |--------------------------------->|
   |                              |                                  |
   |                              |  SSE stream (Responses API)      |
   |                              |<---------------------------------|
   |                              |                                  |
   |                              | 5. output[] -> choices[]         |
   |                              | 6. function_call -> tool_calls   |
   |                              | 7. usage mapping                 |
   |                              |                                  |
   |  Chat Completions response   |                                  |
   |<-----------------------------|                                  |
```

mcp-agent 입장에서는 **평소와 동일한 Chat Completions API**를 사용합니다. 프록시가 중간에서 포맷 변환 + 엔드포인트 변경 + 인증 주입을 처리합니다.

## Why This Architecture

### Design Principles

| 원칙 | 설명 |
|------|------|
| **높은 응집도** | 각 모듈이 하나의 책임만 담당 (번역, 토큰, 프록시, 설정) |
| **낮은 결합도** | 기존 코드와의 접점이 env var 2개뿐 |
| **독립 테스트** | api_translator는 순수 함수, 프록시는 curl로 독립 테스트 가능 |
| **확장 가능** | 다른 프로바이더 추가 시 라우트 추가만, 스트리밍도 함수 추가로 해결 |
| **유지보수** | ChatGPT API 변경 시 api_translator.py 한 파일만 수정 |

### Interface Design

```
[기존 코드] <--env var--> [프록시] <--HTTP--> [ChatGPT]
```

계층 간 인터페이스가 **문자열(env var)과 HTTP**라는 가장 범용적이고 안정적인 프로토콜입니다. 어느 한쪽이 변해도 다른 쪽에 영향이 없습니다.

### Why Not Other Approaches

#### Option B: Monkey-Patch / Subclass (Rejected)
- 17개 에이전트 파일 모두 수정 필요
- mcp-agent 내부에 강결합

#### Option C: httpx Transport Hook (Rejected)
두 가지 confirmed showstopper:
1. **`ensure_serializable()` at augmented_llm_openai.py:303**: `json.dumps -> json.loads -> reconstruct` 과정에서 `httpx.AsyncClient` 객체가 제거됨
2. **`AsyncOpenAI.__aexit__()` at _base_client.py:1468**: 공유 `http_client`가 매 요청마다 close됨

#### Option A-lite: aiohttp In-Process Proxy (Selected)
- `base_url` (문자열)은 `ensure_serializable`을 통과
- 새 의존성 0개 (aiohttp 기존 사용)
- monkey-patching 0개
- 포트 바인딩만 필요 (127.0.0.1, 로컬 전용)

## Module Architecture

```
cores/chatgpt_proxy/
├── __init__.py           # Public API: start_proxy(), stop_proxy(), inject_env()
├── config.py             # Constants (client_id, URLs, ports)
├── oauth_login.py        # PKCE OAuth login flow
├── token_manager.py      # Token lifecycle (load, refresh, store)
├── api_translator.py     # Chat Completions <-> Responses API (pure functions)
└── proxy_server.py       # aiohttp web app (2 routes)
```

### Module Responsibilities

| Module | 책임 | I/O | 상태 |
|--------|------|-----|------|
| `config.py` | 상수 정의 | None | Stateless |
| `oauth_login.py` | 1회성 브라우저 로그인 | Network + File | Login time only |
| `token_manager.py` | 토큰 캐시 + 자동갱신 | File + Network | Singleton (asyncio.Lock) |
| `api_translator.py` | API 포맷 번역 | **None** | **Stateless (pure functions)** |
| `proxy_server.py` | HTTP 라우팅 | Network | Stateless (per-request) |
| `__init__.py` | 생명주기 관리 | Process | Module-level globals |

### Key: api_translator.py is Pure

`api_translator.py`는 **I/O가 없는 순수 함수**로만 구성됩니다:
- `translate_request(body: dict) -> dict`
- `translate_response(response_body: dict, model: str) -> dict`
- `translate_error(error_body: dict, status_code: int) -> tuple[dict, int]`
- `collect_sse_to_response(sse_text: str) -> dict`

이 설계 덕분에 mock 없이 단위 테스트가 가능하고, ChatGPT API 변경 시 이 파일만 수정하면 됩니다.

## API Translation Reference

### Request Translation

| Chat Completions | Responses API | Notes |
|-----------------|---------------|-------|
| `messages` | `input` | 배열 구조 동일 |
| `messages[].role == "system"` | `input[].role == "developer"` | 역할명 변경 |
| `max_tokens` | `max_output_tokens` | 필드명 변경 |
| `tools[].function.name` | `tools[].name` | nested -> flat |
| `tools[].function.description` | `tools[].description` | nested -> flat |
| `tools[].function.parameters` | `tools[].parameters` | nested -> flat |
| `response_format` | `text.format` | 래핑 구조 변경 |
| (없음) | `store: false` | 필수 (true면 400) |
| (없음) | `stream: true` | 필수 (항상 스트림) |

### Response Translation

| Responses API | Chat Completions | Notes |
|---------------|-----------------|-------|
| `output[].type == "message"` | `choices[].message` | 텍스트 응답 |
| `output[].content[].text` | `choices[].message.content` | 텍스트 추출 |
| `output[].type == "function_call"` | `choices[].message.tool_calls[]` | 도구 호출 |
| `usage.input_tokens` | `usage.prompt_tokens` | 필드명 변경 |
| `usage.output_tokens` | `usage.completion_tokens` | 필드명 변경 |

### Tool Calling (Multi-turn)

```
Chat Completions:  {"role": "tool", "tool_call_id": "call_123", "content": "result"}
Responses API:     {"type": "function_call_output", "call_id": "call_123", "output": "result"}
```

## Multi-MCPApp Injection

`cores/analysis.py`가 분석 섹션마다 새 `MCPApp` 인스턴스를 생성합니다. 각 인스턴스는 `get_settings()`를 호출하여 `OpenAISettings`를 새로 만듭니다.

**해결**: 환경변수는 프로세스 전역이므로, `inject_env()`를 MCPApp 생성 전에 한 번만 호출하면 **모든** 인스턴스가 자동으로 프록시 URL을 사용합니다.

```python
# Orchestrator startup (MCPApp 생성 전)
os.environ["OPENAI_BASE_URL"] = "http://localhost:18741"  # 모든 MCPApp에 적용
os.environ["OPENAI_API_KEY"] = "chatgpt-oauth-placeholder"
```

`OpenAISettings`는 `BaseSettings`를 상속하며 `validation_alias=AliasChoices("base_url", "OPENAI_BASE_URL")`로 환경변수를 자동 로드합니다.

## Security

| 항목 | 구현 |
|------|------|
| 토큰 저장 | `~/.config/prism-insight/chatgpt_auth.json` (0o600) |
| 원자적 쓰기 | tmp 파일 + `os.rename` (중간 상태 없음) |
| CSRF 방지 | OAuth state 파라미터 검증 |
| 로컬 전용 | 프록시 `127.0.0.1` 바인딩 (외부 접근 차단) |
| 토큰 로깅 | DEBUG 레벨에서만, 마스킹 처리 |
| 자동 갱신 | 만료 5분 전 자동 refresh (asyncio.Lock으로 동시성 제어) |

## Activation Flow

```
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth
              |
              v
    [Orchestrator main()]
              |
    +-------- + ---------+
    |                     |
  api_key (default)  chatgpt_oauth
    |                     |
 (변경 없음)       1. inject_env() -> OPENAI_BASE_URL 설정
                   2. start_proxy() -> aiohttp 서버 시작
                   3. 실패 시 clear_env() -> 표준 API 폴백
                   4. MCPApp 생성 (프록시 URL 자동 적용)
                   5. 에이전트 실행 (변경 없음)
                   6. stop_proxy() -> 서버 종료
```

## Fallback Strategy

프록시 시작 실패 시:
1. `clear_env()` 호출 → `OPENAI_BASE_URL`, `OPENAI_API_KEY` 제거
2. WARNING 로그 출력
3. 기존 `mcp_agent.secrets.yaml`의 API Key로 자동 폴백
4. 에이전트 실행은 정상 진행

## Dependencies

| 의존성 | 용도 | 신규 여부 |
|--------|------|----------|
| `aiohttp` | 프록시 서버 + OAuth 콜백 | 기존 사용 |
| `hashlib`, `secrets` | PKCE 생성 | Python stdlib |
| `webbrowser` | OAuth 로그인 브라우저 | Python stdlib |

**새로운 pip 의존성: 0개**
