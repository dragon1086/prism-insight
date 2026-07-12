"""Regression tests for dynamic ChatGPT OAuth quota telemetry.

Run: .venv/bin/python -m pytest tests/test_oauth_healthcheck.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "oauth_healthcheck.py"
_SPEC = importlib.util.spec_from_file_location("oauth_healthcheck", _SOURCE)
assert _SPEC and _SPEC.loader
oauth_healthcheck = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oauth_healthcheck)


def _quota(**overrides: int | str) -> dict:
    quota = {
        "status": 200,
        "plan_type": "prolite",
        "active_limit": "premium",
        "primary_used_pct": 10,
        "primary_window_min": 300,
        "primary_reset_at": 0,
        "primary_reset_after_s": 10_800,
        "secondary_used_pct": 10,
        "secondary_window_min": 10_080,
        "secondary_reset_at": 0,
        "secondary_reset_after_s": 604_800,
        "credits_has": "false",
        "credits_balance": "-",
        "credits_unlimited": "false",
    }
    quota.update(overrides)
    return quota


def test_legacy_five_hour_and_weekly_telemetry_keeps_both_windows():
    text, danger = oauth_healthcheck._format_quota_report(_quota())

    assert danger is False
    assert "🗓 주간(7일): 사용 10% · 잔량 90%" in text
    assert "⏱ 5시간: 사용 10% · 잔량 90%" in text


def test_current_single_week_telemetry_uses_primary_window_and_omits_secondary():
    text, danger = oauth_healthcheck._format_quota_report(_quota(
        primary_window_min=10_080,
        primary_reset_after_s=588_000,
        secondary_window_min=0,
        secondary_used_pct=0,
        secondary_reset_after_s=0,
    ))

    assert danger is False
    assert "🗓 주간(7일): 사용 10% · 잔량 90%" in text
    assert "5시간" not in text
    assert text.count("리셋:") == 1


def test_unavailable_window_does_not_trigger_low_quota_warning():
    text, danger = oauth_healthcheck._format_quota_report(_quota(
        primary_window_min=10_080,
        primary_used_pct=10,
        secondary_window_min=0,
        secondary_used_pct=100,
    ))

    assert danger is False
    assert "⚠️" not in text


def test_available_low_quota_and_429_remain_dangerous():
    low_text, low_danger = oauth_healthcheck._format_quota_report(_quota(
        primary_used_pct=81,
    ))
    exhausted_text, exhausted_danger = oauth_healthcheck._format_quota_report(_quota(
        status=429,
        secondary_window_min=0,
    ))

    assert low_danger is True
    assert "⏱ 5시간: 사용 81% · 잔량 19% ⚠️" in low_text
    assert exhausted_danger is True
    assert "🚨 429 — 쿼터 소진됨" in exhausted_text
