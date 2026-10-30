from utils.btc_review_references import read_tape
from live.scenario_review_storage import review_text
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from live.scenario_contract import identity_fields
from live.scenario_review_adapter import make_calculated_review_proposer
from live.scenario_runtime import REQUIRED_CAPABILITIES, ScenarioRuntime


class Broker:
    environment = "demo"
    lane = "MAIN"
    capabilities = REQUIRED_CAPABILITIES

    def __init__(self, now):
        self.executed = []
        self.ctx = dict(account_version="1", legacy_fenced=False, protection_ok=True,
            new_risk_blocked=False, day="2026-10-14", day_start_equity=10000,
            daily_net_pnl=0, initial_equity=10000, positions=[], pending_entries=[],
            previous_hard_stop=None, realized_loss=0, fees_paid=0, funding_paid=0,
            estimated_cost_rate=.002, slippage_bps=10, mark_price=100,
            account_captured_at=now, accounting_status="confirmed", target_status={},
            price_tick=.1, quantity_step=.001, minimum_quantity=.001, minimum_notional=5)

    def context(self):
        return deepcopy(self.ctx)

    def reconcile(self):
        return {}

    def execute(self, payload, ident):
        self.executed.append(deepcopy(payload))


def wire(ctx, action):
    result = dict(**identity_fields(ctx), action=action, side="LONG" if action == "OPEN" else None,
        confidence=.7, expires_at=ctx["now"]+90, hard_stop=90 if action == "OPEN" else None,
        entries=[dict(id="e", price=100, quantity=1)] if action == "OPEN" else [],
        take_profits=[dict(id="tp", price=120, fraction=.5)] if action == "OPEN" else [],
        partial_stops=[], cancel_entry_ids=[], chase=dict(max_bps=0, max_reprices=0) if action == "OPEN" else None,
        rationale="구조 판단", leverage=10, review=None, stop_revision=None)
    if ctx.get('immediate_reduce_version') == 1:
        result['reduce_fraction'] = None
    if ctx.get("recovery_contract_version") in (1, 2):
        result["recovery"] = dict(decision="PROBE" if action == "OPEN" else "OBSERVE",
            reason="관측한 가격 구조 변화", changed_evidence=["timeframes.30m.closed.close"] if action == "OPEN" else [],
            counterevidence="반전 가능성", invalidation="구조적 손절선 이탈")
    return result


