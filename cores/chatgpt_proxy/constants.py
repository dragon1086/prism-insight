"""ChatGPT OAuth Proxy - Constants and configuration."""

import os
from pathlib import Path

# OAuth Constants (official Codex CLI values)
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_SCOPE = "openid profile email offline_access"
OAUTH_CALLBACK_PORT = 1455
OAUTH_REDIRECT_URI = f"http://localhost:{OAUTH_CALLBACK_PORT}/auth/callback"

# ChatGPT API
CHATGPT_API_BASE = "https://chatgpt.com/backend-api/codex"
CHATGPT_RESPONSES_URL = f"{CHATGPT_API_BASE}/responses"

# Token storage
AUTH_DIR = Path.home() / ".config" / "prism-insight"
AUTH_FILE = AUTH_DIR / "chatgpt_auth.json"

# Proxy
DEFAULT_PROXY_PORT = int(os.getenv("PRISM_CHATGPT_PROXY_PORT", "18741"))

# Token refresh buffer (seconds)
TOKEN_REFRESH_BUFFER = 300  # 5 minutes
