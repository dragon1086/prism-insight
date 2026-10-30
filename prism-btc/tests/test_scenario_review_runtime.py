from utils.btc_review_references import read_tape
from utils.btc_review_event_references import MAGIC as EVENT_REF_MAGIC, read_event
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
            slot = int(now[0] // 300)
            events = [json.loads(read_event(body, observed, slot, sequence, decode_legacy=review_text))
                      for sequence, body in observed.execute(
                "SELECT sequence,body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence", (slot,))]
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


def test_model_raw_references_unique_request_and_falls_back_safely(tmp_path, monkeypatch):
    import random
    from utils import btc_review_event_references as refs
    r, _, _ = runtime(tmp_path)
    request = {'kind': 'MODEL_REQUEST', 'phase': 'single', 'request': {
        'prompt': random.Random(17).randbytes(16000).hex(), 'timeout': 50.0}}
    raw = {'kind': 'MODEL_RAW', 'phase': 'single',
           'actual_request': dict(request['request'], timeout=49.0), 'raw': 'fixture'}
    original = json.dumps(raw, sort_keys=True, allow_nan=False)
    r._record_calculated_review(7, request)
    r._record_calculated_review(7, raw)
    stored = r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=7 AND sequence=1').fetchone()[0]
    assert isinstance(stored, bytes) and stored.startswith(EVENT_REF_MAGIC)
    assert read_event(stored, r.conn, 7, 1, decode_legacy=review_text) == original

    r._record_calculated_review(8, request)
    r._record_calculated_review(8, request)
    r._record_calculated_review(8, raw)
    ambiguous = r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=8 AND sequence=2').fetchone()[0]
    assert not (isinstance(ambiguous, bytes) and ambiguous.startswith(EVENT_REF_MAGIC))
    assert review_text(ambiguous) == original

    r._record_calculated_review(9, request)
    monkeypatch.setattr(refs, 'compact', lambda *a: (_ for _ in ()).throw(ValueError('fixture')))
    r._record_calculated_review(9, raw)
    fallback = r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=9 AND sequence=1').fetchone()[0]
    assert review_text(fallback) == original


def test_model_raw_reference_does_not_commit_caller_transaction(tmp_path):
    import random
    r, _, _ = runtime(tmp_path)
    request = {'kind': 'MODEL_REQUEST', 'phase': 'single', 'request': {
        'prompt': random.Random(7).randbytes(16000).hex(), 'timeout': 50}}
    raw = {'kind': 'MODEL_RAW', 'phase': 'single', 'actual_request': dict(request['request'], timeout=49), 'raw': 'x'}
    r._record_calculated_review(10, request, commit=False)
    r._record_calculated_review(10, raw, commit=False)
    assert r.conn.in_transaction
    assert r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=10 AND sequence=1').fetchone()[0].startswith(EVENT_REF_MAGIC)
    r.conn.rollback()
    assert r.conn.execute('SELECT count(*) FROM llm_scenario_review_events WHERE slot=10').fetchone()[0] == 0


def test_model_raw_never_references_changed_source(tmp_path, monkeypatch):
    import random
    from utils import btc_review_event_references as refs
    r, _, _ = runtime(tmp_path)
    request = {'kind': 'MODEL_REQUEST', 'phase': 'single', 'request': {
        'prompt': random.Random(19).randbytes(16000).hex(), 'timeout': 50}}
    raw = {'kind': 'MODEL_RAW', 'phase': 'single', 'actual_request': dict(request['request'], timeout=49), 'raw': 'x'}
    r._record_calculated_review(11, request)
    original_compact = refs.compact
    def mutate(*args):
        candidate = original_compact(*args)
        r.conn.execute("UPDATE llm_scenario_review_events SET body='{}' WHERE slot=11 AND sequence=0")
        return candidate
    monkeypatch.setattr(refs, 'compact', mutate)
    r._record_calculated_review(11, raw)
    stored = r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=11 AND sequence=1').fetchone()[0]
    assert not (isinstance(stored, bytes) and stored.startswith(EVENT_REF_MAGIC))
    assert json.loads(review_text(stored)) == raw


def test_model_raw_locks_reference_source_during_write(tmp_path, monkeypatch):
    import random
    from utils import btc_review_event_references as refs
    r, _, _ = runtime(tmp_path)
    request = {'kind': 'MODEL_REQUEST', 'phase': 'single', 'request': {
        'prompt': random.Random(23).randbytes(16000).hex(), 'timeout': 50}}
    raw = {'kind': 'MODEL_RAW', 'phase': 'single', 'actual_request': dict(request['request'], timeout=49), 'raw': 'x'}
    r._record_calculated_review(12, request)
    original_compact = refs.compact
    lock_checked = []
    def check_lock(*args):
        with sqlite3.connect(tmp_path / 'runtime.db', timeout=0) as other:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute("UPDATE llm_scenario_review_events SET body='{}' WHERE slot=12 AND sequence=0")
        lock_checked.append(True)
        return original_compact(*args)
    monkeypatch.setattr(refs, 'compact', check_lock)
    r._record_calculated_review(12, raw)
    stored = r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=12 AND sequence=1').fetchone()[0]
    assert lock_checked == [True]
    assert stored.startswith(EVENT_REF_MAGIC)
    assert json.loads(read_event(stored, r.conn, 12, 1, decode_legacy=review_text)) == raw


def test_model_raw_source_budget_preserves_unmodified_event(tmp_path, monkeypatch):
    from analysis import scenario_review_tape
    from utils import btc_review_event_references as refs
    r, _, _ = runtime(tmp_path)
    r._record_calculated_review(13, {'kind': 'MODEL_ERROR', 'detail': 'x' * 1500})
    request = {'kind': 'MODEL_REQUEST', 'phase': 'single', 'request': {'prompt': 'short', 'timeout': 50}}
    raw = {'kind': 'MODEL_RAW', 'phase': 'single', 'actual_request': dict(request['request'], timeout=49), 'raw': 'x'}
    r._record_calculated_review(13, request)
    monkeypatch.setattr(scenario_review_tape, 'MAX_BYTES', 1024)
    monkeypatch.setattr(refs, 'compact', lambda *a: pytest.fail('source budget must stop before compaction'))
    r._record_calculated_review(13, raw)
    stored = r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=13 AND sequence=2').fetchone()[0]
    assert not (isinstance(stored, bytes) and stored.startswith(EVENT_REF_MAGIC))
    assert json.loads(review_text(stored)) == raw


def test_model_raw_rechecks_record_limit_after_acquiring_write_lock(tmp_path):
    r, _, _ = runtime(tmp_path)
    r.conn.executemany('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                       [(14, n, '{}') for n in range(127)])
    r.conn.commit()
    real = r.conn
    class RacingConnection:
        raced = False
        def __getattr__(self, name):
            return getattr(real, name)
        def execute(self, statement, parameters=()):
            if not self.raced and statement.startswith('SELECT COUNT(*) FROM llm_scenario_review_events'):
                self.raced = True
                old = real.execute(statement, parameters).fetchone()
                with sqlite3.connect(tmp_path / 'runtime.db', timeout=0) as other:
                    other.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (14, 127, '{}'))
                return SimpleNamespace(fetchone=lambda: old)
            return real.execute(statement, parameters)
    r.conn = RacingConnection()
    raw = {'kind': 'MODEL_RAW', 'phase': 'single', 'actual_request': {'timeout': 1}, 'raw': 'x'}
    with pytest.raises(ValueError, match='review_record_bound'):
        r._record_calculated_review(14, raw)
    assert real.execute('SELECT count(*) FROM llm_scenario_review_events WHERE slot=14').fetchone()[0] == 128
    real.rollback()


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
    from utils.btc_review_references import unpack
    r, broker, calls = runtime(tmp_path)
    outcome = r.tick()
    assert outcome["status"] == "intent_pending", outcome
    assert len(calls) == 2 and len(broker.executed) == 1
    stored = r.conn.execute("SELECT body FROM llm_scenario_review_tapes").fetchone()[0]
    bundle = unpack(stored)
    assert bundle['version'] == 2
    generated = [op for op in bundle['tape']['operations'] if op.get('kind') == 'generate']
    assert len(generated) == 2 and all('$btc_model_ref' in op['request'] for op in generated)
    tape = json.loads(read_tape(stored, r.conn, r.conn.execute("SELECT slot FROM llm_scenario_review_tapes").fetchone()[0], decode_legacy=review_text))
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
    events = [json.loads(read_event(body, r.conn, slot, sequence, decode_legacy=review_text))
              for slot, sequence, body in r.conn.execute("SELECT slot,sequence,body FROM llm_scenario_review_events")]
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


def test_recovery_wait_retention_requires_complete_unchanged_journal(tmp_path):
    import hashlib
    import subprocess
    import sys
    from pathlib import Path
    from utils.btc_review_retention import candidates, snapshot_index

    r, broker, _, price = observing_runtime(tmp_path)
    broker.ctx.update(historical_unsettled=[], incident_epoch_blocked=False, unknown_intents=False)
    assert r.tick()['status'] == 'wait'
    slot = int(r.test_clock[0] // 300)
    at = r.test_clock[0] + 7 * 86400 + 300
    selected = candidates(r.conn, at)['candidates']
    assert [row['slot'] for row in selected] == [slot]
    from utils.btc_offsite_archive import verify_retention_index
    manifest = {'started_at': at, 'btc_retention_index': snapshot_index(r.conn, at)}
    assert verify_retention_index(tmp_path / 'runtime.db', manifest) is True
    root = Path(__file__).resolve().parents[2]
    sources = [(name, (root / path).read_text()) for name, path in (
        ('btc_review_storage_embedded', 'observability/review_storage.py'),
        ('btc_review_event_refs_embedded', 'utils/btc_review_event_references.py'),
        ('btc_review_refs_embedded', 'utils/btc_review_references.py'),
        ('btc_retention_embedded', 'utils/btc_review_retention.py'),
        ('btc_recovery_refs_embedded', 'utils/btc_recovery_references.py'))]
    script = ("import sys,types,sqlite3,json\n"
              "for name,source in " + repr(sources) + ":\n"
              " m=types.ModuleType(name);exec(compile(source,name,'exec'),m.__dict__);sys.modules[name]=m\n"
              "c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)\n"
              "print(json.dumps(sys.modules['btc_retention_embedded'].candidates(c,float(sys.argv[2]))['candidates']))")
    child = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path / 'runtime.db'), str(at)],
                           capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(child.stdout) == selected
    from utils.btc_review_references import canonical, order_recipe, pack, sha, unpack
    original_body = r.conn.execute('SELECT body FROM llm_scenario_review_tapes WHERE slot=?', (slot,)).fetchone()[0]
    original = read_tape(original_body, r.conn, slot, decode_legacy=review_text)
    tape, bundle = json.loads(original), unpack(original_body)
    events = [op['event'] for op in tape['operations'] if op.get('kind') == 'record']
    for index, op in enumerate(tape['operations']):
        if op.get('kind') != 'generate':
            continue
        matches = [(i, event) for i, event in enumerate(events) if event.get('kind') == 'MODEL_RAW'
                   and event.get('actual_request') == op['request'] and event.get('raw') == op['text']]
        assert len(matches) == 1
        sequence, event = matches[0]
        bundle['tape']['operations'][index]['request'] = {'$btc_model_ref': {
            'sequence': sequence, 'order': order_recipe(op['request']),
            'request_sha256': sha(canonical(event['actual_request'])), 'response_sha256': sha(event['raw'])}}
        bundle['tape']['operations'][index]['text'] = {'$btc_model_ref': sequence}
    bundle['version'] = 2
    r.conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=?', (pack(bundle), slot))
    r.conn.commit()
    assert candidates(r.conn, at)['candidates'] == selected
    assert verify_retention_index(tmp_path / 'runtime.db', manifest) is True
    child = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path / 'runtime.db'), str(at)],
                           capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(child.stdout) == selected
    r.conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=?', (original_body, slot))
    r.conn.commit()
    from utils.btc_review_event_references import compact as compact_event
    from live.scenario_review_storage import review_text as decode_event
    r.conn.execute('SAVEPOINT referenced_review_event')
    rows = r.conn.execute('SELECT sequence,body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence', (slot,)).fetchall()
    prior = []
    changed = False
    for seq, body in rows:
        decoded = read_event(body, r.conn, slot, seq, decode_legacy=decode_event)
        if json.loads(decoded).get('kind') == 'MODEL_RAW':
            compacted = body if isinstance(body, bytes) and body.startswith(EVENT_REF_MAGIC) else compact_event(decoded, prior, slot, seq)
            if isinstance(compacted, bytes):
                r.conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=? AND sequence=?',
                               (compacted, slot, seq))
                changed = True
                break
        prior.append((seq, decoded))
    assert changed
    assert candidates(r.conn, at)['candidates'] == selected
    from live.scenario_review_storage import review_storage
    r.conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=?', (review_storage(original), slot))
    assert candidates(r.conn, at)['candidates'] == selected
    from utils.btc_review_event_references import _unpack as unpack_event
    source_seq = unpack_event(compacted)['source_sequence']
    r.conn.execute('SAVEPOINT changed_reference_source')
    r.conn.execute("UPDATE llm_scenario_review_events SET body='{}' WHERE slot=? AND sequence=?", (slot, source_seq))
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('ROLLBACK TO changed_reference_source')
    r.conn.execute('RELEASE changed_reference_source')
    r.conn.execute('RELEASE referenced_review_event')
    r.conn.commit()
    assert verify_retention_index(tmp_path / 'runtime.db', manifest) is True
    child = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path / 'runtime.db'), str(at)],
                           capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(child.stdout) == selected
    r.conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=? AND sequence=?', (body, slot, seq))
    r.conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=?', (original_body, slot))
    r.conn.commit()
    r.conn.execute('SAVEPOINT missing')
    r.conn.execute("DELETE FROM llm_scenario_recovery_events WHERE slot=? AND kind='MODEL_RAW'", (slot,))
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('ROLLBACK TO missing')
    r.conn.execute('RELEASE missing')
    r.conn.execute('SAVEPOINT changed')
    r.conn.execute("UPDATE llm_scenario_recovery_events SET body='{}' WHERE slot=? AND kind='DECISION'", (slot,))
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('ROLLBACK TO changed')
    r.conn.execute('RELEASE changed')
    r.conn.execute('SAVEPOINT extra')
    r.conn.execute("INSERT INTO llm_scenario_recovery_events VALUES('extra',?,NULL,'PERMIT_CONSUMED',1,'{}')", (slot,))
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('ROLLBACK TO extra')
    r.conn.execute('RELEASE extra')
    r.conn.execute('SAVEPOINT missing_review')
    original_body = r.conn.execute('SELECT body FROM llm_scenario_review_tapes WHERE slot=?', (slot,)).fetchone()[0]
    original = read_tape(original_body, r.conn, slot, decode_legacy=review_text)
    from live.scenario_review_storage import review_storage
    r.conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=?', (review_storage(original), slot))
    r.conn.execute('DELETE FROM llm_scenario_review_events WHERE slot=? AND sequence=2', (slot,))
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('ROLLBACK TO missing_review')
    r.conn.execute('RELEASE missing_review')
    r.conn.execute('SAVEPOINT missing_fresh')
    r.conn.execute('DELETE FROM llm_scenario_review_events WHERE slot=? AND sequence='
                   '(SELECT max(sequence) FROM llm_scenario_review_events WHERE slot=?)', (slot, slot))
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('ROLLBACK TO missing_fresh')
    r.conn.execute('RELEASE missing_fresh')
    r.conn.execute('SAVEPOINT corrupt_tape')
    r.conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=?', (b'PRISM_REVIEW_REFS_V1\x00bad', slot))
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('ROLLBACK TO corrupt_tape')
    r.conn.execute('RELEASE corrupt_tape')
    r.conn.execute('DROP INDEX llm_scenario_recovery_slot_kind')
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('CREATE INDEX llm_scenario_recovery_slot_kind ON llm_scenario_recovery_events(kind,slot)')
    assert not candidates(r.conn, at)['candidates']
    r.conn.execute('DROP INDEX llm_scenario_recovery_slot_kind')
    r.conn.execute('CREATE INDEX llm_scenario_recovery_slot_kind ON llm_scenario_recovery_events(slot,kind)')
    from utils.btc_recovery_references import read_recovery
    from utils.btc_review_retention import _delete_matched
    from observability.btc_scenario_export import _anchor_hash
    records = r.conn.execute('SELECT kind,body FROM llm_scenario_recovery_events WHERE slot=? ORDER BY rowid', (slot,)).fetchall()
    def restored():
        return [read_recovery(body, r.conn, slot, decode_legacy=review_text, expected_kind=kind)
                for kind, body in records]
    before = restored()
    anchor_slot, anchor_body, identity = slot + 100, '{}', 'a' * 64
    r.conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (anchor_slot, anchor_body))
    r.conn.commit()
    exporter = sqlite3.connect(':memory:')
    exporter.executescript('CREATE TABLE meta(key PRIMARY KEY,value); CREATE TABLE cursors(stream PRIMARY KEY,seq);'
                           'CREATE TABLE anchors(stream PRIMARY KEY,ref); CREATE TABLE outbox(id PRIMARY KEY,body);')
    exporter.execute('INSERT INTO meta VALUES(?,?)', ('source_identity', identity))
    exporter.execute('INSERT INTO cursors VALUES(?,?)', ('llm_scenario_review_tapes', anchor_slot))
    anchor = _anchor_hash('llm_scenario_review_tapes', {'slot': anchor_slot, 'body': anchor_body}, r.conn)
    exporter.execute('INSERT INTO anchors VALUES(?,?)', ('llm_scenario_review_tapes', anchor))
    exporter.commit()
    receipt = {'event_id': hashlib.sha256(f'{identity}:llm_scenario_review_tapes:{slot}'.encode()).hexdigest()[:32],
               'event_type': 'btc.research.review_tape', 'source_identity': identity,
               'body_sha256': selected[0]['tape_sha256'], 'tape_hash_status': 'MATCH'}
    with pytest.raises(ValueError, match='retention_guard_failed'):
        _delete_matched(r.conn, exporter, {'candidates': selected}, [receipt], identity, at,
                        guard=lambda: True, lease_alive=lambda: False)
    bad_receipt = dict(receipt, body_sha256='0' * 64)
    assert _delete_matched(r.conn, exporter, {'candidates': selected}, [bad_receipt], identity, at,
                           guard=lambda: True, lease_alive=lambda: True) == []
    raw_id, raw_body = r.conn.execute("SELECT event_id,body FROM llm_scenario_recovery_events "
                                      "WHERE slot=? AND kind='MODEL_RAW'", (slot,)).fetchone()
    r.conn.execute('UPDATE llm_scenario_recovery_events SET body=? WHERE event_id=?', ('{}', raw_id))
    r.conn.commit()
    assert _delete_matched(r.conn, exporter, {'candidates': selected}, [receipt], identity, at,
                           guard=lambda: True, lease_alive=lambda: True) == []
    r.conn.execute('UPDATE llm_scenario_recovery_events SET body=? WHERE event_id=?', (raw_body, raw_id))
    r.conn.commit()
    assert _delete_matched(r.conn, exporter, {'candidates': selected}, [receipt], identity, at,
                           guard=lambda: True, lease_alive=lambda: True) == [slot]
    assert r.conn.execute('SELECT slot FROM llm_scenario_review_tapes').fetchall() == [(anchor_slot,)]
    assert restored() == before
    r.test_clock[0] += 300
    broker.ctx['account_captured_at'] = r.clock()
    price[0] += 1
    assert r.tick()['status'] == 'intent_pending'


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


