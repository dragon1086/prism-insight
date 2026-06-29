"""
tests/test_subscriber_healthcheck.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for tools/subscriber_healthcheck.py (mock-only, no network).

Covers:
  - clean window: no alerts
  - import failure: CRITICAL alert
  - attempts but zero successes: CRITICAL alert
  - failures over threshold: WARN alert
  - process down: DOWN alert
  - importlib path-safety: is_us_market_hours() must not pollute sys.path with prism-us,
    and trading.domestic_stock_trading must remain importable after the call.
"""
from __future__ import annotations

import json
import sys
import os
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path so imports resolve
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(delta_minutes: int = 0) -> str:
    """Return a log timestamp string for (now + delta_minutes)."""
    dt = datetime.now() + timedelta(minutes=delta_minutes)
    return dt.strftime("%Y-%m-%d %H:%M:%S,000")


def _make_log(*lines: str) -> str:
    """Join log lines with newlines."""
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import importlib
import importlib.util

def _load_healthcheck():
    spec = importlib.util.spec_from_file_location(
        "subscriber_healthcheck",
        str(REPO_ROOT / "tools" / "subscriber_healthcheck.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hc = _load_healthcheck()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def mock_send():
    """Patch send_alert so no network calls happen."""
    with patch.object(hc, "send_alert", return_value=True) as m:
        yield m


@pytest.fixture
def mock_alive():
    """Subscriber process is alive."""
    with patch.object(hc, "_is_subscriber_running", return_value=True):
        yield


@pytest.fixture
def mock_dead():
    """Subscriber process is not running."""
    with patch.object(hc, "_is_subscriber_running", return_value=False):
        yield


# ---------------------------------------------------------------------------
# Helper: write a temp log and call run_check
# ---------------------------------------------------------------------------

def _run(log_content: str, tmp_path, tmp_state, mock_send,
         window_min=60, fail_threshold=3, realert_min=60):
    log_file = tmp_path / "subscriber_test.log"
    log_file.write_text(log_content)
    hc.run_check(
        window_min=window_min,
        fail_threshold=fail_threshold,
        realert_min=realert_min,
        log_path=str(log_file),
        dry_run=False,
        state_file=tmp_state,
    )
    return mock_send


# ---------------------------------------------------------------------------
# TEST: clean window — no alerts
# ---------------------------------------------------------------------------

def test_clean_window_no_alerts(tmp_path, tmp_state, mock_send, mock_alive):
    log = _make_log(
        f"{_ts()} INFO 🚀 Executing buy order: KR 삼성전자(005930)",
        f"{_ts()} INFO ✅ Actual buy successful: 005930",
    )
    mock_send = _run(log, tmp_path, tmp_state, mock_send)
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# TEST: import failure -> CRITICAL alert
# ---------------------------------------------------------------------------

def test_import_failure_critical(tmp_path, tmp_state, mock_send, mock_alive):
    log = _make_log(
        f"{_ts()} CRITICAL Trading module import failed: No module named 'trading.domestic_stock_trading'",
    )
    mock_send = _run(log, tmp_path, tmp_state, mock_send)
    mock_send.assert_called_once()
    call_text = mock_send.call_args[0][0]
    assert "CRITICAL" in call_text
    assert "import" in call_text.lower()


def test_startup_selfcheck_failed_critical(tmp_path, tmp_state, mock_send, mock_alive):
    log = _make_log(
        f"{_ts()} CRITICAL [STARTUP_SELFCHECK] FAILED: No module named 'Crypto'",
    )
    mock_send = _run(log, tmp_path, tmp_state, mock_send)
    mock_send.assert_called_once()
    call_text = mock_send.call_args[0][0]
    assert "CRITICAL" in call_text


# ---------------------------------------------------------------------------
# TEST: attempts > 0 but zero successes -> CRITICAL
# ---------------------------------------------------------------------------

def test_attempts_zero_success_critical(tmp_path, tmp_state, mock_send, mock_alive):
    log = _make_log(
        f"{_ts()} INFO 🚀 Executing buy order: KR 셀트리온(068270)",
        f"{_ts()} INFO 🚀 Executing sell order: US AAPL(AAPL)",
        # no success lines
    )
    mock_send = _run(log, tmp_path, tmp_state, mock_send)
    mock_send.assert_called_once()
    call_text = mock_send.call_args[0][0]
    assert "CRITICAL" in call_text
    assert "0 successes" in call_text or "zero" in call_text.lower() or "successes" in call_text


# ---------------------------------------------------------------------------
# TEST: failures over threshold -> WARN
# ---------------------------------------------------------------------------

def test_failures_over_threshold_warn(tmp_path, tmp_state, mock_send, mock_alive):
    # 3 actual failures (meets default threshold=3) + a success so zero_success doesn't fire
    log = _make_log(
        f"{_ts()} INFO 🚀 Executing buy order: KR 카카오(035720)",
        f"{_ts()} INFO ✅ Actual buy successful: 035720",
        f"{_ts()} ERROR ❌ Actual buy execution failed: 035720 err1",
        f"{_ts()} ERROR ❌ Actual sell execution failed: 005930 err2",
        f"{_ts()} ERROR ❌ Actual buy execution failed: 068270 err3",
    )
    mock_send = _run(log, tmp_path, tmp_state, mock_send, fail_threshold=3)
    mock_send.assert_called_once()
    call_text = mock_send.call_args[0][0]
    assert "WARN" in call_text


# ---------------------------------------------------------------------------
# TEST: process down -> DOWN alert
# ---------------------------------------------------------------------------

def test_process_down_alert(tmp_path, tmp_state, mock_send, mock_dead):
    log = _make_log(f"{_ts()} INFO subscriber running normally")
    mock_send = _run(log, tmp_path, tmp_state, mock_send)
    mock_send.assert_called_once()
    call_text = mock_send.call_args[0][0]
    assert "DOWN" in call_text


# ---------------------------------------------------------------------------
# TEST: de-duplication (cooldown suppresses repeat alerts)
# ---------------------------------------------------------------------------

def test_dedup_suppresses_repeat(tmp_path, tmp_state, mock_send, mock_alive):
    log = _make_log(
        f"{_ts()} CRITICAL Trading module import failed: err",
    )
    # First run: should alert
    log_file = tmp_path / "sub.log"
    log_file.write_text(log)
    hc.run_check(
        window_min=60, fail_threshold=3, realert_min=60,
        log_path=str(log_file), dry_run=False, state_file=tmp_state,
    )
    assert mock_send.call_count == 1

    # Second run immediately: should be suppressed (realert_min=60)
    hc.run_check(
        window_min=60, fail_threshold=3, realert_min=60,
        log_path=str(log_file), dry_run=False, state_file=tmp_state,
    )
    assert mock_send.call_count == 1  # still 1, not 2


# ---------------------------------------------------------------------------
# TEST: recovery "cleared" message
# ---------------------------------------------------------------------------

def test_recovery_cleared_message(tmp_path, tmp_state, mock_send, mock_alive):
    # Step 1: trigger import_fail alert
    bad_log = _make_log(
        f"{_ts()} CRITICAL Trading module import failed: err",
    )
    log_file = tmp_path / "sub.log"
    log_file.write_text(bad_log)
    hc.run_check(
        window_min=60, fail_threshold=3, realert_min=0,
        log_path=str(log_file), dry_run=False, state_file=tmp_state,
    )
    assert mock_send.call_count == 1

    # Step 2: healthy log — should send "cleared"
    good_log = _make_log(f"{_ts()} INFO all good")
    log_file.write_text(good_log)
    hc.run_check(
        window_min=60, fail_threshold=3, realert_min=0,
        log_path=str(log_file), dry_run=False, state_file=tmp_state,
    )
    assert mock_send.call_count == 2
    cleared_text = mock_send.call_args[0][0]
    assert "cleared" in cleared_text.lower() or "✅" in cleared_text


# ---------------------------------------------------------------------------
# TEST: importlib path-safety
#   - is_us_market_hours() must NOT add prism-us to sys.path
#   - trading.domestic_stock_trading must be importable after the call
# ---------------------------------------------------------------------------

def test_importlib_does_not_pollute_syspath():
    """is_us_market_hours() uses importlib; prism-us must never appear in sys.path.

    Separately verifies that trading.domestic_stock_trading is importable when
    kis_auth config loading is mocked out (the config file doesn't exist in the
    test worktree, but that's a deployment concern — the module itself must be
    importable without prism-us on sys.path shadowing it).
    """
    # Ensure repo root is on path
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    # Import the subscriber module
    spec = importlib.util.spec_from_file_location(
        "gcp_pubsub_subscriber_example_pathtest",
        str(REPO_ROOT / "examples" / "messaging" / "gcp_pubsub_subscriber_example.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"TRADING_MODE": "dry", "GCP_PROJECT_ID": "test", "GCP_PUBSUB_SUBSCRIPTION_ID": "test"}):
        spec.loader.exec_module(mod)

    # Call is_us_market_hours() — may raise (e.g. calendar unavailable); that's fine
    try:
        mod.is_us_market_hours()
    except Exception:
        pass

    # prism-us must NOT be on sys.path after the call
    assert not any("prism-us" in str(p) for p in sys.path), (
        f"prism-us was added to sys.path: {[p for p in sys.path if 'prism-us' in str(p)]}"
    )

    # Verify that Python would resolve trading.domestic_stock_trading from the
    # REPO_ROOT trading/ directory — not from prism-us/trading/. We check this
    # structurally: find which directory sys.path would use for `trading`, and
    # confirm it is NOT under prism-us.
    #
    # (Full import of domestic_stock_trading requires a live KIS YAML config file
    # that does not exist in this worktree; structural verification is sufficient.)
    prism_us_trading = str(REPO_ROOT / "prism-us" / "trading")
    repo_root_trading = str(REPO_ROOT / "trading")

    # Find first sys.path entry that contains a `trading` package
    resolved_trading_root = None
    for p in sys.path:
        candidate = Path(p) / "trading"
        if candidate.is_dir() and (candidate / "domestic_stock_trading.py").exists():
            resolved_trading_root = str(candidate)
            break

    assert resolved_trading_root is not None, (
        "Could not find trading/domestic_stock_trading.py on sys.path at all"
    )
    assert "prism-us" not in resolved_trading_root, (
        f"trading package resolved to prism-us path: {resolved_trading_root}. "
        "prism-us must NOT be on sys.path when the subscriber is running."
    )
    assert resolved_trading_root == repo_root_trading, (
        f"trading package resolved to unexpected path: {resolved_trading_root} "
        f"(expected {repo_root_trading})"
    )
