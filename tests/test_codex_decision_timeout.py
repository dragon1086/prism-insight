"""Codex BUY timeouts: effort-sized budget, distinguishable timeout, visible fallback.

2026-10-01..10-10 (db-server): the account mapping raised effort to xhigh while the
cron BUY timeout stayed at 300s; 14 of 46 KR+US BUY calls were cut and silently
decided by the mcp-agent fallback. SELL is out of scope here and must stay unchanged.
"""
import ast
import base64
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from prism_core.codex_config import (
    CodexFastError,
    CodexFastTimeout,
    BUY_EFFORT_TIMEOUT_FLOOR_SECONDS,
    resolve_buy_codex_settings,
    resolve_sell_codex_settings,
)

ROOT = Path(__file__).resolve().parents[1]
CRON = {"PRISM_BUY_CODEX_TIMEOUT": "300", "PRISM_SELL_CODEX_TIMEOUT": "180", "PRISM_CODEX_FAST_TIMEOUT": "120"}


def _auth_file(tmp_path, email):
    claims = {"https://api.openai.com/profile": {"email": email}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    path = tmp_path / "chatgpt_auth.json"
    path.write_text(json.dumps({"access_token": f"h.{payload}.s"}))
    return str(path)


# --- budget ------------------------------------------------------------------

def test_account_raised_xhigh_lifts_buy_timeout_only(tmp_path):
    env = {**CRON, "PRISM_BUY_CODEX_EFFORT": "medium", "PRISM_SELL_CODEX_EFFORT": "medium",
           "PRISM_CODEX_EFFORT_BY_ACCOUNT": "dragon1086@naver.com=medium,munsangrok@gmail.com=xhigh",
           "PRISM_CODEX_AUTH_FILE": _auth_file(tmp_path, "munsangrok@gmail.com")}
    buy, sell = resolve_buy_codex_settings(env), resolve_sell_codex_settings(env)
    assert (buy.reasoning_effort, buy.timeout) == ("xhigh", 480)
    assert (sell.reasoning_effort, sell.timeout) == ("xhigh", 180)  # SELL budget untouched


def test_medium_account_keeps_configured_timeouts(tmp_path):
    env = {**CRON, "PRISM_BUY_CODEX_EFFORT": "medium", "PRISM_SELL_CODEX_EFFORT": "medium",
           "PRISM_CODEX_EFFORT_BY_ACCOUNT": "dragon1086@naver.com=medium,munsangrok@gmail.com=xhigh",
           "PRISM_CODEX_AUTH_FILE": _auth_file(tmp_path, "dragon1086@naver.com")}
    assert resolve_buy_codex_settings(env).timeout == 300
    assert resolve_sell_codex_settings(env).timeout == 180


@pytest.mark.parametrize("effort,configured,expected", [
    (None, "90", 90), ("high", "120", 120), ("xhigh", "300", 480), ("xhigh", "600", 600),
    ("max", "300", 600), ("ultra", "120", 600),
])
def test_floor_only_raises_and_never_exceeds_cap(effort, configured, expected):
    env = {"PRISM_BUY_CODEX_TIMEOUT": configured}
    if effort:
        env["PRISM_BUY_CODEX_EFFORT"] = effort
    assert resolve_buy_codex_settings(env).timeout == expected
    assert max(BUY_EFFORT_TIMEOUT_FLOOR_SECONDS.values()) <= 600


# --- distinguishable timeout -----------------------------------------------------

def test_backend_timeout_is_a_codex_fast_timeout(monkeypatch):
    from cores.llm import codex_oauth_fast_backend as backend
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_command", lambda *a: [sys.executable, "-c", "import time; time.sleep(30)"])
    with pytest.raises(CodexFastTimeout, match="timed out after 0.5s") as error:
        backend.generate_codex_fast(system_prompt="s", user_prompt="u", timeout=.5)
    assert isinstance(error.value, CodexFastError)  # existing `except CodexFastError` callers unchanged
    assert backend.CodexFastTimeout is CodexFastTimeout


# --- visible fallback --------------------------------------------------------------

@pytest.fixture
def spool(tmp_path, monkeypatch):
    import observability.fallbacks as fallbacks
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(path))
    monkeypatch.setattr(fallbacks, "_alerted", set())

    def read():
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return read


