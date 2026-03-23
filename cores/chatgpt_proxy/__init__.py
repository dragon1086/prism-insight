"""ChatGPT OAuth Proxy for PRISM-INSIGHT.

Routes OpenAI API calls through ChatGPT Plus/Pro subscription
via an in-process aiohttp proxy server.
"""

import logging
import os

from aiohttp import web

from .constants import DEFAULT_PROXY_PORT
from .proxy_server import create_app
from .token_manager import TokenManager

logger = logging.getLogger(__name__)

_runner: web.AppRunner | None = None
_site: web.TCPSite | None = None


def inject_env(port: int | None = None) -> None:
    """Set OPENAI_BASE_URL and OPENAI_API_KEY env vars.

    MUST be called BEFORE any MCPApp creation so that
    OpenAISettings picks up the proxy URL.
    """
    proxy_port = port or DEFAULT_PROXY_PORT
    os.environ["OPENAI_BASE_URL"] = f"http://localhost:{proxy_port}"
    os.environ["OPENAI_API_KEY"] = "chatgpt-oauth-placeholder"
    logger.info("Environment variables set: OPENAI_BASE_URL=http://localhost:%d", proxy_port)


def clear_env() -> None:
    """Remove proxy env vars (for fallback to standard API)."""
    os.environ.pop("OPENAI_BASE_URL", None)
    os.environ.pop("OPENAI_API_KEY", None)
    logger.info("Proxy environment variables cleared")


async def start_proxy(port: int | None = None) -> bool:
    """Start the ChatGPT OAuth proxy server.

    Returns True if started successfully, False otherwise.
    """
    global _runner, _site

    if _runner is not None:
        logger.info("Proxy already running")
        return True

    proxy_port = port or DEFAULT_PROXY_PORT

    try:
        token_manager = TokenManager()
        token_manager.validate_or_fail()

        app = create_app(token_manager)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", proxy_port)
        await site.start()
        _runner = runner
        _site = site

        logger.info("ChatGPT OAuth proxy started on port %d", proxy_port)
        return True

    except Exception as e:
        logger.error("Failed to start proxy: %s", e)
        _runner = None
        _site = None
        return False


async def stop_proxy() -> None:
    """Gracefully stop the proxy server."""
    global _runner, _site

    if _runner is not None:
        await _runner.cleanup()
        logger.info("ChatGPT OAuth proxy stopped")

    _runner = None
    _site = None
