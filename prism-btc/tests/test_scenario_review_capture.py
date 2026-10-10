"""Audit-only boundary capture: real runtime, fake broker, independent proof."""
import copy
import json
import sqlite3

import pytest

from analysis.scenario_proof import content_hash, verify_checkpoint_transition
from live import scenario_provenance as audit
from live.scenario_contract import identity_fields
from tests.test_scenario_runtime import setup as setup, proposal, wait_proposal


@pytest.fixture(autouse=True)
def isolated_audit(monkeypatch):
    monkeypatch.setattr(audit, '_manifest', lambda: {'fixture': 'offline-capture-tests'})
    monkeypatch.setattr('live.scenario_ledger.forward_audits', lambda events: None)


def review(*prices):
    return {'conditions': [{'source': 'MARK_PRICE', 'operator': 'ge', 'price': price}
                           for price in prices], 'acknowledgements': []}


def exact_proposal(snapshot, context, *, action='OPEN', metadata=None):
    value = proposal(snapshot, context) if action in ('OPEN', 'ADJUST') else wait_proposal(snapshot, context)
    value.update(identity_fields(context), action=action, review=metadata)
    if action == 'ADJUST':
        value.update(entries=[], hard_stop=95)
    return value


def run(runtime):
    with audit.run_capture(runtime.conn):
        return runtime.tick()


def events(runtime, kind):
    return [json.loads(row[0]) for row in runtime.conn.execute(
        'SELECT body FROM llm_scenario_audit_events WHERE kind=? ORDER BY rowid', (kind,))]


def held(setup, prices=(110,)):
    runtime, broker, now, path = setup
    runtime.propose = lambda s, c: exact_proposal(s, c, metadata=review(*prices))
    assert run(runtime)['status'] == 'intent_pending'
    ident = broker.executed[-1]
    broker.evidence = {'intents': [{'intent_id': ident, 'terminal': True, 'protection_ok': True,
        'orders_reconciled': True, 'executions_complete': True, 'execution_ids': ['fill'], 'filled_quantity': 1}]}
    broker.ctx.update(positions=[{'price': 100, 'quantity': 1}], account_captured_at=now[0])
    now[0] += 300
    return runtime, broker, now, path


def proof_packet(runtime, body):
    context, proposed = runtime.conn.execute(
        'SELECT context,proposal FROM llm_scenario_decisions WHERE slot=?', (body['decision_slot'],)).fetchone()
    context, proposed = json.loads(context), json.loads(proposed)
    model_input = {'contract_context': context}
    return {'version': 1, 'input': model_input, 'proposal': proposed,
            'binding': {'input_id': body['input_id'], 'scenario_id': body['scenario_id'],
                        'action_id': body['action_id'], 'input_sha256': content_hash(model_input),
                        'proposal_sha256': content_hash(proposed), 'source_revision': 'isolated-runtime-fixture',
                        'source_ids': {key: f'fixture:slot:{body["decision_slot"]}:{key}' for key in
                                       ('input', 'proposal', 'before', 'after', 'receipt', 'core_validation')}},
            'core_validated': body['commit_status'] == 'COMMITTED_DECISION_RECEIPT_MATCH',
            **{key: body[key] for key in ('before', 'presented', 'after', 'receipt', 'applied_at')}}


@pytest.mark.parametrize('action', ['WAIT', 'EXIT', 'ADJUST'])
def test_capture_matches_independent_proof_and_preserves_action(setup, action):
    runtime, broker, _, _ = held(setup)
    runtime.propose = lambda s, c: exact_proposal(s, c, action=action, metadata=review(120))
    previous = list(broker.executed)
    outcome = run(runtime)
    assert outcome['status'] == ('wait' if action == 'WAIT' else 'intent_pending')
    assert len(broker.executed) == len(previous) + (action != 'WAIT')
    records = events(runtime, 'review_transition')
    assert len(records) == 2
    for body in records:
        assert body['commit_scope'] == 'NOT_EXCHANGE_EXECUTION_OR_PROFIT_PROOF'
        assert verify_checkpoint_transition(proof_packet(runtime, body))['status'] == 'VERIFIED'
        assert body['receipt_hash'] == audit.digest(body['receipt'])


