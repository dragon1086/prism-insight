from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.scenario_convergence import entry_convergence, require_entry_convergence
from core.llm_scenario import ScenarioValidationError, validate_scenario
from tests.test_llm_scenario import sample, active
from tests.test_scenario_review_runtime import runtime, wire


def snapshot(now=1800000000, h1=-1, m30=-1):
    frames = {}
    for tf, duration, change in [('1h', 3600000, h1), ('30m', 1800000, m30)]:
        close = int(now*1000//duration)*duration
        points = [dict(is_confirmed=True, open_time_ms=end-duration, close_time_ms=end,
                       ma10=ma, ma35=100) for end, ma in [(close-duration, 102), (close, 102+change)]]
        state = 'NARROWING' if change < 0 else 'WIDENING' if change > 0 else 'UNCHANGED'
        frames[tf] = dict(status='ok', forming=dict(observed_at_ms=now*1000), ma_path=dict(status='available',
            confirmed_points=points, confirmed_gap=dict(state=state), forming_gap=dict(state='WIDENING')))
    return dict(valid=True, as_of_ms=now*1000, timeframes=frames)


@pytest.mark.parametrize('h1', [-1, 0, 1])
@pytest.mark.parametrize('m30', [-1, 0, 1])
def test_only_both_confirmed_narrowing_restricts(h1, m30):
    value = entry_convergence(snapshot(h1=h1, m30=m30))
    assert value['status'] == ('BLOCKED' if h1 < 0 and m30 < 0 else 'ALLOWED')
    if value['status'] == 'ALLOWED':
        require_entry_convergence({'entry_convergence': value, 'now': 1800000000})
    else:
        with pytest.raises(ValueError, match='entry_convergence_restricted'):
            require_entry_convergence({'entry_convergence': value, 'now': 1800000000})


@pytest.mark.parametrize('damage', ['missing', 'future', 'gap', 'nan', 'label'])
def test_missing_stale_or_conflicting_evidence_is_unknown(damage):
    snap = snapshot(m30=1)
    path = snap['timeframes']['1h']['ma_path']
    if damage == 'missing': path['confirmed_points'] = []
    if damage == 'future': path['confirmed_points'][-1]['close_time_ms'] += 3600000
    if damage == 'gap': path['confirmed_points'][0]['close_time_ms'] -= 3600000
    if damage == 'nan': path['confirmed_points'][-1]['ma10'] = float('nan')
    if damage == 'label': path['confirmed_gap']['state'] = 'WIDENING'
    fact = entry_convergence(snap)
    assert fact['status'] == 'UNKNOWN'
    with pytest.raises(ValueError): require_entry_convergence({'entry_convergence': fact})


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_core_blocks_new_entry_on_both_converging_even_with_small_allocation(side):
    p, c = sample()
    p['side'] = side
    if side == 'SHORT':
        p.update(hard_stop=101000, take_profits=[dict(id='tp1', price=98000, fraction=.5)])
    c.update(now=1800000000, input_captured_at=1800000000)
    p['expires_at'] = 1800000090
    p['entries'][0]['quantity'] = .001
    c['entry_convergence'] = entry_convergence(snapshot())
    with pytest.raises(ScenarioValidationError, match='entry_convergence_restricted'):
        validate_scenario(p, c)
    c['entry_convergence'] = entry_convergence(snapshot(m30=1))
    assert validate_scenario(p, c)['action'] == 'OPEN'


def test_protection_without_new_entry_survives_convergence():
    p, c = active()
    c['entry_convergence'] = entry_convergence(snapshot())
    assert validate_scenario(p, c)['action'] == 'ADJUST'
    p['entries'] = [dict(id='add', price=101000, quantity=.001)]
    with pytest.raises(ScenarioValidationError, match='entry_convergence_restricted'):
        validate_scenario(p, c)


def test_renewal_cannot_bypass_entry_gate():
    p, c = active()
    p.update(action='WAIT', pending_renewal=dict(offer_id='test'))
    for key in ('side', 'hard_stop', 'chase'): p.pop(key, None)
    p['take_profits'] = []
    c.update(pending_renewal_version=1, entry_convergence=entry_convergence(snapshot()))
    with pytest.raises(ScenarioValidationError, match='entry_convergence_restricted'):
        validate_scenario(p, c)


@pytest.mark.parametrize('mode', ['legacy', 'economic-reject-calculated-v1'])
@pytest.mark.parametrize('m30,expected', [(-1, 'wait'), (1, 'intent_pending')])
def test_actual_review_rejects_entry_but_preserves_early_30m_expansion(tmp_path, m30, expected, mode):
    from live.scenario_review_adapter import make_calculated_review_proposer
    r, broker, _ = runtime(tmp_path)
    r.convergence_entry_enabled = True
    r.snapshot = lambda: snapshot(r.test_clock[0], m30=m30)
    r.research_review_mode = mode
    seen = []
    def generate(**request):
        data = json.loads(request['user_prompt']);ctx = data['contract_context']
        seen.append(request)
        if 'selection_token' in request['response_schema']['properties']:
            candidates = data['calculated_candidates']
            chosen = next((x for x in candidates['candidates'] if x['eligible']), candidates['wait'])
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale='fixture')
        else:
            plan = wire(ctx, 'OPEN')
            if mode != 'legacy':
                plan['entries'][0].pop('quantity')
                plan['entries'][0]['allocation_fraction'] = .01
            result = dict(wait_proposal=wire(ctx, 'WAIT'), candidates=[plan], no_candidate_reason='')
        return SimpleNamespace(text=json.dumps(result))
    r.calculated_review_propose = make_calculated_review_proposer(generate=generate, clock=lambda:r.test_clock[0], research_mode=mode)
    outcome = r.tick()
    assert outcome['status'] == expected, outcome
    assert len(broker.executed) == (1 if m30 == 1 else 0)
    assert all('one gap widening or unchanged' in x['system_prompt'] for x in seen)


