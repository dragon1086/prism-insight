"""OpenAI API 400/429 debug logging.

Import this module early in orchestrator entry points to enable
automatic request/response metadata logging when OpenAI returns 400/429 errors.

Usage:
    import cores.openai_debug  # noqa: F401 — side-effect import
"""

import logging
import httpx

logger = logging.getLogger("openai_debug")

_original_async_init = httpx.AsyncClient.__init__
_original_sync_init = httpx.Client.__init__


async def _async_log_on_error(response: httpx.Response):
    """Log request metadata when OpenAI API returns 400/429."""
    if (
        response.status_code in {400, 429}
        and "openai.com" in str(response.request.url)
    ):
        body = response.request.content
        request_id = response.headers.get("x-request-id", "unavailable")
        logger.error(
            "[OpenAI %s Debug] %s %s | x-request-id=%s | Body(%d bytes): %s",
            response.status_code,
            response.request.method,
            response.request.url,
            request_id,
            len(body),
            body[:3000].decode("utf-8", errors="replace"),
        )


def _patched_async_init(self, *args, **kwargs):
    _original_async_init(self, *args, **kwargs)
    hooks = self.event_hooks.get("response", [])
    if _async_log_on_error not in hooks:
        hooks.append(_async_log_on_error)
        self.event_hooks["response"] = hooks


def _sync_log_on_error(response: httpx.Response):
    """Log request metadata when OpenAI API returns 400/429 (sync)."""
    if (
        response.status_code in {400, 429}
        and "openai.com" in str(response.request.url)
    ):
        body = response.request.content
        request_id = response.headers.get("x-request-id", "unavailable")
        logger.error(
            "[OpenAI %s Debug] %s %s | x-request-id=%s | Body(%d bytes): %s",
            response.status_code,
            response.request.method,
            response.request.url,
            request_id,
            len(body),
            body[:3000].decode("utf-8", errors="replace"),
        )


def _patched_sync_init(self, *args, **kwargs):
    _original_sync_init(self, *args, **kwargs)
    hooks = self.event_hooks.get("response", [])
    if _sync_log_on_error not in hooks:
        hooks.append(_sync_log_on_error)
        self.event_hooks["response"] = hooks


# Apply monkey-patches on import
httpx.AsyncClient.__init__ = _patched_async_init
httpx.Client.__init__ = _patched_sync_init

logger.info("OpenAI 400/429 debug logging enabled")
