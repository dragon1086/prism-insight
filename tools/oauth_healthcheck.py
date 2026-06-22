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

It can ALSO report proactive subscription quota usage (--quota mode):

  3. QUOTA  — the ChatGPT/Codex backend returns the same rate-limit telemetry
     Codex CLI displays ("주간 한도 X% 사용") as `x-codex-*` response headers on
     every successful call. We fire ONE cheap probe and read:
       primary   window = ~300 min  (the "5h" limit)  -> x-codex-primary-*
       secondary window = ~10080 min (the WEEKLY limit) -> x-codex-secondary-*
     plus x-codex-plan-type / x-codex-active-limit. This lets us detect quota
     exhaustion (429) BEFORE the batch hits it. See tasks/oauth_quota_monitor.md.

On a problem it sends a Telegram alert (to OAUTH_ALERT_CHAT_ID, falling back to
TELEGRAM_CHANNEL_ID) using TELEGRAM_BOT_TOKEN. A small state file suppresses
duplicate alerts within a cooldown window so cron does not spam.

This script is READ-ONLY w.r.t. trading/DB. It only reads the token + logs and
sends a Telegram message. Intended cron lines (db-server):

    # health (every 30 min)
    */30 * * * * cd /root/prism-insight && /root/.pyenv/shims/python tools/oauth_healthcheck.py >> logs/oauth_health.log 2>&1
    # quota status report (hourly; --quota always posts a status line)
    0 * * * * cd /root/prism-insight && /root/.pyenv/shims/python tools/oauth_healthcheck.py --quota >> logs/oauth_health.log 2>&1

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
# Alerts may need a DIFFERENT bot than the public broadcast bot (the admin/personal
# channel is often served by a separate bot). Prefer OAUTH_ALERT_BOT_TOKEN.
BOT_TOKEN = os.getenv("OAUTH_ALERT_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

# --- Quota config ----------------------------------------------------------
# Probe uses the lightest Codex-compatible model (api_translator._MODEL_MAP
# target) so it burns almost nothing of the quota it is measuring.
QUOTA_PROBE_MODEL = os.getenv("OAUTH_QUOTA_PROBE_MODEL", "gpt-5.4-mini")
# "Remaining < this %" on EITHER window triggers the ⚠️ warning highlight.
QUOTA_WARN_REMAINING_PCT = int(os.getenv("OAUTH_QUOTA_WARN_REMAINING_PCT", "20"))

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


def _fmt_reset(reset_at: int, reset_after_s: int) -> str:
    """Human-friendly reset time: KST clock + relative hours."""
    if reset_at:
        try:
            local = time.strftime("%m/%d %H:%M", time.localtime(reset_at))
        except Exception:
            local = "?"
    else:
        local = "?"
    if reset_after_s:
        hrs = reset_after_s / 3600.0
        rel = f"{hrs/24:.1f}일 후" if hrs >= 24 else f"{hrs:.1f}시간 후"
    else:
        rel = "?"
    return f"{local} ({rel})"


async def _probe_quota() -> tuple[dict | None, str]:
    """Fire ONE cheap Codex call and read x-codex-* rate-limit headers.

    Returns (quota_dict, detail). quota_dict is None on failure (detail says why).
    On HTTP 429 we still return the parsed headers/body so the caller can report
    "exhausted + reset time".
    """
    import aiohttp

    from cores.chatgpt_proxy.constants import CHATGPT_RESPONSES_URL
    from cores.chatgpt_proxy.token_manager import TokenManager

    tm = TokenManager()
    try:
        token = await tm.get_token()
        account_id = await tm.get_account_id()
    except Exception as e:  # noqa: BLE001
        return None, f"토큰 획득 실패: {e!r}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
        "accept": "text/event-stream",
        "originator": "codex_cli_rs",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id

    body = {
        "model": QUOTA_PROBE_MODEL,
        "instructions": "quota probe",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "ok"}]}],
        "stream": True,
        "store": False,
        "reasoning": {"effort": "low"},
    }

    def _int(h: dict, key: str) -> int:
        try:
            return int(h.get(key, "") or 0)
        except (TypeError, ValueError):
            return 0

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                CHATGPT_RESPONSES_URL, json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                h = resp.headers
                status = resp.status
                # Drain the stream so the connection closes cleanly (cheap call).
                try:
                    await resp.read()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        return None, f"프록시/백엔드 호출 실패: {e!r}"

    if not any(k.lower().startswith("x-codex-") for k in h):
        return None, f"쿼터 헤더 없음 (status={status}). 백엔드 응답에 x-codex-* 미포함."

    quota = {
        "status": status,
        "plan_type": h.get("x-codex-plan-type", "?"),
        "active_limit": h.get("x-codex-active-limit", "?"),
        "primary_used_pct": _int(h, "x-codex-primary-used-percent"),
        "primary_window_min": _int(h, "x-codex-primary-window-minutes"),
        "primary_reset_at": _int(h, "x-codex-primary-reset-at"),
        "primary_reset_after_s": _int(h, "x-codex-primary-reset-after-seconds"),
        "secondary_used_pct": _int(h, "x-codex-secondary-used-percent"),
        "secondary_window_min": _int(h, "x-codex-secondary-window-minutes"),
        "secondary_reset_at": _int(h, "x-codex-secondary-reset-at"),
        "secondary_reset_after_s": _int(h, "x-codex-secondary-reset-after-seconds"),
        "credits_has": h.get("x-codex-credits-has-credits", "?"),
        "credits_balance": h.get("x-codex-credits-balance", "") or "-",
        "credits_unlimited": h.get("x-codex-credits-unlimited", "?"),
    }
    return quota, f"status={status} plan={quota['plan_type']}"


