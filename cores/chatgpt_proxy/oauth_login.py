"""ChatGPT OAuth PKCE Login Flow.

One-time interactive login that opens a browser for ChatGPT authentication.
Uses the official Codex CLI OAuth client_id and PKCE flow.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from .constants import (
    OAUTH_CLIENT_ID,
    OAUTH_AUTHORIZE_URL,
    OAUTH_TOKEN_URL,
    OAUTH_SCOPE,
    OAUTH_CALLBACK_PORT,
    OAUTH_REDIRECT_URI,
    AUTH_DIR,
    AUTH_FILE,
)

logger = logging.getLogger(__name__)


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(32)[:43]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _parse_jwt_claims(id_token: str) -> dict:
    """Extract claims from JWT id_token without verification (base64 decode only)."""
    parts = id_token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    # Add padding
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _extract_account_id(claims: dict) -> str | None:
    """Extract account_id from JWT claims.

    The claim path varies across token types:
    - id_token: organizations[0].id or sub
    - access_token: "https://api.openai.com/auth".chatgpt_account_id
    """
    # Try the OpenAI auth namespace (access_token pattern)
    auth_ns = claims.get("https://api.openai.com/auth", {})
    if isinstance(auth_ns, dict):
        acct = auth_ns.get("chatgpt_account_id")
        if acct:
            return acct
    # Try direct chatgpt_account_id
    if claims.get("chatgpt_account_id"):
        return claims["chatgpt_account_id"]
    # Try organizations field (id_token pattern)
    orgs = claims.get("organizations", [])
    if orgs and isinstance(orgs, list):
        first_org = orgs[0] if isinstance(orgs[0], str) else orgs[0].get("id", "")
        if first_org:
            return first_org
    # Fallback to sub
    return claims.get("sub")


async def login(force: bool = False) -> dict:
    """Run the OAuth PKCE login flow.

    Returns dict with access_token, refresh_token, expires_at, account_id.
    """
    # Check existing auth
    if not force and AUTH_FILE.exists():
        try:
            with open(AUTH_FILE) as f:
                existing = json.load(f)
            if existing.get("refresh_token"):
                logger.info("Already authenticated. Use force=True to re-authenticate.")
                return existing
        except (json.JSONDecodeError, KeyError):
            pass

    # Generate PKCE
    verifier, challenge = _generate_pkce()
    state = secrets.token_hex(16)

    # Build authorization URL
    params = {
        "response_type": "code",
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": OAUTH_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "prism_insight",
    }
    auth_url = f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    # Set up callback server
    code_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    async def handle_callback(request: web.Request) -> web.Response:
        qs = request.query
        received_state = qs.get("state", "")
        received_code = qs.get("code", "")

        if received_state != state:
            return web.Response(
                text="<h1>Error: State mismatch</h1>",
                content_type="text/html",
                status=400,
            )

        if not received_code:
            error = qs.get("error", "unknown")
            error_desc = qs.get("error_description", "")
            if not code_future.done():
                code_future.set_exception(
                    RuntimeError(f"OAuth error: {error} - {error_desc}")
                )
            return web.Response(
                text=f"<h1>Error: {error}</h1><p>{error_desc}</p>",
                content_type="text/html",
                status=400,
            )

        if not code_future.done():
            code_future.set_result(received_code)

        return web.Response(
            text="<h1>Login successful!</h1><p>You can close this window.</p>",
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/auth/callback", handle_callback)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", OAUTH_CALLBACK_PORT)

    try:
        await site.start()
        logger.info(f"Callback server started on port {OAUTH_CALLBACK_PORT}")

        # Open browser
        print(f"\nOpening browser for ChatGPT login...")
        print(f"If browser doesn't open, visit:\n{auth_url}\n")
        webbrowser.open(auth_url)

        # Wait for callback (timeout 5 minutes)
        code = await asyncio.wait_for(code_future, timeout=300)
        logger.info("Authorization code received")

    finally:
        await runner.cleanup()

    # Exchange code for tokens
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "client_id": OAUTH_CLIENT_ID,
        "code_verifier": verifier,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            OAUTH_TOKEN_URL,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Token exchange failed ({resp.status}): {body}")
            tokens = await resp.json()

    # Parse id_token for account_id
    id_token = tokens.get("id_token", "")
    claims = _parse_jwt_claims(id_token)
    account_id = _extract_account_id(claims)

    # Build auth data
    expires_in = tokens.get("expires_in", 3600)
    auth_data = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": int(time.time()) + expires_in,
        "account_id": account_id or "",
        "issued_at": int(time.time()),
        "auth_method": "chatgpt_oauth_pkce",
    }

    # Save tokens
    _save_auth(auth_data)

    masked_token = auth_data["access_token"][:20] + "..."
    print(f"\nAuthentication successful!")
    print(f"Token: {masked_token}")
    print(f"Expires: {time.ctime(auth_data['expires_at'])}")
    print(f"Saved to: {AUTH_FILE}")

    return auth_data


def _save_auth(auth_data: dict) -> None:
    """Save auth data with restricted permissions (atomic write)."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = AUTH_FILE.with_suffix(".tmp")
    with open(tmp_file, "w") as f:
        json.dump(auth_data, f, indent=2)
    os.chmod(tmp_file, 0o600)
    os.rename(tmp_file, AUTH_FILE)
    logger.debug("Auth data saved to %s", AUTH_FILE)


async def logout() -> None:
    """Remove stored authentication."""
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()
        print("Logged out. Auth file removed.")
    else:
        print("No auth file found.")


async def status() -> None:
    """Show current authentication status."""
    if not AUTH_FILE.exists():
        print("Not authenticated. Run 'login' to authenticate.")
        return

    with open(AUTH_FILE) as f:
        auth = json.load(f)

    now = time.time()
    expires_at = auth.get("expires_at", 0)
    has_refresh = bool(auth.get("refresh_token"))

    print(f"Status: {'VALID' if expires_at > now else 'EXPIRED'}")
    print(f"Access token expires: {time.ctime(expires_at)}")
    print(f"Refresh token: {'present' if has_refresh else 'missing'}")
    print(f"Account ID: {auth.get('account_id', 'unknown')}")
    print(f"Auth file: {AUTH_FILE}")
