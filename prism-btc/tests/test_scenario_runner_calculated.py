from utils.btc_review_references import read_tape
from live.scenario_review_storage import review_text
"""Operational opt-in stays demo-only, durable, no-retry and default-off."""
from copy import deepcopy
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from live import scenario_runner as runner
from tests.test_scenario_review_runtime import Broker, wire


@pytest.mark.parametrize("flags", [[], ["--protect-only"], ["--activate"]])
def test_cli_calculated_requires_decision_execute(monkeypatch, flags):
    monkeypatch.setattr(sys, "argv", ["runner", "--calculated-review", *flags])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_runner_explicit_runtime_configuration(tmp_path, monkeypatch, enabled):
    configured = {}

    class Runtime:
        def __init__(self, *args, **kwargs):
            configured.update(kwargs)

        def tick(self):
            return {"status": "wait"}

    monkeypatch.setattr(runner, "ScenarioRuntime", Runtime)
    conn = sqlite3.connect(tmp_path / "runner.db")
    try:
        assert runner.run_once(conn, Broker(1000), execute=True,
                               calculated_review=enabled)["status"] == "wait"
        assert configured["calculated_review_enabled"] is enabled
        assert configured["research_review_mode"] == (runner.CALCULATED_REVIEW_MODE if enabled else "legacy")
        assert configured["calculated_review_propose"] is (runner.operational_calculated_propose if enabled else None)
    finally:
        conn.close()


@pytest.mark.parametrize("environment,lane", [("live", "MAIN"), ("demo", "SWING")])
def test_optin_cannot_escape_demo_main(tmp_path, environment, lane):
    broker = Broker(1000)
    broker.environment, broker.lane = environment, lane
    with sqlite3.connect(tmp_path / "wrong.db") as conn:
        assert runner.run_once(conn, broker, execute=True, calculated_review=True)["reason"] == "demo_main_required"
    assert broker.executed == []


def test_protection_has_no_calculated_or_legacy_model(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "operational_calculated_propose", lambda *a, **k: pytest.fail("model called"))
    broker = Broker(1000)
    broker.reconcile = lambda: {"protection_confirmed": True}
    with sqlite3.connect(tmp_path / "protect.db") as conn:
        result = runner.run_once(conn, broker, protect_only=True,
                                 proposal=lambda *a: pytest.fail("legacy called"))
    assert result["status"] == "protection_checked" and result["model_called"] is False


@pytest.mark.parametrize("failure", [False, True])
def test_operational_row_factory_records_real_review_without_legacy_fallback(tmp_path, monkeypatch, failure):
    from live import scenario_oauth
    now = 1800000000.
    broker = Broker(now)
    conn = sqlite3.connect(tmp_path / "operational.db")
    conn.row_factory = sqlite3.Row  # Identical to main(), not the tuple-only test path.
    captured, requests = {}, []
    operational = runner.operational_calculated_propose

    def callback(snapshot, context, *, record):
        captured.update(deepcopy(context))
        return operational(snapshot, context, record=record)

    def generate(**request):
        requests.append(request)
        assert (request['model'], request['reasoning_effort']) == ('gpt-6.1-terra', 'low')
        assert request["retry_transport"] is False
        assert 0 < request["timeout"] <= 75
        assert conn.execute("SELECT COUNT(*) FROM llm_scenario_review_events").fetchone()[0] > 0
        if failure:
            raise TimeoutError("fixture transport failure")
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wire(captured, "WAIT"),
            candidates=[], no_candidate_reason="첫 관측")))

    monkeypatch.setattr(runner.time, "time", lambda: now)
    # Runtime's default clock was bound at import, so use its real current time.
    from live.scenario_runtime import ScenarioRuntime
    real_clock = ScenarioRuntime.__init__.__kwdefaults__["clock"]
    # Whole seconds round-trip through snapshot milliseconds without becoming
    # fractionally future-dated relative to the fixture's frozen review clock.
    now = float(int(real_clock()))
    broker.ctx["account_captured_at"] = now
    monkeypatch.setattr(scenario_oauth, "generate_scenario", generate)
    monkeypatch.setattr(runner, "operational_calculated_propose", callback)
    try:
        outcome = runner.run_once(conn, broker, execute=True, calculated_review=True,
            snapshot=lambda: dict(valid=True, as_of_ms=now * 1000),
            proposal=lambda *a: pytest.fail("legacy fallback"))
        assert outcome["status"] == ("blocked" if failure else "wait"), outcome
        assert len(requests) == 1
        assert broker.executed == []
        envelope = json.loads(read_tape(conn.execute("SELECT body FROM llm_scenario_review_tapes").fetchone()[0], conn, conn.execute("SELECT slot FROM llm_scenario_review_tapes").fetchone()[0], decode_legacy=review_text))
        assert envelope["outcome"]["status"] == ("FAILED" if failure else "COMPLETE")
        assert envelope["inputs"]["bindings"]["policy"] == runner.CALCULATED_REVIEW_MODE
        assert conn.execute("SELECT COUNT(*) FROM llm_scenario_intents").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("failure", [False, True])