def test_capacity_and_fresh_hit_capture_presented_separately(setup):
    runtime, broker, now, _ = held(setup, (110, 120, 130))
    runtime.propose = lambda s, c: exact_proposal(s, c, action='WAIT', metadata=review(140))
    assert run(runtime)['status'] == 'wait'
    body = events(runtime, 'review_transition')[-1]
    assert body['receipt']['conditions'][0]['status'] == 'NOT_ADDED_CAPACITY'
    assert verify_checkpoint_transition(proof_packet(runtime, body))['status'] == 'VERIFIED'
    now[0] += 300
    def changed(snapshot, context):
        broker.ctx.update(mark_price=115, account_captured_at=now[0])
        value = review(140)
        value['acknowledgements'] = [{'id': context['review_memory'][0]['id'],
                                     'disposition': 'replace', 'reason': 'old condition'}]
        return exact_proposal(snapshot, context, action='WAIT', metadata=value)
    runtime.propose = changed
    assert run(runtime)['status'] == 'wait'
    body = events(runtime, 'review_transition')[-1]
    assert body['presented'][0]['reached_at'] is None
    assert body['before'][0]['reached_at'] == now[0]
    assert body['after'] == body['before']
    assert body['receipt']['status'] == 'new_hit_not_presented'
    assert verify_checkpoint_transition(proof_packet(runtime, body))['status'] == 'VERIFIED'


@pytest.mark.parametrize('action', ['EXIT', 'ADJUST'])
@pytest.mark.parametrize('failure', ['bad_metadata', 'oversize', 'begin_raise', 'finish_raise', 'none_token'])
def test_optional_audit_failures_cannot_block_protection(setup, monkeypatch, action, failure):
    runtime, broker, _, _ = held(setup)
    metadata = {'invalid': True} if failure == 'bad_metadata' else review(120)
    runtime.propose = lambda s, c: exact_proposal(s, c, action=action, metadata=metadata)
    def fail(*args, **kwargs):
        raise RuntimeError('optional audit only')
    if failure == 'oversize':
        monkeypatch.setattr(audit, 'MAX_REVIEW_BYTES', 1)
    elif failure == 'begin_raise':
        monkeypatch.setattr(audit, 'begin_review_capture', fail)
    elif failure == 'finish_raise':
        monkeypatch.setattr(audit, 'finish_review_capture', fail)
    elif failure == 'none_token':
        monkeypatch.setattr(audit, 'begin_review_capture', lambda *args, **kwargs: None)
    previous = len(broker.executed)
    assert run(runtime)['status'] == 'intent_pending'
    assert len(broker.executed) == previous + 1
    assert len(events(runtime, 'review_transition')) == 1
    if failure in ('bad_metadata', 'oversize'):
        assert events(runtime, 'review_transition_gap')


def test_failed_state_save_is_gap_not_committed_proof(setup, monkeypatch):
    runtime, _, _, _ = held(setup)
    old_state = runtime.state()
    original_save = runtime._save
    old_id = old_state['active']['review_update_receipt']['action_id']
    def failed_save(state):
        if state['active'].get('review_update_receipt', {}).get('action_id') != old_id:
            runtime.conn.execute('UPDATE llm_scenario_state SET body=? WHERE id=1', (json.dumps(state),))
            raise RuntimeError('before state commit')
        return original_save(state)
    monkeypatch.setattr(runtime, '_save', failed_save)
    runtime.propose = lambda s, c: exact_proposal(s, c, action='WAIT', metadata=review(120))
    assert run(runtime)['status'] == 'blocked'
    assert len(events(runtime, 'review_transition')) == 1
    assert events(runtime, 'review_transition_gap')[-1]['reason'] == 'COMMITTED_DECISION_RECEIPT_NOT_MATCHED'
    assert runtime.state()['active']['review_memory'] == old_state['active']['review_memory']


