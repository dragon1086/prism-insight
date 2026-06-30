"""
tools/subscriber_healthcheck.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Standalone health monitor for the GCP Pub/Sub subscriber process.
Designed for cron (~5 min interval) on Mac.

Detects "alive but failing to execute" conditions:
  - Subscriber process not running (DOWN)
  - Trading module import failures logged (CRITICAL)
  - Attempts with zero successes (CRITICAL)
  - Execution failures over threshold (WARN)

Alerting:
  - Telegram via OAUTH_ALERT_BOT_TOKEN -> chat SUBSCRIBER_ALERT_CHAT_ID
  - De-duplication via state JSON (logs/subscriber_healthcheck_state.json)
  - Recovery "cleared" alerts when condition resolves

CLI:
  python tools/subscriber_healthcheck.py [--once] [--window-min 60]
      [--fail-threshold 3] [--realert-min 60] [--log-path PATH] [--dry-run]

Exit code: always 0 for normal cron operation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: load .env from PROJECT_ROOT
# ---------------------------------------------------------------------------
_FILE = Path(__file__).resolve()
PROJECT_ROOT = _FILE.parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN: str | None = os.getenv("OAUTH_ALERT_BOT_TOKEN")
ALERT_CHAT_ID: str = os.getenv("SUBSCRIBER_ALERT_CHAT_ID", "-1002989735551")
LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_STATE_FILE = LOG_DIR / "subscriber_healthcheck_state.json"

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_alert(text: str, dry_run: bool = False) -> bool:
    """Send a Telegram alert. Exposed for import by gcp_pubsub_subscriber_example.py.
    Returns True on success."""
    if dry_run:
        print(f"[subscriber-health][DRY-RUN] Would send: {text}")
        return True
    if not BOT_TOKEN:
        print(f"[subscriber-health] BOT_TOKEN missing, cannot send: {text}")
        return False
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": ALERT_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as exc:
        print(f"[subscriber-health] telegram send error: {exc}")
        return False


# ---------------------------------------------------------------------------
# State (de-duplication + recovery)
# ---------------------------------------------------------------------------

def _load_state(state_file: Path) -> dict:
    try:
        if state_file.exists():
            return json.loads(state_file.read_text())
    except Exception:
        pass
    return {}


def _save_state(state_file: Path, state: dict) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        print(f"[subscriber-health] state save error: {exc}")


def _should_alert(state: dict, key: str, realert_min: int) -> bool:
    """Return True if we should send an alert for this condition key."""
    entry = state.get(key)
    if entry is None:
        return True
    last_ts = entry.get("last_alert_ts", 0)
    return (_time.time() - last_ts) >= realert_min * 60


def _record_alert(state: dict, key: str) -> None:
    state[key] = {"last_alert_ts": _time.time(), "active": True}


def _clear_condition(state: dict, key: str) -> bool:
    """Return True if condition was previously active (and needs a recovery message)."""
    entry = state.get(key)
    was_active = entry is not None and entry.get("active", False)
    if was_active:
        state[key] = {"active": False, "last_alert_ts": entry.get("last_alert_ts", 0)}
    return was_active


# ---------------------------------------------------------------------------
# Process liveness
# ---------------------------------------------------------------------------

def _is_subscriber_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "gcp_pubsub_subscriber_example.py"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Log scanning
# ---------------------------------------------------------------------------

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+")

_IMPORT_FAIL_RE = re.compile(
    r"Trading module import failed|US Trading module import failed|\[STARTUP_SELFCHECK\] FAILED"
)
_BUY_ATTEMPT_RE = re.compile(r"Executing buy order")
_SELL_ATTEMPT_RE = re.compile(r"Executing sell order")
# Match BOTH log formats the subscriber emits:
#   KR: "✅ Actual buy successful"      / "❌ Actual buy failed"
#   US: "✅ 🇺🇸 US buy successful"       / "❌ 🇺🇸 US buy failed"
# (the older regexes only matched the KR "Actual ..." form, so every US trade —
#  success AND failure — went uncounted, firing a false zero_success CRITICAL
#  whenever activity was US-only. See subscriber_20260629 MU/NVDA/GOOGL batch.)
_BUY_SUCCESS_RE = re.compile(r"(?:Actual|US) buy successful")
_SELL_SUCCESS_RE = re.compile(r"(?:Actual|US) sell successful")
_EXEC_ERROR_RE = re.compile(r"Error during buy execution|Error during sell execution|Actual")
_ACTUAL_FAIL_RE = re.compile(r"(?:Actual|US) (?:buy|sell)(?: execution)? failed")
# Benign business rejections — expected outcomes, NOT system faults, so they must
# not trip WARN/CRITICAL (they only cause alert fatigue):
#   - "not found in portfolio": sell signal for a stock the subscriber doesn't hold
#     (portfolio drift) — nothing to sell, correct no-op.
#   - "quantity is 0": per-trade budget too small for even one share.
#   - "주문가능금액": insufficient buying power (KIS APBK0952 등) — account out of cash.
#   - "Order window unavailable": signal arrived in the post-close / pre-reserved-window
#     dead zone (KST 15:30–16:00). The publisher hits the SAME KIS window rule and also
#     fails, so it is a deterministic both-sides timing limitation, not a subscriber fault.
# These are counted separately (benign_rejections) for visibility but excluded from
# the failure thresholds and from the zero-success fault check.
_BENIGN_FAIL_RE = re.compile(r"not found in portfolio|quantity is 0|주문가능금액|Order window unavailable")


def _resolve_log_path(log_path: str | None) -> Path | None:
    if log_path:
        p = Path(log_path)
        return p if p.exists() else None

    # Try today's dated log first
    today = datetime.now().strftime("%Y%m%d")
    dated = LOG_DIR / f"subscriber_{today}.log"
    if dated.exists():
        return dated

    # Fallback
    fallback = LOG_DIR / "pubsub_subscriber.log"
    if fallback.exists():
        return fallback

    return None


def _parse_line_ts(line: str) -> datetime | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _scan_log(log_path: Path, window_min: int, fail_threshold: int) -> dict:
    """Scan log lines within the window. Returns counts dict."""
    cutoff = datetime.now() - timedelta(minutes=window_min)
    counts = {
        "import_fail": 0,
        "buy_attempts": 0,
        "sell_attempts": 0,
        "buy_successes": 0,
        "sell_successes": 0,
        "exec_errors": 0,
        "actual_failures": 0,
        "benign_rejections": 0,
        "lines_scanned": 0,
    }
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                ts = _parse_line_ts(line)
                if ts is None or ts < cutoff:
                    continue
                counts["lines_scanned"] += 1
                if _IMPORT_FAIL_RE.search(line):
                    counts["import_fail"] += 1
                if _BUY_ATTEMPT_RE.search(line):
                    counts["buy_attempts"] += 1
                if _SELL_ATTEMPT_RE.search(line):
                    counts["sell_attempts"] += 1
                if _BUY_SUCCESS_RE.search(line):
                    counts["buy_successes"] += 1
                if _SELL_SUCCESS_RE.search(line):
                    counts["sell_successes"] += 1
                if _ACTUAL_FAIL_RE.search(line):
                    if _BENIGN_FAIL_RE.search(line):
                        counts["benign_rejections"] += 1
                    else:
                        counts["actual_failures"] += 1
                if _EXEC_ERROR_RE.search(line) and "Error during" in line:
                    counts["exec_errors"] += 1
    except Exception as exc:
        print(f"[subscriber-health] log read error: {exc}")
    return counts


# ---------------------------------------------------------------------------
# Main check logic
# ---------------------------------------------------------------------------

def run_check(
    window_min: int = 60,
    fail_threshold: int = 3,
    realert_min: int = 60,
    log_path: str | None = None,
    dry_run: bool = False,
    state_file: Path = DEFAULT_STATE_FILE,
) -> int:
    """Run one health check pass. Returns 0 always (cron-safe)."""
    state = _load_state(state_file)
    alerts_sent = []
    clears_sent = []

    prefix = "🩺 [SUBSCRIBER]"

    # --- 1. Process liveness ---
    alive = _is_subscriber_running()
    if not alive:
        key = "process_down"
        if _should_alert(state, key, realert_min):
            msg = f"{prefix} 🔴 CRITICAL: subscriber process is DOWN (gcp_pubsub_subscriber_example.py not found in pgrep)"
            send_alert(msg, dry_run=dry_run)
            _record_alert(state, key)
            alerts_sent.append("process_down")
        else:
            print(f"[subscriber-health] process DOWN (suppressed, in cooldown)")
    else:
        was_down = _clear_condition(state, "process_down")
        if was_down:
            send_alert(f"{prefix} ✅ cleared: subscriber process is running again", dry_run=dry_run)
            clears_sent.append("process_down")

    # --- 2. Log scan (only if there is a log) ---
    resolved_log = _resolve_log_path(log_path)
    if resolved_log is None:
        print(f"[subscriber-health] no log file found, skipping log scan")
    else:
        counts = _scan_log(resolved_log, window_min, fail_threshold)

        # 2a. Import failure (CRITICAL)
        key = "import_fail"
        if counts["import_fail"] > 0:
            if _should_alert(state, key, realert_min):
                msg = (
                    f"{prefix} 🔴 CRITICAL: trading module import failure detected "
                    f"({counts['import_fail']} occurrences in last {window_min} min). "
                    f"Log: {resolved_log}"
                )
                send_alert(msg, dry_run=dry_run)
                _record_alert(state, key)
                alerts_sent.append("import_fail")
        else:
            if _clear_condition(state, key):
                send_alert(f"{prefix} ✅ cleared: no import failures in last {window_min} min", dry_run=dry_run)
                clears_sent.append(key)

        # 2b. Attempts with zero successes (CRITICAL)
        key = "zero_success"
        total_attempts = counts["buy_attempts"] + counts["sell_attempts"]
        total_successes = counts["buy_successes"] + counts["sell_successes"]
        # Benign rejections (no buying power / drift / sub-share budget) are not a
        # fault: an account that is simply out of cash will log attempts with zero
        # successes, but that is an operational state, not a broken subscriber. Only
        # flag zero-success when ≥1 attempt was NOT explained by a benign rejection
        # (i.e. a real failure or an unexplained/silent no-fill).
        non_benign_attempts = total_attempts - counts["benign_rejections"]
        zero_success_critical = total_successes == 0 and non_benign_attempts > 0
        if zero_success_critical:
            if _should_alert(state, key, realert_min):
                msg = (
                    f"{prefix} 🔴 CRITICAL: {total_attempts} trade attempts, 0 successes "
                    f"in last {window_min} min. Buy={counts['buy_attempts']}, Sell={counts['sell_attempts']}. "
                    f"Log: {resolved_log}"
                )
                send_alert(msg, dry_run=dry_run)
                _record_alert(state, key)
                alerts_sent.append("zero_success")
        else:
            if _clear_condition(state, key):
                send_alert(f"{prefix} ✅ cleared: trade successes now observed", dry_run=dry_run)
                clears_sent.append(key)

        # 2c. Failures over threshold (WARN)
        key = "fail_threshold"
        over_threshold = (
            counts["actual_failures"] >= fail_threshold
            or counts["exec_errors"] >= fail_threshold
        )
        if over_threshold:
            if _should_alert(state, key, realert_min):
                msg = (
                    f"{prefix} ⚠️ WARN: execution failures in last {window_min} min: "
                    f"actual_failures={counts['actual_failures']}, exec_errors={counts['exec_errors']} "
                    f"(benign_rejections={counts['benign_rejections']} excluded; "
                    f"threshold={fail_threshold}). Log: {resolved_log}"
                )
                send_alert(msg, dry_run=dry_run)
                _record_alert(state, key)
                alerts_sent.append("fail_threshold")
        else:
            if _clear_condition(state, key):
                send_alert(f"{prefix} ✅ cleared: failure count back below threshold", dry_run=dry_run)
                clears_sent.append(key)

    _save_state(state_file, state)

    # Summary line
    status = "DOWN" if not alive else "ALIVE"
    print(
        f"[subscriber-health] status={status} alerts={alerts_sent} clears={clears_sent} "
        + (f"log_lines={counts['lines_scanned']} benign={counts['benign_rejections']}" if resolved_log else "no_log")
    )
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Subscriber health monitor — run once (default) or loop via cron"
    )
    p.add_argument("--once", action="store_true", default=True, help="Run one check and exit (default)")
    p.add_argument("--window-min", type=int, default=60, help="Log scan window in minutes (default: 60)")
    p.add_argument("--fail-threshold", type=int, default=3, help="Failure count threshold for WARN (default: 3)")
    p.add_argument("--realert-min", type=int, default=60, help="Minimum minutes between repeat alerts (default: 60)")
    p.add_argument("--log-path", default=None, help="Explicit log file path (default: auto-detect)")
    p.add_argument("--dry-run", action="store_true", help="Print alerts instead of sending to Telegram")
    return p


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(
        run_check(
            window_min=args.window_min,
            fail_threshold=args.fail_threshold,
            realert_min=args.realert_min,
            log_path=args.log_path,
            dry_run=args.dry_run,
        )
    )