def test_operational_fixture_handles_millisecond_roundtrip_future(tmp_path, monkeypatch, failure):
    from live.scenario_runtime import ScenarioRuntime
    stamp = 1792713600.000005
    assert stamp * 1000 / 1000 > stamp
    monkeypatch.setitem(ScenarioRuntime.__init__.__kwdefaults__, "clock", lambda: stamp)
    test_operational_row_factory_records_real_review_without_legacy_fallback(tmp_path, monkeypatch, failure)


def test_known_rejection_health_is_not_error_wait_or_model_recovery(tmp_path):
    from live import tracking
    conn = tracking.get_connection(tmp_path / "health.db")
    tracking.ensure_schema(conn)
    result = dict(status="rejected_no_action", reason="economic_rejection")
    runner.notify_runtime_status(conn, dict(status="blocked", reason="llm_call_failed"))
    runner.record_health(conn, result, loop="decision")
    runner.notify_runtime_status(conn, result)
    assert conn.execute("SELECT active FROM llm_scenario_model_incident").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM btc_events WHERE kind='error'").fetchone()[0] == 0
    health = json.loads(tracking.get_meta(conn, "scenario_health_decision", "demo"))
    assert health["status"] == "rejected_no_action"
    assert conn.execute("SELECT COUNT(*) FROM llm_scenario_outbox WHERE kind='MODEL_RECOVERED'").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("action", ["ADJUST", "EXIT"])
