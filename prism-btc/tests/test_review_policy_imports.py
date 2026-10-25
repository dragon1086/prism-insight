"""The opt-in policy loader has a literal, closed import surface."""
import importlib

from live.scenario_provenance import review_policy_binding


def test_review_binding_does_not_dispatch_module_names(monkeypatch):
    before = review_policy_binding()  # Warm ordinary dependencies first.
    assert len(before['source_sha256']) == 28
    assert 'live/scenario_incident_epoch.py' in before['source_sha256']
    assert 'live/scenario_management_quarantine.py' in before['source_sha256']
    assert 'live/scenario_analysis_frames.py' in before['source_sha256']
    assert 'live/scenario_entry_goal.py' in before['source_sha256']
    assert 'engine/scenario_snapshot.py' in before['source_sha256']
    assert 'core/scenario_pending_renewal.py' in before['source_sha256']
    assert 'live/scenario_pending_renewal.py' in before['source_sha256']
    assert 'live/scenario_entry_contract.py' in before['source_sha256']
    assert 'live/scenario_position_flow.py' in before['source_sha256']
    assert 'live/scenario_immediate_reduce.py' in before['source_sha256']
    assert 'live/scenario_explanation.py' in before['source_sha256']
    assert 'live/scenario_fee_projection.py' in before['source_sha256']
    assert set(before['loaded_parity'].values()) == {'VERIFIED'}

    def forbidden(*_args, **_kwargs):
        raise AssertionError('dynamic module dispatch is forbidden')

    monkeypatch.setattr(importlib, 'import_module', forbidden)
    assert review_policy_binding() == before
