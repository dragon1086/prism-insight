"""Persistent report-only OAuth proxy, separate from batch-owned port 18741."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    from aiohttp import web

    from cores.chatgpt_proxy.proxy_server import create_app
    from cores.chatgpt_proxy.token_manager import TokenManager

    manager = TokenManager()
    manager.validate_or_fail()
    # Reuse host-side OAuth storage/refresh. Never copy tokens to the app server.
    web.run_app(create_app(manager), host="127.0.0.1", port=18742, access_log=None)


if __name__ == "__main__":
    main()
