"""ChatGPT OAuth Proxy Server.

Lightweight aiohttp web server that translates Chat Completions API
requests to ChatGPT Responses API format and back.
"""

import json
import logging

import aiohttp
from aiohttp import web

from . import api_translator
from .constants import CHATGPT_RESPONSES_URL
from .token_manager import TokenManager

logger = logging.getLogger(__name__)

_token_manager: TokenManager | None = None


def create_app(token_manager: TokenManager) -> web.Application:
    """Create the aiohttp proxy application."""
    global _token_manager
    _token_manager = token_manager

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_post("/v1/responses", handle_responses)
    app.router.add_get("/health", handle_health)
    return app


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    token_valid = False
    if _token_manager:
        try:
            await _token_manager.get_token()
            token_valid = True
        except Exception:
            pass

    return web.json_response({"status": "ok", "token_valid": token_valid})


async def _forward_to_codex(
    translated_request: dict,
) -> "tuple[dict | None, web.Response | None]":
    """Forward a pre-translated Responses API request to the Codex upstream.

    Handles token retrieval, HTTP POST, SSE/JSON parsing, and error translation.

    Returns:
        (api_response, None)  on success — api_response is a Responses API dict.
        (None, error_response) on any failure — error_response is a web.Response.
    """
    if not _token_manager:
        return None, web.json_response(
            {"error": {"message": "Token manager not initialized", "type": "server_error"}},
            status=500,
        )

    # Get OAuth token
    try:
        token = await _token_manager.get_token()
        account_id = await _token_manager.get_account_id()
    except Exception as e:
        logger.error("Token retrieval failed: %s", e)
        return None, web.json_response(
            {"error": {"message": str(e), "type": "authentication_error"}},
            status=401,
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
        "accept": "text/event-stream",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                CHATGPT_RESPONSES_URL,
                json=translated_request,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                raw_body = await resp.text()

                if resp.status != 200:
                    try:
                        error_body = json.loads(raw_body)
                    except json.JSONDecodeError:
                        error_body = {"error": {"message": raw_body}}

                    translated_error, status = api_translator.translate_error(error_body, resp.status)
                    logger.warning("ChatGPT API error (%d): %s", resp.status, raw_body[:200])
                    return None, web.json_response(translated_error, status=status)

                # Parse response (may be SSE or JSON)
                # Always request stream=true, so check both Content-Type and body format
                content_type = resp.headers.get("Content-Type", "")
                is_sse = "text/event-stream" in content_type or raw_body.lstrip().startswith("event:")
                if is_sse:
                    try:
                        api_response = api_translator.collect_sse_to_response(raw_body)
                        logger.debug("SSE parsed: output_items=%s status=%s",
                                     [i.get("type") for i in api_response.get("output", [])],
                                     api_response.get("status"))
                    except ValueError as e:
                        logger.error("SSE parsing failed: %s (Content-Type: %s, body[:200]: %s)", e, content_type, raw_body[:200])
                        return None, web.json_response(
                            {"error": {"message": f"SSE parsing error: {e}", "type": "server_error"}},
                            status=502,
                        )
                else:
                    try:
                        api_response = json.loads(raw_body)
                    except json.JSONDecodeError:
                        logger.error("Invalid JSON response from ChatGPT (Content-Type: %s, body[:200]: %s)", content_type, raw_body[:200])
                        return None, web.json_response(
                            {"error": {"message": "Invalid response from upstream", "type": "server_error"}},
                            status=502,
                        )

    except aiohttp.ClientError as e:
        logger.error("Connection to ChatGPT failed: %s", e)
        return None, web.json_response(
            {"error": {"message": f"Upstream connection error: {e}", "type": "server_error"}},
            status=502,
        )

    return api_response, None


async def handle_chat_completions(request: web.Request) -> web.Response:
    """Translate and proxy a Chat Completions request to ChatGPT Responses API."""
    if not _token_manager:
        return web.json_response(
            {"error": {"message": "Token manager not initialized", "type": "server_error"}},
            status=500,
        )

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
            status=400,
        )

    original_model = body.get("model", "gpt-4o")

    # Translate request
    try:
        translated_request = api_translator.translate_request(body)
    except Exception as e:
        msg_types = [(m.get("role"), type(m.get("content")).__name__, type(m.get("tool_calls")).__name__) for m in (body.get("messages") or [])]
        logger.error("Request translation failed: %s | messages: %s", e, msg_types)
        return web.json_response(
            {"error": {"message": f"Request translation error: {e}", "type": "server_error"}},
            status=500,
        )

    logger.debug("Proxy request: model=%s -> %s, tools=%d, messages=%d",
                 original_model, translated_request.get("model"),
                 len(body.get("tools") or []), len(body.get("messages") or []))

    api_response, err = await _forward_to_codex(translated_request)
    if err is not None:
        return err

    # Translate response back to Chat Completions format
    try:
        result = api_translator.translate_response(api_response, original_model)
    except Exception as e:
        logger.error("Response translation failed: %s", e)
        return web.json_response(
            {"error": {"message": f"Response translation error: {e}", "type": "server_error"}},
            status=500,
        )

    return web.json_response(result)


async def handle_responses(request: web.Request) -> web.Response:
    """Proxy a Responses API request directly to the ChatGPT Codex backend.

    The openai-agents Runner already emits a Responses-shaped body, so no
    format translation is needed.  The response is returned to the caller
    as-is (no back-translation to Chat Completions format).
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
            status=400,
        )

    translated = api_translator.prepare_responses_passthrough(body)

    logger.debug("Responses passthrough: model=%s -> %s, tools=%d",
                 body.get("model"), translated.get("model"),
                 len(body.get("tools") or []))

    api_response, err = await _forward_to_codex(translated)
    if err is not None:
        return err

    return web.json_response(api_response)
