"""Persistent report-only OAuth proxy, separate from batch-owned port 18741."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def create_report_token_manager():
    from cores.chatgpt_proxy.token_manager import ChatGPTAuthExpiredError, TokenManager

    class ReadOnlyReportTokenManager(TokenManager):
        async def get_token(self):
            async with self._lock:
                # The existing host refresh owner may atomically rotate the file.
                # Never reuse a stale cached refresh token in this persistent reader.
                self._auth_data = self._load_from_disk()
                if self._is_expired(self._auth_data):
                    raise ChatGPTAuthExpiredError("Host OAuth token requires refresh; report proxy is read-only")
                return self._auth_data['access_token']

    return ReadOnlyReportTokenManager()


def main():
    from aiohttp import web

    from cores.chatgpt_proxy.proxy_server import create_app

    manager = create_report_token_manager()
    manager.validate_or_fail()
    # Host owns refresh; this service only reads it. No alternate auth fallback.
    web.run_app(create_app(manager), host="127.0.0.1", port=18742, access_log=None)


if __name__ == "__main__":
    main()