def test_operational_holding_row_factory_keeps_prices_and_exits(tmp_path, monkeypatch, action):
    from functools import partial
    from live import scenario_oauth
    from live.scenario_runtime import ScenarioRuntime
    now = 1800000000.
    broker = Broker(now)
    conn = sqlite3.connect(tmp_path / "holding.db")
    conn.row_factory = sqlite3.Row
    # Seed a confirmed pre-existing holding; no test exchange writes or fills.
    runtime = ScenarioRuntime(conn, broker, lambda *_: None, lambda: {}, clock=lambda: now)
    state = runtime.state()
    state["active"] = dict(scenario_id="existing", initial_equity=10000, side="LONG",
                           hard_stop=90, revision=0, created_at=now-600, expires_at=now+900)
    runtime._save(state)
    broker.ctx.update(positions=[dict(price=100, quantity=1)], previous_hard_stop=90)
    captured, calls = {}, []
    original = runner.operational_calculated_propose

    def callback(snapshot, context, *, record):
        captured.update(context)
        return original(snapshot, context, record=record)

    def generate(**request):
        calls.append(request)
        assert (request['model'], request['reasoning_effort']) == ('gpt-6.1-terra', 'low')
        assert request["retry_transport"] is False
        if "wait_proposal" in request["response_schema"]["properties"]:
            raw = wire(captured, "OPEN" if action == "ADJUST" else "EXIT")
            raw.update(action=action, entries=[])
            body = dict(wait_proposal=wire(captured, "WAIT"), candidates=[raw], no_candidate_reason="")
        else:
            candidate = next(row for row in json.loads(request["user_prompt"])["calculated_candidates"]["candidates"] if row["eligible"])
            body = dict(selection_token=f"{candidate['id']}:{candidate['receipt_sha256']}", rationale="잔량 검산")
        return SimpleNamespace(text=json.dumps(body))

    monkeypatch.setattr(runner.time, "time", lambda: now)
    monkeypatch.setattr(runner, "ScenarioRuntime", partial(ScenarioRuntime, clock=lambda: now))
    monkeypatch.setattr(runner, "operational_calculated_propose", callback)
    monkeypatch.setattr(scenario_oauth, "generate_scenario", generate)
    try:
        result = runner.run_once(conn, broker, execute=True, calculated_review=True,
            snapshot=lambda: dict(valid=True, as_of_ms=now*1000),
            proposal=lambda *_: pytest.fail("legacy fallback"))
        assert result["status"] == "intent_pending", result
        assert len(calls) == 2 and len(broker.executed) == 1
        assert broker.executed[0]["action"] == action
        assert broker.executed[0]["entries"] == []
        if action == "ADJUST":
            assert broker.executed[0]["hard_stop"] == 90
            assert broker.executed[0]["take_profits"][0]["price"] == 120
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["open", "reject", "selection_failure", "quote_drift"])
def test_runner_first_observation_then_host_sized_decision_with_row_factory(tmp_path, monkeypatch, kind):
    from functools import partial
    from live import scenario_oauth
    from live.scenario_runtime import ScenarioRuntime
    now = [1800000000.]
    broker = Broker(now[0])
    conn = sqlite3.connect(tmp_path / "steps.db")
    conn.row_factory = sqlite3.Row
    captured, calls = {}, []
    operational = runner.operational_calculated_propose
    if kind == "quote_drift":
        original_context = broker.context

        def changing_context():
            now[0] += .01  # REST capture completes after _context observation began.
            broker.ctx["account_captured_at"] = now[0]
            broker.ctx["mark_price"] += .001
            broker.ctx["limit_price_quotes"] = dict(captured_at=now[0],
                bid=broker.ctx["mark_price"]-.01, ask=broker.ctx["mark_price"]+.01)
            return original_context()

        broker.context = changing_context

    def callback(snapshot, ctx, *, record):
        captured.update(deepcopy(ctx))
        return operational(snapshot, ctx, record=record)

    def generate(**request):
        calls.append(request)
        assert request["retry_transport"] is False
        if "wait_proposal" not in request["response_schema"]["properties"]:
            if kind == "selection_failure":
                raise TimeoutError("selection fixture")
            bundle = json.loads(request["user_prompt"])["calculated_candidates"]
            candidate = next(row for row in bundle["candidates"] if row["eligible"])
            body = dict(selection_token=f"{candidate['id']}:{candidate['receipt_sha256']}", rationale="검산 승인")
        else:
            first = len(calls) == 1
            raw = wire(captured, "OPEN")
            raw["entries"][0].pop("quantity")
            raw["entries"][0]["allocation_fraction"] = .000001 if kind == "reject" else .5
            body = dict(wait_proposal=wire(captured, "WAIT"), candidates=[] if first else [raw],
                        no_candidate_reason="최초 관측" if first else "")
        return SimpleNamespace(text=json.dumps(body))

    monkeypatch.setattr(runner.time, "time", lambda: now[0])
    monkeypatch.setattr(runner, "ScenarioRuntime", partial(ScenarioRuntime, clock=lambda: now[0]))
    monkeypatch.setattr(runner, "operational_calculated_propose", callback)
    monkeypatch.setattr(scenario_oauth, "generate_scenario", generate)
    try:
        def tick():
            return runner.run_once(conn, broker, execute=True, calculated_review=True,
                snapshot=lambda: dict(valid=True, as_of_ms=now[0]*1000,
                    timeframes={"15m": {"closed": {"close": now[0]}}}),
                proposal=lambda *a: pytest.fail("legacy fallback"))
        first_outcome = tick()
        assert first_outcome["status"] == "wait", first_outcome
        now[0] += 300
        broker.ctx["account_captured_at"] = now[0]
        outcome = tick()
        expected = {"open": "intent_pending", "quote_drift": "intent_pending",
                    "reject": "rejected_no_action", "selection_failure": "blocked"}[kind]
        assert outcome["status"] == expected, outcome
        assert len(calls) == (2 if kind == "reject" else 3)
        assert len(broker.executed) == (1 if kind in {"open", "quote_drift"} else 0)
        assert conn.execute("SELECT COUNT(*) FROM llm_scenario_review_tapes").fetchone()[0] == 2
        if kind in {"open", "quote_drift"}:
            submitted = broker.executed[0]
            assert submitted["entries"][0]["quantity"] > 0
            assert "allocation_fraction" not in submitted["entries"][0]
            events = [json.loads(review_text(row[0])) for row in conn.execute("SELECT body FROM llm_scenario_review_events")]
            assert any(row["kind"] == "FINAL_FRESH_REVIEW_RECEIPT" for row in events)
            final = next(row for row in events if row["kind"] == "FINAL_FRESH_REVIEW_RECEIPT")
            assert final["stability_scope"] == "COHERENT_FINAL_OBSERVATION_EXECUTOR_FRESH_PREFLIGHT_REQUIRED"
    finally:
        conn.close()
