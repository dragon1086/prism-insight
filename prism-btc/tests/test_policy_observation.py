import json
import sys
from types import SimpleNamespace

from live import runner, tracking


def test_observation_is_sanitized_and_change_only(tmp_path, monkeypatch):
    conn = tracking.get_connection(tmp_path / 'root.sqlite')
    tracking.ensure_schema(conn)
    state = {'state': 'active', 'source': 'runtime', 'policy_id': 'trial-1',
             'heat': .065, 'slippage': .001, 'main_uid': 'PRIVATE_UID'}
    monkeypatch.setitem(sys.modules, 'live.shared_entry_policy',
                        SimpleNamespace(policy_status=lambda _: state))
    try:
        first = runner._observe_shared_entry_policy(conn, 'demo')
        runner._observe_shared_entry_policy(conn, 'demo')
        saved = tracking.get_meta(conn, 'shared_entry_policy_observed_v1', 'demo')
        assert saved['state'] == 'active' and saved['heat'] == .065
        assert saved['slippage'] == .001 and saved['observed_at']
        assert 'PRIVATE_UID' not in json.dumps(first) + json.dumps(saved)
        assert conn.execute("SELECT count(*) FROM btc_events WHERE kind='shared_policy'").fetchone()[0] == 1
    finally:
        conn.close()


def test_policy_observer_error_does_not_skip_broker_protection(tmp_path, monkeypatch):
    path = tmp_path / 'root.sqlite'
    def broken(_):
        raise ValueError('PRIVATE_UID_TOKEN')
    monkeypatch.setitem(sys.modules, 'live.shared_entry_policy', SimpleNamespace(policy_status=broken))
    calls = []
    monkeypatch.setattr(runner, '_record_code_version', lambda *_: None)
    monkeypatch.setattr(runner, '_broker_recovery', lambda *_: calls.append('protection') or [])
    monkeypatch.setattr(runner, '_tick_inner', lambda conn, mode, market, result: result)
    result = runner.tick('demo', root_db_path=path)
    assert calls == ['protection']
    assert result['shared_entry_policy']['state'] == 'error'
    assert 'PRIVATE_UID_TOKEN' not in json.dumps(result)


def test_shadow_does_not_read_or_store_demo_policy(tmp_path, monkeypatch):
    path = tmp_path / 'root.sqlite'
    def forbidden(_):
        raise AssertionError('shadow must not inspect private demo policy')
    monkeypatch.setitem(sys.modules, 'live.shared_entry_policy', SimpleNamespace(policy_status=forbidden))
    monkeypatch.setattr(runner, '_record_code_version', lambda *_: None)
    monkeypatch.setattr(runner, '_broker_recovery', lambda *_: [])
    monkeypatch.setattr(runner, '_tick_inner', lambda conn, mode, market, result: result)
    result = runner.tick('shadow', root_db_path=path)
    assert 'shared_entry_policy' not in result
