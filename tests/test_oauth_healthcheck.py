"""Regression tests for dynamic ChatGPT OAuth quota telemetry.

Run: .venv/bin/python -m pytest tests/test_oauth_healthcheck.py -q
"""
from __future__ import annotations

import importlib.util
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "oauth_healthcheck.py"
_SPEC = importlib.util.spec_from_file_location("oauth_healthcheck", _SOURCE)
assert _SPEC and _SPEC.loader
oauth_healthcheck = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oauth_healthcheck)


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_failed_probe_never_exposes_raw_backend_message(status):
    detail = oauth_healthcheck._quota_http_failure(
        status, b'{"detail":"model not supported secret-token account-id"}',
    )
    assert str(status) in detail
    assert "secret-token" not in detail
    assert "account-id" not in detail
    if status == 400:
        assert "모델 미지원" in detail
        assert "잔량은 확인되지 않았습니다" in detail


def test_valid_and_rate_limited_responses_keep_quota_parsing():
    assert oauth_healthcheck._quota_http_failure(200, b"") is None
    assert oauth_healthcheck._quota_http_failure(429, b"") is None
    assert "요청 거절" in oauth_healthcheck._quota_http_failure(400, b"invalid input")


@pytest.mark.parametrize("status", [200, 400, 429])
def test_probe_supported_model_and_http_status_handling(monkeypatch, status):
    from cores.chatgpt_proxy import token_manager
    import aiohttp

    monkeypatch.delenv("OAUTH_QUOTA_PROBE_MODEL", raising=False)
    module = importlib.util.module_from_spec(_SPEC)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    _SPEC.loader.exec_module(module)
    monkeypatch.setattr(token_manager, "TokenManager", lambda: SimpleNamespace(
        get_token=AsyncMock(return_value="fake-token"),
        get_account_id=AsyncMock(return_value="fake-account"),
    ))
    sent = {}

    class Response:
        headers = {"x-codex-primary-window-minutes": "10080"}
        async def __aenter__(self):
            self.status = status
            return self
        async def __aexit__(self, *args):
            pass
        async def read(self):
            return b'{"detail":"model not supported secret-token"}'

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def post(self, url, **kwargs):
            sent.update(kwargs)
            return Response()

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    quota, detail = asyncio.run(module._probe_quota())
    assert sent["json"]["model"] == "gpt-5.6-sol"
    assert "secret-token" not in detail
    if status == 400:
        assert quota is None  # reject even if incidental quota headers exist
        assert "모델 미지원" in detail
    else:
        assert quota["status"] == status
        assert quota["primary_window_min"] == 10080


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