def test_pending_entries_cancel_without_calling_model(tmp_path):
    r, broker, calls = runtime(tmp_path)
    state = r.state()
    state['active'] = dict(scenario_id='held', initial_equity=10000, side='LONG', hard_stop=90,
        revision=0, created_at=r.test_clock[0]-60, expires_at=r.test_clock[0]+600)
    r._save(state)
    broker.ctx.update(pending_entries=[dict(id='old', price=100, quantity=1)], previous_hard_stop=90)
    r.convergence_entry_enabled = True
    r.snapshot = lambda: snapshot(r.test_clock[0])
    result = r.tick()
    assert result['status'] == 'intent_pending', result
    assert calls == []
    assert broker.executed[0]['action'] == 'WAIT'
    assert broker.executed[0]['cancel_entry_ids'] == ['old']
    assert r.state()['active']['hard_stop'] == 90
    assert r.conn.execute("SELECT status FROM llm_scenario_intents").fetchall() == [('PENDING',)]


def test_executor_uses_original_snapshot_not_forged_allowed_context(tmp_path):
    from live.scenario_execution import ScenarioExecution
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE llm_scenario_decisions(snapshot TEXT,context TEXT,proposal TEXT)')
    snap = snapshot()
    ctx = dict(input_id='i', now=1800000000, entry_convergence=entry_convergence(snap))
    c.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)',
        (json.dumps(snap), json.dumps(ctx), json.dumps(dict(action_id='a'))))
    executor = ScenarioExecution();executor.conn=c;executor.convergence_entry_enabled=True;executor.clock=lambda:1800000000
    with pytest.raises(ValueError, match='entry_convergence_restricted'):
        executor._check_entry_convergence(dict(action_id='a', input_id='i'))
    from live.scenario_review_storage import review_storage
    c.execute('UPDATE llm_scenario_decisions SET snapshot=?', (review_storage(json.dumps(snap)),))
    with pytest.raises(ValueError, match='entry_convergence_restricted'):
        executor._check_entry_convergence(dict(action_id='a', input_id='i'))
    ctx['entry_convergence']['status'] = 'ALLOWED'
    c.execute('UPDATE llm_scenario_decisions SET context=?', (json.dumps(ctx),))
    with pytest.raises(Exception, match='entry_convergence_evidence_changed'):
        executor._check_entry_convergence(dict(action_id='a', input_id='i'))


