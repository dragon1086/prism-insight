import asyncio
import json
from types import SimpleNamespace

import pytest

from prism_core import buy_model_shadow as shadow


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "buy_model_shadow.jsonl"
    monkeypatch.setattr(shadow, "LEDGER", path)
    monkeypatch.setattr(shadow, "_scheduled", 0)
    monkeypatch.setattr(shadow, "_semaphore", None)
    shadow._pending.clear()
    for key in ("PRISM_BUY_SHADOW_MODEL", "PRISM_BUY_SHADOW_EFFORT", "PRISM_BUY_SHADOW_MAX_RUNS",
                "PRISM_BUY_SHADOW_CODEX_BIN", "PRISM_BUY_SHADOW_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    return path


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _schedule(generate, decision="Enter", live_model="gpt-6-astra"):
    return shadow.schedule_buy_model_shadow(
        market="KR", ticker="000660", system_prompt="sys", user_prompt="prompt",
        mcp_profile="kr_trading", live_model=live_model, live_effort="xhigh",
        live_result=SimpleNamespace(latency_s=80.0, usage={"input_tokens": 10}),
        live_scenario={"decision": decision, "buy_score": 7, "rationale": "long text"},
        generate=generate, parse=json.loads, service_tier=lambda: "fast",
    )


def _fake(decision="Enter", calls=None):
    async def generate(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return SimpleNamespace(text=json.dumps({"decision": decision, "buy_score": 6}),
                               latency_s=40.0, usage={"input_tokens": 5}, mcp_calls=(1, 2))
    return generate


def test_disabled_without_model(ledger):
    async def run():
        assert _schedule(_fake()) is False
        await shadow.drain_buy_model_shadows()
    asyncio.run(run())
    assert _rows(ledger) == []


def test_records_comparison_with_same_prompt(ledger, monkeypatch):
    monkeypatch.setenv("PRISM_BUY_SHADOW_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("PRISM_BUY_SHADOW_EFFORT", "max")
    monkeypatch.setenv("PRISM_BUY_SHADOW_CODEX_BIN", "/opt/codex-new/codex")
    calls = []

    async def run():
        assert _schedule(_fake("Skip", calls)) is True
        await shadow.drain_buy_model_shadows()
    asyncio.run(run())

    (row,) = _rows(ledger)
    assert calls[0]["model"] == "gpt-6.1-sol" and calls[0]["reasoning_effort"] == "max"
    assert calls[0]["user_prompt"] == "prompt" and calls[0]["system_prompt"] == "sys"
    assert calls[0]["codex_bin"] == "/opt/codex-new/codex" and calls[0]["require_mcp_calls"] is True
    assert row["live"]["scenario"] == {"decision": "Enter", "buy_score": 7}
    assert row["shadow"]["scenario"] == {"decision": "Skip", "buy_score": 6}
    assert row["decision_match"] is False and row["tier"] == "fast"


def test_cap_counts_ledger_and_in_flight(ledger, monkeypatch):
    monkeypatch.setenv("PRISM_BUY_SHADOW_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("PRISM_BUY_SHADOW_MAX_RUNS", "3")
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"old": 1}\n')

    async def run():
        results = [_schedule(_fake()) for _ in range(4)]
        await shadow.drain_buy_model_shadows()
        return results
    assert asyncio.run(run()) == [True, True, False, False]
    assert len(_rows(ledger)) == 3


def test_same_model_as_live_is_skipped(ledger, monkeypatch):
    monkeypatch.setenv("PRISM_BUY_SHADOW_MODEL", "gpt-6-astra")

    async def run():
        return _schedule(_fake())
    assert asyncio.run(run()) is False


def test_shadow_failure_is_recorded_not_raised(ledger, monkeypatch):
    monkeypatch.setenv("PRISM_BUY_SHADOW_MODEL", "gpt-6.1-sol")

    async def boom(**kwargs):
        raise RuntimeError("secret detail")

    async def run():
        assert _schedule(boom) is True
        await shadow.drain_buy_model_shadows()
    asyncio.run(run())
    (row,) = _rows(ledger)
    assert row["shadow"]["error"] == "RuntimeError" and "secret" not in json.dumps(row)


def test_drain_cancels_stragglers(ledger, monkeypatch):
    monkeypatch.setenv("PRISM_BUY_SHADOW_MODEL", "gpt-6.1-sol")

    async def slow(**kwargs):
        await asyncio.sleep(10)

    async def run():
        assert _schedule(slow) is True
        await shadow.drain_buy_model_shadows(timeout=0.05)
    asyncio.run(run())
    assert _rows(ledger) == []
