"""OpenAI API 400 error debug logging.

Import this module early in orchestrator entry points to enable
automatic request body logging when OpenAI returns 400 errors.

Usage:
    import cores.openai_debug  # noqa: F401 — side-effect import
"""

import logging
import httpx

logger = logging.getLogger("openai_debug")

_original_async_init = httpx.AsyncClient.__init__
_original_sync_init = httpx.Client.__init__


async def _async_log_on_error(response: httpx.Response):
    """Log request body when OpenAI API returns 400."""
    if (
        response.status_code == 400
        and "openai.com" in str(response.request.url)
    ):
        body = response.request.content
        logger.error(
            "[OpenAI 400 Debug] %s %s | "
            "Body(%d bytes): %s",
            response.request.method,
            response.request.url,
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
    """Log request body when OpenAI API returns 400 (sync)."""
    if (
        response.status_code == 400
        and "openai.com" in str(response.request.url)
    ):
        body = response.request.content
        logger.error(
            "[OpenAI 400 Debug] %s %s | "
            "Body(%d bytes): %s",
            response.request.method,
            response.request.url,
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

logger.info("OpenAI 400 debug logging enabled")