def _format_quota_report(q: dict) -> tuple[str, bool]:
    """Build the Korean Telegram body. Returns (text, danger)."""
    wk_used = q["secondary_used_pct"]
    wk_left = max(0, 100 - wk_used)
    pr_used = q["primary_used_pct"]
    pr_left = max(0, 100 - pr_used)

    is_429 = q["status"] == 429
    low_week = wk_left < QUOTA_WARN_REMAINING_PCT
    low_5h = pr_left < QUOTA_WARN_REMAINING_PCT
    danger = is_429 or low_week or low_5h

    head = "⚠️ ChatGPT 쿼터 경고" if danger else "📊 ChatGPT 쿼터 현황"
    wk_mark = " ⚠️" if (is_429 or low_week) else ""
    pr_mark = " ⚠️" if (is_429 or low_5h) else ""

    lines = [
        head,
        f"플랜: {q['plan_type']} (active={q['active_limit']})",
        "",
        f"🗓 주간(7일): 사용 {wk_used}% · 잔량 {wk_left}%{wk_mark}",
        f"   리셋: {_fmt_reset(q['secondary_reset_at'], q['secondary_reset_after_s'])}",
        f"⏱ 5시간: 사용 {pr_used}% · 잔량 {pr_left}%{pr_mark}",
        f"   리셋: {_fmt_reset(q['primary_reset_at'], q['primary_reset_after_s'])}",
    ]
    if is_429:
        lines.insert(1, "🚨 429 — 쿼터 소진됨. 위 리셋시각까지 대기 필요.")
    if str(q.get("credits_unlimited")).lower() == "true":
        lines.append("크레딧: 무제한")
    elif str(q.get("credits_has")).lower() == "true":
        lines.append(f"크레딧 잔액: {q['credits_balance']}")
    return "\n".join(lines), danger


async def _run_quota(force_send: bool) -> int:
    """Probe quota and report to Telegram. Returns 1 if danger, else 0.

    force_send=True (default for --quota) posts the status line every run.
    Danger conditions additionally route through _alert (cooldown-suppressed).
    """
    quota, detail = await _probe_quota()
    if quota is None:
        print(f"[oauth-health] quota probe failed: {detail}")
        if force_send:
            _send_telegram(f"📊 ChatGPT 쿼터 조회 실패\n{detail}")
        return 0

    text, danger = _format_quota_report(quota)
    print(f"[oauth-health] quota: {detail} "
          f"week_used={quota['secondary_used_pct']}% 5h_used={quota['primary_used_pct']}% danger={danger}")

    if danger:
        # Cooldown-suppressed critical alert (so cron does not spam on sustained low).
        _alert("ChatGPT 쿼터 임계 도달", text)
    if force_send and not danger:
        _send_telegram(text)
    return 1 if danger else 0


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
    if "--test-alert" in sys.argv:
        # Validate the alert delivery path (bot token + chat id) end-to-end.
        _ok = _send_telegram("✅ PRISM OAuth 워치독 테스트 — 이 메시지가 보이면 알림 경로 정상입니다.")
        print(f"[oauth-health] test alert sent={_ok} chat={ALERT_CHAT_ID}")
        raise SystemExit(0 if _ok else 1)
    if "--quota-dry-run" in sys.argv:
        # Probe + print the report to stdout only (no Telegram send).
        async def _dry() -> int:
            q, det = await _probe_quota()
            if q is None:
                print(f"[oauth-health] quota probe failed: {det}")
                return 1
            txt, danger = _format_quota_report(q)
            print(f"[oauth-health] DRY-RUN danger={danger}\n----- message -----\n{txt}\n-------------------")
            return 0
        raise SystemExit(asyncio.run(_dry()))
    if "--quota" in sys.argv:
        # Always post a quota status line; danger conditions also alert.
        raise SystemExit(asyncio.run(_run_quota(force_send=True)))
    raise SystemExit(asyncio.run(main()))
