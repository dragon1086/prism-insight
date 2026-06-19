"""ChatGPT OAuth health & usage watchdog.

Runs on the db-server via cron (e.g. every 30 min) to catch the two failure
modes introduced by running on the ChatGPT subscription (OAuth) instead of an
API key:

  1. LOGIN EXPIRY  — the OAuth refresh_token was revoked/expired, so every LLM
     call starts failing with ChatGPTAuthExpiredError. Detected by attempting a
     real token refresh via TokenManager.
  2. COST / RATE LIMIT — subscription usage caps hit, surfacing as 429 /
     rate-limit / insufficient_quota / authentication errors in the orchestrator
     logs. Detected by scanning recent log files.

On a problem it sends a Telegram alert (to OAUTH_ALERT_CHAT_ID, falling back to
TELEGRAM_CHANNEL_ID) using TELEGRAM_BOT_TOKEN. A small state file suppresses
duplicate alerts within a cooldown window so cron does not spam.

This script is READ-ONLY w.r.t. trading/DB. It only reads the token + logs and
sends a Telegram message. Intended cron line (db-server):

    */30 * * * * cd /root/prism-insight && /root/.pyenv/shims/python tools/oauth_healthcheck.py >> logs/oauth_health.log 2>&1

Exit code: 0 = healthy, 1 = problem detected (alert attempted).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

# --- Config (env-driven) ---------------------------------------------------
LOG_DIR = Path(os.getenv("OAUTH_HEALTH_LOG_DIR", str(ROOT / "logs")))
LOG_SCAN_MINUTES = int(os.getenv("OAUTH_HEALTH_LOG_WINDOW_MIN", "90"))
ERROR_THRESHOLD = int(os.getenv("OAUTH_HEALTH_ERROR_THRESHOLD", "3"))
EXPIRY_WARN_HOURS = int(os.getenv("OAUTH_HEALTH_EXPIRY_WARN_HOURS", "24"))
ALERT_COOLDOWN_MIN = int(os.getenv("OAUTH_HEALTH_ALERT_COOLDOWN_MIN", "180"))
STATE_FILE = Path(os.getenv("OAUTH_HEALTH_STATE_FILE", "/tmp/oauth_health_state"))
ALERT_CHAT_ID = os.getenv("OAUTH_ALERT_CHAT_ID") or os.getenv("TELEGRAM_CHANNEL_ID")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Patterns that indicate auth/usage problems in orchestrator logs.
# Deliberately SPECIFIC: must match real OpenAI/proxy error strings and NOT
# benign trading-log text (e.g. "1,291,429 KRW" or "(limit)"). So we anchor on
# SDK error formatting ("Error code: 429"), exception class names, and exact
# OpenAI error codes — never a bare number or the word "limit"/"quota".
ERROR_PATTERNS = re.compile(
    r"ChatGPTAuthExpiredError"
    r"|insufficient_quota|quota[ _]?exceeded"
    r"|RateLimitError|rate_limit_exceeded|Too Many Requests"
    r"|Error code:\s*(?:429|401|403)"
    r"|authentication_error|AuthenticationError|PermissionDeniedError"
    r"|invalid_api_key|Incorrect API key"
    r"|Token refresh failed|Token retrieval failed|OAuth proxy.*fail",
    re.IGNORECASE,
)


def _send_telegram(text: str) -> bool:
    """Send a Telegram alert. Returns True on success. Uses the Bot HTTP API."""
    if not BOT_TOKEN or not ALERT_CHAT_ID:
        print(f"[oauth-health] cannot alert: BOT_TOKEN or chat id missing. msg={text}")
        return False
    try:
        import requests

        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ALERT_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f"[oauth-health] telegram send error: {e}")
        return False


def _already_alerted(signature: str) -> bool:
    """Cooldown: skip if the same alert signature fired within the window."""
    try:
        if STATE_FILE.exists():
            last_sig, last_ts = STATE_FILE.read_text().strip().split("|", 1)
            if last_sig == signature and (time.time() - float(last_ts)) < ALERT_COOLDOWN_MIN * 60:
                return True
    except Exception:
        pass
    return False


def _record_alert(signature: str) -> None:
    try:
        STATE_FILE.write_text(f"{signature}|{time.time()}")
    except Exception:
        pass


def _alert(title: str, body: str) -> None:
    sig = hashlib.sha1(f"{title}".encode()).hexdigest()[:12]
    if _already_alerted(sig):
        print(f"[oauth-health] suppressed (cooldown): {title}")
        return
    msg = f"🚨 PRISM OAuth 경보\n{title}\n\n{body}"
    ok = _send_telegram(msg)
    _record_alert(sig)
    print(f"[oauth-health] ALERT sent={ok}: {title}")


async def _check_token() -> tuple[bool, str]:
    """Return (healthy, detail). Attempts a real refresh via TokenManager."""
    from cores.chatgpt_proxy.constants import AUTH_FILE
    from cores.chatgpt_proxy.token_manager import ChatGPTAuthExpiredError, TokenManager

    if not Path(AUTH_FILE).exists():
        return False, f"OAuth 토큰 파일 없음: {AUTH_FILE} (재로그인 필요)"

    tm = TokenManager()
    try:
        await tm.get_token()  # refreshes if expired; raises if refresh_token dead
        data = tm._auth_data or {}
        expires_at = data.get("expires_at", 0)
        hours_left = (expires_at - time.time()) / 3600.0
        detail = f"access_token expires in {hours_left:.1f}h"
        # Access tokens auto-refresh; only warn if a refresh somehow left it short.
        if hours_left < 0:
            return True, detail + " (방금 갱신됨)"
        return True, detail
    except ChatGPTAuthExpiredError as e:
        return False, f"refresh_token 만료/철회 — 재로그인 필요: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"토큰 점검 중 예외: {e!r}"


def _scan_logs() -> tuple[int, list[str]]:
    """Count error-pattern hits in log files modified within the scan window."""
    if not LOG_DIR.is_dir():
        return 0, []
    cutoff = time.time() - LOG_SCAN_MINUTES * 60
    hits = 0
    samples: list[str] = []
    for log_file in LOG_DIR.glob("*.log"):
        try:
            if log_file.stat().st_mtime < cutoff:
                continue
            # Read only the tail to stay cheap.
            with log_file.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 200_000))
                tail = f.read().decode("utf-8", errors="ignore")
            for line in tail.splitlines():
                if ERROR_PATTERNS.search(line):
                    hits += 1
                    if len(samples) < 5:
                        samples.append(f"{log_file.name}: {line.strip()[:160]}")
        except Exception:
            continue
    return hits, samples


async def main() -> int:
    oauth_mode = os.getenv("PRISM_OPENAI_AUTH_MODE") == "chatgpt_oauth"
    problems = 0

    # 1) Token / login health (only meaningful when OAuth mode is active OR a token exists)
    token_healthy, token_detail = await _check_token()
    if oauth_mode and not token_healthy:
        problems += 1
        _alert("OAuth 로그인 풀림", token_detail + "\n→ `python -m cores.chatgpt_proxy.oauth_login` 후 토큰 재배치 필요")
    print(f"[oauth-health] oauth_mode={oauth_mode} token_healthy={token_healthy} ({token_detail})")

    # 2) Cost / rate-limit error scan
    hits, samples = _scan_logs()
    print(f"[oauth-health] log error hits (last {LOG_SCAN_MINUTES}m) = {hits} (threshold {ERROR_THRESHOLD})")
    if hits >= ERROR_THRESHOLD:
        problems += 1
        _alert(
            f"LLM 인증/리밋 에러 급증 ({hits}건/{LOG_SCAN_MINUTES}분)",
            "최근 로그 샘플:\n" + "\n".join(samples),
        )

    if problems == 0:
        print("[oauth-health] OK")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
