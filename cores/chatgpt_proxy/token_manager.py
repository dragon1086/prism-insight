"""ChatGPT OAuth Token Manager.

Handles token loading, caching, and automatic refresh.
Thread/async-safe with asyncio.Lock.
"""

import asyncio
import json
import logging
import os
import time

import aiohttp

from .constants import (
    OAUTH_CLIENT_ID,
    OAUTH_TOKEN_URL,
    AUTH_FILE,
    AUTH_DIR,
    TOKEN_REFRESH_BUFFER,
)

logger = logging.getLogger(__name__)


class ChatGPTAuthExpiredError(Exception):
    """Raised when refresh token is invalid and re-login is required."""
    pass


class TokenManager:
    """Manages ChatGPT OAuth token lifecycle."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._auth_data: dict | None = None

    def _load_from_disk(self) -> dict:
        """Load auth data from disk."""
        if not AUTH_FILE.exists():
            raise ChatGPTAuthExpiredError(
                f"No auth file found at {AUTH_FILE}. "
                "Run 'python -m cores.chatgpt_proxy.oauth_login' to authenticate."
            )
        with open(AUTH_FILE) as f:
            return json.load(f)

    def _save_to_disk(self, auth_data: dict) -> None:
        """Save auth data atomically with restricted permissions."""
        AUTH_DIR.mkdir(parents=True, exist_ok=True)
        tmp_file = AUTH_FILE.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump(auth_data, f, indent=2)
        os.chmod(tmp_file, 0o600)
        os.rename(tmp_file, AUTH_FILE)

    def validate_or_fail(self) -> None:
        """Check that auth file exists and has a refresh token. Call at startup."""
        data = self._load_from_disk()
        if not data.get("refresh_token"):
            raise ChatGPTAuthExpiredError(
                "Auth file exists but has no refresh token. "
                "Run 'python -m cores.chatgpt_proxy.oauth_login' to re-authenticate."
            )
        self._auth_data = data
        logger.info("ChatGPT OAuth: token loaded (expires %s)", time.ctime(data.get("expires_at", 0)))

    def _is_expired(self, auth_data: dict) -> bool:
        """Check if access token is expired or within refresh buffer."""
        expires_at = auth_data.get("expires_at", 0)
        return time.time() >= (expires_at - TOKEN_REFRESH_BUFFER)

    async def _refresh_token(self, auth_data: dict) -> dict:
        """Refresh the access token using the refresh token."""
        refresh_token = auth_data.get("refresh_token")
        if not refresh_token:
            raise ChatGPTAuthExpiredError("No refresh token available. Re-login required.")

        logger.debug("Refreshing ChatGPT OAuth token...")

        token_data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                OAUTH_TOKEN_URL,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("Token refresh failed (%d): %s", resp.status, body)
                    raise ChatGPTAuthExpiredError(
                        f"Token refresh failed ({resp.status}). Re-login required. "
                        "Run 'python -m cores.chatgpt_proxy.oauth_login' to re-authenticate."
                    )
                tokens = await resp.json()

        expires_in = tokens.get("expires_in", 3600)
        auth_data["access_token"] = tokens["access_token"]
        auth_data["expires_at"] = int(time.time()) + expires_in
        if tokens.get("refresh_token"):
            auth_data["refresh_token"] = tokens["refresh_token"]
        auth_data["issued_at"] = int(time.time())

        self._save_to_disk(auth_data)
        logger.info("ChatGPT OAuth token refreshed (expires %s)", time.ctime(auth_data["expires_at"]))
        return auth_data

    async def get_token(self) -> str:
        """Get a valid access token, refreshing if necessary.

        Returns the access_token string.
        """
        async with self._lock:
            if self._auth_data is None:
                self._auth_data = self._load_from_disk()

            if self._is_expired(self._auth_data):
                self._auth_data = await self._refresh_token(self._auth_data)

            return self._auth_data["access_token"]

    async def get_account_id(self) -> str:
        """Get the account ID."""
        async with self._lock:
            if self._auth_data is None:
                self._auth_data = self._load_from_disk()
            return self._auth_data.get("account_id", "")