def runtime(tmp_path, *, change=None, tape_change=None, fail_second=False, empty=False, recovery=False,
            candidate_action='OPEN', selection_reason='검산 확인'):
    now = [1800000000.]
    path = tmp_path / "runtime.db"
    broker = Broker(now[0])
    captured, calls = {}, []

    def generate(**request):
        assert request["retry_transport"] is False
        calls.append(deepcopy(request))
        with sqlite3.connect(path) as observed:
            events = [json.loads(review_text(r[0])) for r in observed.execute(
                "SELECT body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence",
                (int(now[0] // 300),))]
            assert events[-1]["kind"] == "MODEL_REQUEST"
        if "wait_proposal" in request["response_schema"]["properties"]:
            first = captured.get("recovery", {}).get("first_observation", False)
            result = dict(wait_proposal=wire(captured, "WAIT"),
                candidates=[] if empty or first else [wire(captured, candidate_action)],
                no_candidate_reason="첫 관측 또는 구조 부족" if empty or first else "")
        else:
            if fail_second:
                raise RuntimeError("model failed")
            data = json.loads(request["user_prompt"])["calculated_candidates"]
            chosen = data["candidates"][0]
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale=selection_reason)
        return SimpleNamespace(text=json.dumps(result))

    adapter = make_calculated_review_proposer(generate=generate, clock=lambda: now[0])

    def proposed(snapshot, ctx, *, record):
        captured.update(deepcopy(ctx))
        tape = adapter(snapshot, ctx, record=record)
        if change:
            change(broker.ctx)
        if tape_change:
            tape_change(tape)
        return tape

    r = ScenarioRuntime(sqlite3.connect(path), broker,
        lambda *_: pytest.fail("legacy proposer called"),
        lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0],
        calculated_review_enabled=True, calculated_review_propose=proposed,
        recovery_enabled=recovery)
    r.test_clock = now
    if candidate_action == 'EXIT':
        state = r.state()
        state['active'] = dict(scenario_id='existing', initial_equity=10000, side='LONG',
            hard_stop=90, revision=0, created_at=now[0]-600, expires_at=now[0]+900)
        r._save(state)
        broker.ctx.update(positions=[dict(price=100, quantity=1)], previous_hard_stop=90)
    return r, broker, calls


def test_review_event_storage_roundtrip_and_compression_failure_fallback(tmp_path, monkeypatch):
    import live.scenario_review_storage as storage

    r, _, _ = runtime(tmp_path)
    event = {'kind': 'MODEL_REQUEST', 'content': 'abc' * 3000}
    r._record_calculated_review(7, event)
    first = r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=7 AND sequence=0').fetchone()[0]
    assert isinstance(first, bytes) and json.loads(review_text(first)) == event
    def fail(_):
        raise RuntimeError('compression_unavailable')
    monkeypatch.setattr(storage, 'review_storage', fail)
    r._record_calculated_review(7, event)
    second = r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=7 AND sequence=1').fetchone()[0]
    assert isinstance(second, str) and json.loads(second) == event


def test_optin_complete_proof_before_simulated_intent(tmp_path):
    r, broker, calls = runtime(tmp_path)
    outcome = r.tick()
    assert outcome["status"] == "intent_pending", outcome
    assert len(calls) == 2 and len(broker.executed) == 1
    tape = json.loads(read_tape(r.conn.execute("SELECT body FROM llm_scenario_review_tapes").fetchone()[0], r.conn, r.conn.execute("SELECT slot FROM llm_scenario_review_tapes").fetchone()[0], decode_legacy=review_text))
    proposal = json.loads(r.conn.execute("SELECT proposal FROM llm_scenario_decisions").fetchone()[0])
    assert tape["outcome"]["status"] == "COMPLETE"
    assert proposal["action"] == "OPEN" and "operations" not in proposal
    assert proposal["rationale"] == "구조 판단"
    assert broker.executed[0]["rationale"] == "구조 판단"
    assert tape['outcome']['result']['selection']['rationale'] == '검산 확인'
    assert tape["outcome"]["result"]["selected"]["raw_proposal"]["rationale"] == "구조 판단"
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_intents").fetchone()[0] == 1
    final_receipt = json.loads(review_text(r.conn.execute("SELECT body FROM llm_scenario_review_events ORDER BY sequence DESC LIMIT 1").fetchone()[0]))
    assert final_receipt["kind"] == "FINAL_FRESH_REVIEW_RECEIPT"
    assert final_receipt["atomic_with_intent"] is True


def test_exit_with_hold_selection_prose_keeps_candidate_reason_in_saved_plan_and_intent(tmp_path):
    reason='잔여 물량과 기존 보호 주문을 유지합니다.'
    r,broker,calls=runtime(tmp_path,candidate_action='EXIT',selection_reason=reason)
    assert r.tick()['status']=='intent_pending'
    assert len(calls)==2
    tape=json.loads(read_tape(r.conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0], r.conn, r.conn.execute('SELECT slot FROM llm_scenario_review_tapes').fetchone()[0], decode_legacy=review_text))
    selected=tape['outcome']['result']['selected']['raw_proposal']
    proposal=json.loads(r.conn.execute('SELECT proposal FROM llm_scenario_decisions').fetchone()[0])
    intent=json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents').fetchone()[0])
    # The pre-guard audit proposal remains the original normalized candidate;
    # the final intent/adapter receive the separately bound official display.
    assert proposal['action']=='EXIT' and proposal['rationale']==selected['rationale']=='구조 판단'
    for plan in (intent,broker.executed[0]):
        assert plan['action']=='EXIT'
        assert selected['rationale']=='구조 판단'
        assert plan['rationale']==tape['outcome']['result']['selected']['explanation']['text']
        assert '모델 해석(미검증): 구조 판단' in plan['rationale']
        assert reason not in plan['rationale']
    assert tape['outcome']['result']['selection']['explanation']['raw']==reason
    assert reason in tape['outcome']['result']['selection']['rationale']


def test_pre_call_oversize_keeps_failed_tape_but_reports_actual_input_error(tmp_path):
    r, broker, calls = runtime(tmp_path)
    broker.ctx['uncompressible_required_evidence'] = 'x' * 110000
    result = r.tick()
    assert result == dict(status='blocked', reason='llm_input_preparation_failed',
        failure_stage='model_input_preparation', failure_code='input_size', model_called=False)
    assert calls == [] and broker.executed == []
    tape = json.loads(read_tape(r.conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0], r.conn, r.conn.execute('SELECT slot FROM llm_scenario_review_tapes').fetchone()[0], decode_legacy=review_text))
    assert tape['outcome']['error'] == dict(type='ValueError', message='research_input_size')
    assert tape['calls'] == 0