def test_failed_outcome_commit_is_gap_even_if_state_saved(setup):
    runtime, _, _, _ = held(setup)
    runtime.conn.execute("CREATE TRIGGER fail_outcome BEFORE UPDATE OF outcome ON llm_scenario_decisions "
                         "BEGIN SELECT RAISE(ABORT, 'test outcome failure'); END")
    runtime.conn.commit()
    runtime.propose = lambda s, c: exact_proposal(s, c, action='WAIT', metadata=review(120))
    with audit.run_capture(runtime.conn):
        with pytest.raises(sqlite3.IntegrityError):
            runtime.tick()
        runtime.conn.rollback()
    assert len(runtime.state()['active']['review_memory']) == 2
    assert len(events(runtime, 'review_transition')) == 1
    assert events(runtime, 'review_transition_gap')


def test_review_verification_exception_does_not_drop_settlement_audit(setup, monkeypatch):
    runtime, _, _, _ = held(setup)
    runtime.propose = lambda s, c: exact_proposal(s, c, action='WAIT', metadata=review(120))
    def fail(*args):
        raise RuntimeError('review verifier fault')
    monkeypatch.setattr(audit, '_review_commit_matches', fail)
    settlement = {'fixture': 'independently committed existing settlement'}
    with audit.run_capture(runtime.conn):
        assert runtime.tick()['status'] == 'wait'
        runtime.conn.execute('INSERT INTO llm_scenario_settlements VALUES (?,?)', ('old-scenario', json.dumps(settlement)))
        runtime.conn.commit()
        audit.record('settlement_recorded', {'settlement': settlement}, scenario_id='old-scenario')
    assert len(events(runtime, 'review_transition')) == 1
    assert len(events(runtime, 'settlement_recorded')) == 1
    assert events(runtime, 'review_transition_gap')


def test_committed_guard_rejects_foreign_original_proposal_and_mutated_receipt(setup):
    runtime, _, _, _ = held(setup)
    body = events(runtime, 'review_transition')[-1]
    assert audit._review_commit_matches(runtime.conn, body)
    for key, value in [('original_proposal_hash', 'foreign'), ('validated_payload_hash', 'foreign'),
                       ('input_id', 'foreign'), ('action_id', 'foreign')]:
        changed = dict(body, **{key: value})
        assert not audit._review_commit_matches(runtime.conn, changed)
    changed = copy.deepcopy(body)
    changed['receipt']['free_capacity'] = 3
    assert not audit._review_commit_matches(runtime.conn, changed)


def test_capture_has_no_model_context_or_broker_input_additions(setup):
    runtime, broker, _, _ = held(setup)
    captured = []
    def propose(snapshot, context):
        captured.append(copy.deepcopy(context))
        return exact_proposal(snapshot, context, action='EXIT', metadata=review(120))
    runtime.propose = propose
    original = broker.execute
    executed = []
    def execute(payload, ident):
        executed.append(copy.deepcopy(payload))
        original(payload, ident)
    broker.execute = execute
    assert run(runtime)['status'] == 'intent_pending'
    forbidden = {'before', 'after', 'presented', 'commit_status', 'original_proposal_hash', 'validated_payload_hash'}
    assert not forbidden.intersection(captured[0])
    assert not forbidden.intersection(executed[0])


@pytest.mark.parametrize('failure', ['stale', 'core_rejected'])
def test_unvalidated_proposals_never_capture_a_successful_transition(setup, failure):
    runtime, broker, _, _ = held(setup)
    previous = list(broker.executed)
    def proposed(snapshot, context):
        value = exact_proposal(snapshot, context, action='ADJUST', metadata=review(120))
        if failure == 'stale':
            broker.ctx['account_version'] = 'changed'
        else:
            value['hard_stop'] = 80  # Core rejects stop widening.
        return value
    runtime.propose = proposed
    outcome = run(runtime)
    assert outcome['status'] == ('stale_proposal' if failure == 'stale' else 'blocked')
    assert broker.executed == previous
    assert len(events(runtime, 'review_transition')) == 1


def test_new_audit_kinds_remain_outside_public_forward_allowlist():
    from live.scenario_ledger import FORWARDED_KINDS
    assert 'review_transition' not in FORWARDED_KINDS
    assert 'review_transition_gap' not in FORWARDED_KINDS
