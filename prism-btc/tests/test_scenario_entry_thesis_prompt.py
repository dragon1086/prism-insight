"""Holding-review framing contract; these tests do not prove trading returns."""










def test_entry_helper_is_in_policy_provenance():
    from live import scenario_entry_thesis  # noqa: F401
    from live.scenario_provenance import _manifest
    manifest = _manifest()
    assert 'live/scenario_entry_thesis.py' in manifest['source_hashes']
