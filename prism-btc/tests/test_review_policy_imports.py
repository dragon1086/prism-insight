"""The opt-in policy loader has a literal, closed import surface."""
import importlib

from live.scenario_provenance import review_policy_binding


def test_review_binding_does_not_dispatch_module_names(monkeypatch):
    before = review_policy_binding()  # Warm ordinary dependencies first.
    assert len(before['source_sha256']) == 32
    assert 'utils/btc_review_references.py' in before['source_sha256']
    assert 'live/scenario_review_storage.py' in before['source_sha256']
    assert 'live/scenario_oauth.py' in before['source_sha256']
    assert 'live/scenario_incident_epoch.py' in before['source_sha256']
    assert 'live/scenario_management_quarantine.py' in before['source_sha256']
    assert 'live/scenario_analysis_frames.py' in before['source_sha256']
    assert 'live/scenario_entry_goal.py' in before['source_sha256']
    assert 'engine/scenario_snapshot.py' in before['source_sha256']
    assert 'core/scenario_convergence.py' in before['source_sha256']
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


def test_reference_decoder_is_bound_and_tampering_is_rejected(monkeypatch):
    import pytest
    from live import scenario_provenance
    from utils import btc_review_references

    before = review_policy_binding()
    assert before['loaded_parity']['utils/btc_review_references.py'] == 'VERIFIED'
    manifest = scenario_provenance._manifest()
    assert manifest['execution_loaded_code_checks']['utils/btc_review_references.py'] == 'VERIFIED'
    monkeypatch.setattr(btc_review_references, 'MAGIC', b'wrong')
    with pytest.raises(ValueError, match='review_mixed_loaded_policy'):
        review_policy_binding()
    assert scenario_provenance._manifest()['execution_loaded_code_checks']['utils/btc_review_references.py'] == 'MIXED'