def test_post_call_failure_is_not_claimed_as_no_model_call(tmp_path):
    def changed(tape):
        tape['outcome'] = dict(status='FAILED', error=dict(type='ValueError', message='research_input_size'))
    r, broker, calls = runtime(tmp_path, tape_change=changed)
    result = r.tick()
    assert len(calls) == 2 and broker.executed == []
    assert result['reason'] == 'reconciliation_or_proposal_failed'
    assert result.get('model_called') is not False


@pytest.mark.parametrize("change", [
    lambda c: c.update(account_version="2"),
    lambda c: c.update(account_captured_at=c["account_captured_at"]-121),
    lambda c: c.update(positions=[dict(price=100, quantity=.5)]),
    lambda c: c.update(pending_entries=[dict(id="unexpected", price=100, quantity=.5)]),
    lambda c: c.update(mark_price=89),
    lambda c: c.update(fees_paid=1),
])
def test_fresh_changes_create_no_intent_or_execution(tmp_path, change):
    r, broker, _ = runtime(tmp_path, change=change)
    assert r.tick()["status"] in {"blocked", "stale_proposal"}
    assert broker.executed == []
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_intents").fetchone()[0] == 0


def test_failed_second_call_persists_failed_tape_and_raw_first_phase(tmp_path):
    r, broker, calls = runtime(tmp_path, fail_second=True)
    assert r.tick()["status"] == "blocked"
    assert len(calls) == 2 and broker.executed == []
    tape = json.loads(read_tape(r.conn.execute("SELECT body FROM llm_scenario_review_tapes").fetchone()[0], r.conn, r.conn.execute("SELECT slot FROM llm_scenario_review_tapes").fetchone()[0], decode_legacy=review_text))
    assert tape["outcome"]["status"] == "FAILED"
    events = [json.loads(review_text(x[0])) for x in r.conn.execute("SELECT body FROM llm_scenario_review_events")]
    assert any(e["kind"] == "MODEL_RAW" and e["phase"] == "candidate" for e in events)
    assert any(e["kind"] == "MODEL_ERROR" and e["phase"] == "selection" for e in events)


def test_tampered_complete_tape_fails_before_intent(tmp_path):
    r, broker, _ = runtime(tmp_path, tape_change=lambda t: t.update(sha256="bad"))
    assert r.tick()["status"] == "blocked"
    assert broker.executed == []
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_intents").fetchone()[0] == 0


def test_runtime_wait_writes_model_reference_with_replayable_evidence(tmp_path):
    from utils.btc_review_references import read_tape, unpack

    r, broker, calls = runtime(tmp_path, empty=True)
    assert r.tick()['status'] == 'wait'
    assert len(calls) == 1 and broker.executed == []
    slot, body = r.conn.execute('SELECT slot,body FROM llm_scenario_review_tapes').fetchone()
    assert unpack(body)['version'] == 2
    tape = json.loads(read_tape(body, r.conn, slot, decode_legacy=review_text))
    assert tape['outcome']['status'] == 'COMPLETE'
    r.conn.execute('DELETE FROM llm_scenario_review_events WHERE slot=?', (slot,))
    with pytest.raises(ValueError):
        read_tape(body, r.conn, slot, decode_legacy=review_text)


def test_no_candidate_wait_has_one_call_and_no_intent(tmp_path):
    r, broker, calls = runtime(tmp_path, empty=True)
    assert r.tick()["status"] == "wait"
    assert len(calls) == 1 and not broker.executed