@pytest.mark.parametrize('elapsed', [-1, 121, 1800])
def test_entry_gate_never_accepts_future_stale_or_previous_boundary_evidence(elapsed):
    now = 1800000000
    fact = entry_convergence(snapshot(now, m30=1))
    with pytest.raises(ValueError, match='entry_convergence_stale'):
        require_entry_convergence(dict(entry_convergence=fact, now=now+elapsed))


def test_new_half_hour_requires_new_confirmed_evidence_even_within120seconds():
    boundary = 1800000000
    before = boundary-1
    fact = entry_convergence(snapshot(before, m30=1))
    assert fact['status'] == 'ALLOWED'
    with pytest.raises(ValueError, match='entry_convergence_stale'):
        require_entry_convergence(dict(entry_convergence=fact, now=boundary))


@pytest.mark.parametrize('held', [False, True])
def test_native_pending_order_is_cancelled_without_resetting_state_or_replaying_http(tmp_path, held):
    from analysis.scenario_replay import run_replay, network_boundary
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_replay import inputs
    from tests.test_scenario_calculated_replay import wire as replay_wire
    bundle, market = inputs(900000)
    original = market.snapshot
    def market_snapshot(at):
        value = original(at)
        controlled = snapshot(at/1000, m30=1 if at < bundle['start_ms']+300000 else -1)
        for tf in ('1h', '30m'):
            value['timeframes'][tf]['ma_path'] = controlled['timeframes'][tf]['ma_path']
        return value
    market.snapshot = market_snapshot
    def generate(**request):
        data = restore_model_input(json.loads(request['user_prompt']));ctx = data['contract_context']
        if 'selection_token' in request['response_schema']['properties']:
            choices = data['calculated_candidates']
            selected = next((x for x in choices['candidates'] if x['eligible']), choices['wait'])
            result = dict(selection_token=f"{selected['id']}:{selected['receipt_sha256']}", rationale='fixture')
        else:
            wait = replay_wire(ctx, 'WAIT')
            wait.update(entry_contract=None, thesis_assessment=None)
            if ctx.get('pending_renewal_version') == 1: wait['pending_renewal'] = None
            if ctx.get('scenario_id'):
                wait['thesis_assessment'] = dict(contract_id=ctx['registered_entry_contract']['contract_id'], status='INTACT', reason='fixture')
            candidates = []
            if ctx['now'] < bundle['start_ms']/1000+300:
                plan = replay_wire(ctx, 'OPEN')
                plan.update(expires_at=ctx['now']+1200, hard_stop=94,
                    thesis_assessment=None, entry_contract=dict(version=2, regime='TREND', premise='fixture',
                        goal=dict(mode='NO_MA_BOUND', reference=None),
                        explicit_failure=dict(source='MARK_PRICE',operator='le',price=94.5,minimum_observations=2)))
                plan['entries'] = [dict(id='resting', price=95, trigger_price=None, allocation_fraction=.01)]
                if held:
                    plan['hard_stop'] = 98
                    plan['entry_contract']['explicit_failure']['price'] = 98.4
                    plan['entries'] = [dict(id='filled', price=100.1, trigger_price=None, allocation_fraction=.005),
                                       dict(id='resting', price=98.5, trigger_price=None, allocation_fraction=.005)]
                candidates = [plan]
            result = dict(wait_proposal=wait, candidates=candidates, no_candidate_reason='' if candidates else 'fixture')
        return SimpleNamespace(text=json.dumps(result))
    with network_boundary('frozen') as attempts:
        fresh = run_replay(bundle, market, tmp_path/'fresh', mode='fixture', tape_path=tmp_path/'tape',
            policy_profile='economic-reject-goal-v2', model_generator=generate, clock_monotonic=lambda:0.)
        frozen = run_replay(bundle, market, tmp_path/'frozen', mode='frozen', tape_path=tmp_path/'tape',
            policy_profile='economic-reject-goal-v2')
    assert not attempts and fresh['execution_status'] == 'COMPLETE', fresh.get('failure_evidence')
    assert fresh['result_hash'] == frozen['result_hash']
    with sqlite3.connect(tmp_path/'fresh/replay.sqlite') as c:
        rows = c.execute('SELECT slot,proposal,outcome FROM llm_scenario_decisions ORDER BY slot').fetchall()
        cancelled = json.loads(rows[1][1]);outcome = json.loads(rows[1][2])
        assert cancelled['action'] == 'WAIT' and cancelled['cancel_entry_ids'] == ['resting']
        assert outcome['reason'] == 'awaiting_cancellation' and outcome['model_called'] is False
        assert c.execute('SELECT count(*) FROM llm_scenario_review_tapes WHERE slot=?', (rows[1][0],)).fetchone()[0] == 0
        assert c.execute("SELECT kind,status FROM llm_scenario_children WHERE kind='entry'").fetchall() == [('entry','TERMINAL')]*(2 if held else 1)
        assert c.execute("SELECT count(*) FROM llm_scenario_children WHERE kind IN ('exit','reduce')").fetchone()[0] == 0
        if held:
            state = json.loads(c.execute('SELECT body FROM llm_scenario_state').fetchone()[0])
            assert state['active']['hard_stop'] == 98
    assert (fresh['economic']['open_quantity'] > 0) is held
    assert fresh['economic']['pending_intents'] == 0


