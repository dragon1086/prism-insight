"""Runtime observation hooks do not create quote freshness or execute trades."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from observability import oneil_capture as capture


def test_default_off_zero_io(monkeypatch):
    monkeypatch.delenv("ONEIL_TAPE_CAPTURE_ENABLED", raising=False)
    monkeypatch.setattr(capture, "_tape", lambda: pytest.fail("unexpected I/O"))
    assert capture.capture_initial({}) is None
    assert capture.capture_holding({}) is None
    assert capture.capture_exit(position_id="p", price=100, source="s") is None
    assert capture.holding_observation(position_id="p", price=100, scenario={}, source="s") is None


def test_partial_quote_remains_missing(monkeypatch):
    monkeypatch.setenv("ONEIL_TAPE_CAPTURE_ENABLED", "true")
    observation = capture.holding_observation(
        position_id="legacy:US:1", price=102, scenario='{"stop_loss":90}', source="regular")
    assert observation["tick"]["evidence"] == {}
    assert observation["tick"]["current_stop"] == 90
    assert "QUOTE_TIMESTAMP_UNAVAILABLE" in observation["tick"]["reason_codes"]
    tape = Mock()
    tape.campaign_id_for_position.return_value = "campaign-1"
    tape.bind_event_identity.side_effect = lambda _, value: value
    monkeypatch.setattr(capture, "_tape", lambda: tape)
    capture.capture_holding(observation)
    tape.campaign_id_for_position.assert_called_once_with("legacy:US:1")
    tape.append_tick.assert_called_once_with("campaign-1", observation["tick"])
    capture.capture_exit(position_id="legacy:US:1", price=105, source="exit")
    assert tape.append_exit.call_args.args[1]["price"] == 105


def test_failures_do_not_escape(monkeypatch):
    monkeypatch.setenv("ONEIL_TAPE_CAPTURE_ENABLED", "true")
    def broken():
        raise RuntimeError("unavailable")
    monkeypatch.setattr(capture, "_tape", broken)
    assert capture.capture_initial({}) is None
    assert capture.capture_holding({"position_id": "p", "tick": {}}) is None
    assert capture.capture_exit(position_id="p", price=100, source="s") is None


@pytest.mark.parametrize("broken", [False, True])
def test_deferred_exits_flush_after_entire_loop_and_restore_context(monkeypatch, broken):
    monkeypatch.setenv("ONEIL_TAPE_CAPTURE_ENABLED", "true")
    events = []
    tape = Mock()
    tape.campaign_id_for_position.return_value = "campaign"
    tape.bind_event_identity.side_effect = lambda _, event: event
    tape.append_exit.side_effect = lambda *_: events.append("persist")
    monkeypatch.setattr(capture, "_tape", lambda: tape)
    @capture.defer_exit_capture
    async def nested():
        capture.capture_exit(position_id="p", price=90, source="exit")
        assert events == []
    @capture.defer_exit_capture
    async def protection():
        await nested()
        events.append("all_protection_done")
        if broken:
            raise ValueError("original failure")
    if broken:
        with pytest.raises(ValueError, match="original failure"):
            asyncio.run(protection())
    else:
        asyncio.run(protection())
    assert events == ["all_protection_done", "persist"]
    capture.capture_exit(position_id="direct", price=90, source="exit")
    assert events[-1] == "persist" and len(events) == 3


def test_real_tape_retains_missing_runtime_evidence(tmp_path, monkeypatch):
    from test_oneil_capture_tape import capture as original_capture
    from prism_core.oneil_capture_tape import OneilCaptureTape
    path = tmp_path / "observations.sqlite"
    monkeypatch.setenv("ONEIL_TAPE_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("ONEIL_TAPE_CAPTURE_DB", str(path))
    original = original_capture()
    assert capture.capture_initial(original)["status"] == "RECORDED"
    observation = capture.holding_observation(position_id=original["position_id"],
        price=102, scenario={"stop_loss": original["attributes"]["plan"]["initial_stop"]},
        source="us_regular_holdings")
    assert capture.capture_holding(observation)["evidence_status"] == "MISSING_OR_INVALID"
    assert capture.capture_exit(position_id=original["position_id"], price=105,
                                source="us_strategy_exit")["status"] == "RECORDED"
    exported = OneilCaptureTape(path).export_replay()
    assert exported["capture_coverage"]["closed"] == 1
    assert exported["capture_coverage"]["missing_or_invalid_ticks"] == 1
    assert exported["prospective_proof"] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_real_mechanical_loop_observes_only_after_protection(monkeypatch, enabled):
    from tools import hardstop_seller as loop
    monkeypatch.setenv("ONEIL_TAPE_CAPTURE_ENABLED", str(enabled))
    events = []
    connection = Mock()
    monkeypatch.setattr(loop, "_connect", lambda: connection)
    monkeypatch.setattr(loop, "_ensure_schema", lambda _: None)
    monkeypatch.setattr(loop, "load_holdings_by_ticker", lambda *_: {
        "AAPL": [{"id": 1, "buy_price": 100, "stop_loss": 93}],
        "MSFT": [{"id": 2, "buy_price": 100, "stop_loss": 93}],
        "TEST": [{"id": 3, "buy_price": 100, "stop_loss": 93}],
    })
    @asynccontextmanager
    async def context(_):
        yield SimpleNamespace(get_current_price=lambda ticker: {
            "current_price": 102 if ticker == "TEST" else 90})
    monkeypatch.setattr(loop, "_open_context", context)
    async def action(*args, **_kwargs):
        events.append("protection")
        capture.capture_exit(position_id=f"legacy:US:{args[3]['id']}", price=90, source="exit")
        assert "exit" not in events
    monkeypatch.setattr(loop, "_act_on_trigger", action)
    tape = Mock()
    tape.campaign_id_for_position.return_value = "campaign"
    tape.bind_event_identity.side_effect = lambda _, event: event
    tape.append_tick.side_effect = lambda *_: events.append("observation")
    tape.append_exit.side_effect = lambda *_: events.append("exit")
    monkeypatch.setattr(capture, "_tape", lambda: tape)
    result = asyncio.run(loop.run_market("US", "test"))
    assert result["triggered"] == 2
    assert events == ["protection", "protection"] + (["observation", "exit", "exit"] if enabled else [])