@pytest.mark.asyncio
async def test_fallback_event_and_one_alert_per_market_side(spool):
    from observability.fallbacks import note_codex_fallback
    sent = []

    async def sender(text):
        sent.append(text)
        return True

    await note_codex_fallback("us", "sell", "SPCX", CodexFastTimeout("Codex Fast timed out after 180s"),
                              timeout_s=180, alert_sender=sender)
    await note_codex_fallback("US", "sell", "FRSH", CodexFastTimeout("Codex Fast timed out after 180s"),
                              timeout_s=180, alert_sender=sender)
    await note_codex_fallback("US", "buy", "TSLA", "parse_failed", timeout_s=480, alert_sender=sender)
    await note_codex_fallback("KR", "buy", "005930", RuntimeError("SECRET token=abc"), alert_sender=sender)

    events = spool()
    assert [e["event_type"] for e in events] == ["codex.decision_fallback"] * 4
    first = events[0]
    assert (first["market"], first["ticker"], first["severity"]) == ("US", "SPCX", "WARNING")
    assert first["attributes"] == {"decision": "sell", "reason": "timeout", "error_type": "CodexFastTimeout",
                                   "detail": "Codex Fast timed out after 180s", "timeout_s": 180,
                                   "fallback": "mcp-agent"}
    assert events[2]["attributes"]["reason"] == "parse_failed"
    assert events[3]["attributes"]["reason"] == "error"
    assert "SECRET" not in json.dumps(events)  # non-Codex exception text is never recorded
    assert len(sent) == 3  # US sell once, US buy once, KR buy once
    assert "SPCX" in sent[0] and "제한 시간(180초) 초과" in sent[0]
    assert "SECRET" not in "".join(sent)


@pytest.mark.asyncio
async def test_fallback_note_never_raises(spool, monkeypatch):
    import observability.fallbacks as fallbacks
    from observability.fallbacks import note_codex_fallback

    async def broken(text):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(fallbacks, "emit_event", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    assert await note_codex_fallback("KR", "sell", "252990", CodexFastTimeout("x"), alert_sender=broken) is None


@pytest.mark.asyncio
async def test_test_runs_never_send_real_alerts(spool, monkeypatch):
    import prism_core.ops_alert as ops_alert
    from observability.fallbacks import note_codex_fallback
    send = AsyncMock()
    monkeypatch.setattr(ops_alert, "send_ops_alert", send)
    await note_codex_fallback("US", "sell", "BKH", CodexFastTimeout("x"))
    send.assert_not_awaited()  # PRISM_DISABLE_SIGNAL_PUBLISH / PYTEST_CURRENT_TEST guard
    assert len(spool()) == 1


# --- wired into the BUY paths only ------------------------------------------------

@pytest.mark.parametrize("effort", ["xhigh", "max", "ultra"])
def test_sell_settings_ignore_the_buy_floor(effort):
    assert resolve_sell_codex_settings({"PRISM_SELL_CODEX_EFFORT": effort, "PRISM_SELL_CODEX_TIMEOUT": "180"}).timeout == 180


@pytest.mark.parametrize("path", ["stock_tracking_enhanced_agent.py", "prism-us/us_stock_tracking_agent.py"])
def test_sell_dispatch_is_not_modified(path):
    source = (ROOT / path).read_text("utf-8")
    method = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_analyze_sell_decision")
    assert "note_codex_fallback" not in ast.unparse(method)


@pytest.mark.parametrize("market,path", [
    ("KR", "stock_tracking_agent.py"),
    ("US", "prism-us/us_stock_tracking_agent.py"),
])
def test_buy_codex_failure_and_parse_failure_are_recorded(market, path):
    source = (ROOT / path).read_text("utf-8")
    method = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_extract_trading_scenario")
    calls = [ast.unparse(n) for n in ast.walk(method) if isinstance(n, ast.Await)
             and "note_codex_fallback" in ast.unparse(n)]
    assert any(f"'{market}', 'buy', ticker, codex_err" in c for c in calls), calls
    assert any(f"'{market}', 'buy', ticker, 'parse_failed'" in c for c in calls), calls
    handler = next(n for n in ast.walk(method) if isinstance(n, ast.ExceptHandler) and n.name == "codex_err")
    assert "note_codex_fallback" in ast.unparse(handler)