def test_recovery_two_call_wait_is_retained_only_with_both_model_phases(tmp_path):
    from utils.btc_review_retention import candidates

    r, broker, _, price = observing_runtime(tmp_path)
    broker.ctx.update(historical_unsettled=[], incident_epoch_blocked=False, unknown_intents=False)
    assert r.tick()['status'] == 'wait'
    r.test_clock[0] += 300
    broker.ctx['account_captured_at'] = r.clock()
    price[0] += 1
    captured = {}
    def generate(**request):
        schema = request['response_schema']['properties']
        if 'wait_proposal' in schema:
            result = {'wait_proposal': wire(captured, 'WAIT'), 'candidates': [wire(captured, 'OPEN')],
                      'no_candidate_reason': ''}
        else:
            token = next(x for x in schema['selection_token']['enum'] if x.startswith('WAIT:'))
            result = {'selection_token': token, 'rationale': '관망 선택'}
        return SimpleNamespace(text=json.dumps(result))
    propose = make_calculated_review_proposer(generate=generate, clock=r.clock)
    def invoke(snapshot, ctx, *, record):
        captured.update(deepcopy(ctx))
        return propose(snapshot, ctx, record=record)
    r.calculated_review_propose = invoke
    assert r.tick()['status'] == 'wait'
    assert not broker.executed
    slot = int(r.test_clock[0] // 300)
    at = r.test_clock[0] + 7 * 86400 + 300
    assert [item['slot'] for item in candidates(r.conn, at, after_slot=slot-1)['candidates']] == [slot]
    r.conn.execute("DELETE FROM llm_scenario_recovery_events WHERE event_id=(SELECT event_id FROM "
                   "llm_scenario_recovery_events WHERE slot=? AND kind='MODEL_RAW' LIMIT 1)", (slot,))
    assert not candidates(r.conn, at, after_slot=slot-1)['candidates']


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