def test_flat_wait_becomes_retention_candidate_only_after_seven_days(tmp_path):
    from utils.btc_review_retention import candidates

    r, broker, _ = runtime(tmp_path, empty=True)
    broker.ctx.update(historical_unsettled=[], incident_epoch_blocked=False, unknown_intents=False)
    assert r.tick()['status'] == 'wait'
    slot = int(r.test_clock[0] // 300)
    assert not candidates(r.conn, r.test_clock[0] + 7 * 86400)['candidates']
    assert [row['slot'] for row in candidates(r.conn, r.test_clock[0] + 7 * 86400 + 300)['candidates']] == [slot]
    context = json.loads(r.conn.execute('SELECT context FROM llm_scenario_decisions WHERE slot=?', (slot,)).fetchone()[0])
    context.pop('unknown_intents')
    r.conn.execute('UPDATE llm_scenario_decisions SET context=? WHERE slot=?', (json.dumps(context), slot))
    assert not candidates(r.conn, r.test_clock[0] + 7 * 86400 + 300)['candidates']


@pytest.mark.parametrize("value", [1, 0, "false", None])
def test_flag_is_strict_bool(tmp_path, value):
    with pytest.raises(ValueError, match="flag_must_be_bool"):
        ScenarioRuntime(sqlite3.connect(tmp_path / "r.db"), None, None, None,
                        calculated_review_enabled=value)


def test_default_off_does_not_create_review_tables(tmp_path):
    r = ScenarioRuntime(sqlite3.connect(tmp_path / "legacy.db"), Broker(1000), None, None)
    assert not r.calculated_review_enabled
    assert not r.conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'llm_scenario_review_%'").fetchall()


def test_flag_requires_explicit_callback(tmp_path):
    with pytest.raises(ValueError, match="callback_required"):
        ScenarioRuntime(sqlite3.connect(tmp_path / "r.db"), None, None, None,
                        calculated_review_enabled=True)


def test_changed_quote_cannot_silently_rebuffer_selected_plan(tmp_path):
    r, broker, _ = runtime(tmp_path, change=lambda c: c["limit_price_quotes"].update(ask=106, bid=105))
    broker.ctx.update(execution_price_policy=dict(version="round-limit-v1"),
        limit_price_quotes=dict(bid=99, ask=100, captured_at=1800000000.))
    assert r.tick()["status"] == "blocked"
    assert broker.executed == []


def test_recovery_journal_keeps_real_phase_response_not_host_payload(tmp_path):
    r, broker, _ = runtime(tmp_path, recovery=True)
    outcome = r.tick()
    assert outcome["status"] == "intent_pending", outcome
    from live.scenario_review_storage import review_text
    from utils.btc_recovery_references import MAGIC, read_recovery
    stored = r.conn.execute("SELECT slot,body FROM llm_scenario_recovery_events WHERE kind='MODEL_RAW' ORDER BY rowid").fetchall()
    assert any(isinstance(row[1], bytes) and row[1].startswith(MAGIC) for row in stored)
    rows = [json.loads(read_recovery(body, r.conn, slot, decode_legacy=review_text, expected_kind='MODEL_RAW')) for slot, body in stored]
    assert len(rows) == 2
    assert all(row["calculated_review_version"] == 1 for row in rows)
    assert "wait_proposal" in json.loads(rows[0]["raw"])
    assert "selection_token" in json.loads(rows[1]["raw"])
    assert "action" not in json.loads(rows[1]["raw"])
    assert broker.executed


def test_missing_durable_phase_record_blocks_before_intent(tmp_path):
    r, broker, _ = runtime(tmp_path)
    callback = r.calculated_review_propose
    def forget_event(snapshot, ctx, *, record):
        tape = callback(snapshot, ctx, record=record)
        r.conn.execute("DELETE FROM llm_scenario_review_events WHERE sequence=0")
        r.conn.commit()
        return tape
    r.calculated_review_propose = forget_event
    assert r.tick()["status"] == "blocked"
    assert not broker.executed


def test_changed_loaded_policy_fails_before_model(tmp_path, monkeypatch):
    from live import scenario_calculated_review
    r, broker, calls = runtime(tmp_path)
    monkeypatch.setattr(scenario_calculated_review, "DRAFT_PROMPT", "different policy")
    assert r.tick()["status"] == "blocked"
    assert calls == [] and broker.executed == []


def test_callback_cannot_replace_registered_input(tmp_path):
    r, broker, _ = runtime(tmp_path)
    callback = r.calculated_review_propose
    def mutate(snapshot, ctx, *, record):
        ctx["mark_price"] = 101
        return callback(snapshot, ctx, record=record)
    r.calculated_review_propose = mutate
    assert r.tick()["status"] == "blocked"
    assert not broker.executed


@pytest.mark.parametrize("recovery", [False, True])
def test_slow_final_durable_record_cannot_extend_original_deadline(tmp_path, recovery):
    r, broker, calls = runtime(tmp_path, recovery=recovery)
    original_record, original_clock = r._record_calculated_review, r.clock
    def delayed_record(slot, event):
        original_record(slot, event)
        if event["kind"] == "FRESH_REVIEW_RECEIPT":
            r.clock = lambda: original_clock() + 76
    r._record_calculated_review = delayed_record
    outcome = r.tick()
    assert outcome["status"] == "blocked", outcome
    assert len(calls) == 2 and not broker.executed
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_intents").fetchone()[0] == 0
    if recovery:
        assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_recovery_events WHERE kind='DECISION'").fetchone()[0] == 0


@pytest.mark.parametrize("field,value,elapsed", [
    ("expires_at", 1005, 5), ("input_captured_at", 880, 1),
    ("account_captured_at", 880, 1), ("max_input_age_seconds", 5, 6),
])
def test_final_time_check_rejects_expiry_and_original_age_limits(tmp_path, field, value, elapsed):
    r, _, _ = runtime(tmp_path)
    r.clock = lambda: 1000 + elapsed
    context = dict(input_captured_at=1000, max_input_age_seconds=120)
    fresh = dict(account_captured_at=1000)
    payload = dict(expires_at=1090)
    target = payload if field == "expires_at" else fresh if field == "account_captured_at" else context
    target[field] = value
    with pytest.raises(ValueError, match="review_final_deadline"):
        r._assert_calculated_review_time(dict(operations=[dict(kind="clock", value=1000)]), context, fresh, payload)


def observing_runtime(tmp_path):
    r, broker, calls = runtime(tmp_path, recovery=True)
    price = [100.]
    r.snapshot = lambda: dict(valid=True, as_of_ms=r.clock()*1000,
        timeframes={"30m": {"closed": {"close": price[0], "ma10": 99.}}})
    state = r.state()
    state["breaker"] = dict(blocked=True, reasons=["three_losses"], consecutive_losses=3,
                            completed_ids=["old1", "old2", "old3"])
    r._save(state)
    return r, broker, calls, price


def test_actual_observing_first_wait_then_changed_evidence_probe(tmp_path):
    r, broker, calls, price = observing_runtime(tmp_path)
    first = r.tick()
    assert first["status"] == "wait", first
    assert r.state()["recovery"]["phase"] == "OBSERVING"
    assert len(calls) == 1 and broker.executed == []
    r.test_clock[0] += 300
    broker.ctx["account_captured_at"] = r.clock()
    price[0] += 1
    second = r.tick()
    assert second["status"] == "intent_pending", second
    state = r.state()
    assert state["breaker"]["blocked"] is True
    assert state["recovery"]["phase"] == "CONSUMED"
    assert state["recovery"]["budget"] == 50
    assert len(calls) == 3 and len(broker.executed) == 1
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED'").fetchone()[0] == 1


def test_observing_changed_fresh_guard_cannot_consume_permit(tmp_path):
    r, broker, calls, price = observing_runtime(tmp_path)
    assert r.tick()["status"] == "wait"
    r.test_clock[0] += 300
    broker.ctx["account_captured_at"] = r.clock()
    price[0] += 1
    callback = r.calculated_review_propose
    def stale_after_call(snapshot, ctx, *, record):
        tape = callback(snapshot, ctx, record=record)
        broker.ctx["fees_paid"] = 1
        return tape
    r.calculated_review_propose = stale_after_call
    outcome = r.tick()
    assert outcome["status"] == "blocked", outcome
    assert len(calls) == 3 and not broker.executed
    assert r.state()["recovery"]["phase"] == "OBSERVING"
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_intents").fetchone()[0] == 0
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED'").fetchone()[0] == 0


def test_legacy_replay_refuses_actual_calculated_phase_journal(tmp_path):
    from analysis.scenario_replay import _single_pass_audit_rows
    from backtest.scenario_tape import TapeMismatch
    r, _, _ = runtime(tmp_path, recovery=True)
    assert r.tick()["status"] == "intent_pending"
    with pytest.raises(TapeMismatch, match="multi_phase"):
        _single_pass_audit_rows(r.conn, int(r.clock() // 300))


@pytest.mark.parametrize("bodies", [
    [("MODEL_REQUEST", {}), ("MODEL_REQUEST", {})],
    [("MODEL_RAW", {"phase": "candidate"})],
    [("MODEL_REQUEST", {"calculated_review_version": 1})],
])
def test_legacy_replay_refuses_duplicate_or_partial_multiphase_record(bodies):
    from analysis.scenario_replay import _single_pass_audit_rows
    from backtest.scenario_tape import TapeMismatch
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE llm_scenario_recovery_events (slot INTEGER,kind TEXT,body TEXT)")
    conn.executemany("INSERT INTO llm_scenario_recovery_events VALUES(1,?,?)",
                     [(kind, json.dumps(body)) for kind, body in bodies])
    with pytest.raises(TapeMismatch, match="multi_phase"):
        _single_pass_audit_rows(conn, 1)


@pytest.mark.parametrize('storage', ['runtime', 'economic'])
@pytest.mark.parametrize('empty', [False, True])
def test_persisted_review_replays_order_sensitive_recovery_projection(tmp_path, storage, empty):
    from analysis.scenario_review_tape import replay_review
    from analysis.scenario_calculated_replay import EconomicTape
    from live.scenario_calculated_review import research_review
    original = {}
    r, broker, calls = runtime(tmp_path, empty=empty,
        tape_change=lambda tape: original.update(deepcopy(tape)))
    keys = [f'timeframes.30m.observed_feature_{i:03d}' for i in reversed(range(100))]
    broker.ctx['recovery'] = dict(
        baseline={key: 100 for key in keys}, current={key: 101 for key in keys},
        changed_evidence={key: dict(before=100, now=101) for key in keys})
    outcome = r.tick()
    assert outcome['status'] == ('wait' if empty else 'intent_pending'), outcome
    projected = json.loads(calls[0]['user_prompt'])['contract_context']['recovery']['changed_evidence']
    assert projected['$recovery_changes'] == 'baseline-current-v1'
    assert projected['key_order'] != sorted(projected['key_order'])
    if storage == 'runtime':
        saved = json.loads(read_tape(r.conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0], r.conn, r.conn.execute('SELECT slot FROM llm_scenario_review_tapes').fetchone()[0], decode_legacy=review_text))
    else:
        path = tmp_path / 'economic.jsonl'
        recorded = EconomicTape(path, 'record', {'purpose': 'ordered review persistence'})
        recorded.begin(1000)
        recorded.current['decision'] = {'envelope': original}
        recorded.finish(outcome)
        recorded.close()
        frozen = EconomicTape(path, 'frozen', recorded.contract)
        saved = frozen.rows[0]['envelope']
    before = deepcopy((calls, broker.executed))
    inputs = saved['inputs']
    assert replay_review(saved, research_review, inputs['snapshot'], inputs['context'],
                         inputs['contract'], bindings=inputs['bindings']) == original['outcome']
    assert (calls, broker.executed) == before
    from backtest.scenario_tape import TapeMismatch
    lossy = json.loads(json.dumps(saved, sort_keys=True))
    inputs = lossy['inputs']
    with pytest.raises(TapeMismatch, match='replay_mismatch'):
        replay_review(lossy, research_review, inputs['snapshot'], inputs['context'],
                      inputs['contract'], bindings=inputs['bindings'])