@pytest.mark.parametrize('m30,expected', [(-1, 'BLOCKED'), (1, 'ALLOWED')])
def test_bearish_ma_order_uses_absolute_gap_not_signed_difference(m30, expected):
    value = snapshot(m30=m30)
    for frame in value['timeframes'].values():
        for point in frame['ma_path']['confirmed_points']:
            point['ma10'] = 200-point['ma10']
    assert entry_convergence(value)['status'] == expected


def test_decision_identity_lookup_uses_index(tmp_path):
    r, _, _ = runtime(tmp_path, empty=True)
    plan = r.conn.execute("EXPLAIN QUERY PLAN SELECT snapshot,context FROM llm_scenario_decisions WHERE json_extract(proposal,'$.action_id')=?", ('a',)).fetchall()
    assert any('llm_scenario_decisions_action_id' in str(row) for row in plan)


def test_unknown_is_not_reported_as_confirmed_convergence():
    with pytest.raises(ValueError, match='entry_convergence_unknown'):
        require_entry_convergence(dict(entry_convergence=entry_convergence({}), now=1800000000))



def test_final_pure_validation_cannot_carry_entry_permission_across_half_hour(tmp_path, monkeypatch):
    import live.scenario_review_guard as module
    r, broker, _ = runtime(tmp_path)
    r.test_clock[0] -= 10
    broker.ctx['account_captured_at'] = r.test_clock[0]
    r.convergence_entry_enabled = True
    r.snapshot = lambda: snapshot(r.test_clock[0], m30=1)
    original = module.deepcopy
    seen = []
    def slow_copy(value):
        result = original(value)
        if isinstance(value, dict) and value.get('input_id') and 'entry_convergence' in value:
            seen.append(1)
            if len(seen) == 3:
                r.test_clock[0] += 11
        return result
    monkeypatch.setattr(module, 'deepcopy', slow_copy)
    result = r.tick()
    assert result['status'] == 'stale_proposal', result
    assert result['reason'] == 'entry_convergence_stale'
    assert not broker.executed
    assert r.conn.execute('SELECT count(*) FROM llm_scenario_intents').fetchone()[0] == 0
