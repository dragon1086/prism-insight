import json

from tools import feature_status


def test_oneil_status_requires_policy_and_micro(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_status, "_ROOT", tmp_path)
    enabled = {"MICRO_SPLIT_SHADOW_ENABLED": "true"}
    assert feature_status._decide_oneil_watchlist_shadow(enabled, "")[0] == "OFF"
    path = tmp_path / "trading/config/oneil_watchlist_shadow.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mode": "SHADOW", "market": "US", "policy_version": "oneil_watchlist_v1", "enabled": True}))
    assert feature_status._decide_oneil_watchlist_shadow(enabled, "")[0] == "SHADOW"
    assert feature_status._decide_oneil_watchlist_shadow({}, "")[0] == "OFF"
    assert feature_status._decide_oneil_watchlist_shadow({**enabled, "ONEIL_WATCHLIST_SHADOW_ENABLED": "false"}, "")[0] == "OFF"
    path.write_text("broken")
    assert feature_status._decide_oneil_watchlist_shadow(enabled, "")[0] == "OFF"
