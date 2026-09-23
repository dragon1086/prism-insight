"""Persistent report-only OAuth proxy, separate from batch-owned port 18741."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def create_report_token_manager():
    import math
    from contextvars import ContextVar

    from cores.chatgpt_proxy.token_manager import ChatGPTAuthExpiredError, TokenManager

    class ReadOnlyReportTokenManager(TokenManager):
        def __init__(self):
            super().__init__()
            self._request_account = ContextVar('report_oauth_account', default=None)

        async def get_token(self):
            async with self._lock:
                # The existing host refresh owner may atomically rotate the file.
                # Never reuse a stale cached refresh token in this persistent reader.
                try:
                    data = self._load_from_disk()
                    expiry = data.get('expires_at')
                    if (not isinstance(data.get('access_token'), str) or not data['access_token']
                            or not isinstance(data.get('account_id', ''), str)
                            or isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                            or not math.isfinite(expiry) or self._is_expired(data)):
                        raise ValueError('invalid host snapshot')
                except (OSError, ValueError, TypeError, AttributeError, ChatGPTAuthExpiredError):
                    self._request_account.set(None)
                    raise ChatGPTAuthExpiredError('Host OAuth authentication unavailable; report proxy is read-only') from None
                self._request_account.set(data.get('account_id', ''))
                return data['access_token']

        async def get_account_id(self):
            account = self._request_account.get()
            if account is None:
                raise ChatGPTAuthExpiredError('No valid request authentication snapshot')
            return account

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
