import json

import pytest

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
    assert feature_status._decide_oneil_watchlist_shadow({**enabled, "ONEIL_WATCHLIST_SHADOW_ENABLED": "garbage"}, "")[0] == "OFF"
    assert feature_status._decide_oneil_watchlist_shadow({**enabled, "ONEIL_WATCHLIST_SHADOW_ENABLED": ""}, "")[0] == "OFF"
    path.write_text("broken")
    assert feature_status._decide_oneil_watchlist_shadow(enabled, "")[0] == "OFF"


def test_watch_runtime_override_matches_status(monkeypatch):
    from observability import oneil_watchlist
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "true")
    for value in ("false", " false ", "garbage", "0", ""):
        monkeypatch.setenv("ONEIL_WATCHLIST_SHADOW_ENABLED", value)
        assert not oneil_watchlist.enabled()


@pytest.mark.parametrize("override", [None, "true", " on ", "false", "garbage", "", "0"])
def test_kr_status_matches_runtime_without_us_micro(tmp_path, monkeypatch, override):
    from observability import micro_split, oneil_watchlist
    monkeypatch.setattr(feature_status, "_ROOT", tmp_path)
    policy = {"mode": "SHADOW", "market": "KR", "policy_version": "oneil_watchlist_kr_v1", "enabled": True}
    path = tmp_path / "trading/config/oneil_watchlist_kr_shadow.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(policy))
    monkeypatch.setattr(oneil_watchlist, "KR_POLICY_PATH", path)
    original = micro_split.Path.read_text
    monkeypatch.setattr(micro_split.Path, "read_text", lambda self, *a, **kw:
        json.dumps(policy) if self.name == "oneil_watchlist_kr_shadow.json" else original(self, *a, **kw))
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "false")
    env = {"MICRO_SPLIT_SHADOW_ENABLED": "false"}
    monkeypatch.delenv("ONEIL_WATCHLIST_SHADOW_ENABLED", raising=False)
    if override is not None:
        monkeypatch.setenv("ONEIL_WATCHLIST_SHADOW_ENABLED", override)
        env["ONEIL_WATCHLIST_SHADOW_ENABLED"] = override
    assert (feature_status._decide_oneil_watchlist_kr_shadow(env, "")[0] == "SHADOW") == oneil_watchlist.enabled("KR")
    if override is None:
        evidence = feature_status._decide_oneil_watchlist_kr_shadow(env, "")[1]
        assert "proxy" in evidence


def test_kr_status_requires_exact_policy_and_honors_empty_cron(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_status, "_ROOT", tmp_path)
    assert feature_status._decide_oneil_watchlist_kr_shadow({}, "")[0] == "OFF"
    path = tmp_path / "trading/config/oneil_watchlist_kr_shadow.json"
    path.parent.mkdir(parents=True)
    for policy in ({"mode": "LIVE"}, {"mode": "SHADOW", "market": "US"}, "broken"):
        path.write_text(json.dumps(policy))
        assert feature_status._decide_oneil_watchlist_kr_shadow({}, "")[0] == "OFF"
    path.write_text(json.dumps({"mode": "SHADOW", "market": "KR", "policy_version": "oneil_watchlist_kr_v1", "enabled": True}))
    assert feature_status._decide_oneil_watchlist_kr_shadow({}, "* * * * * ONEIL_WATCHLIST_SHADOW_ENABLED= runner")[0] == "OFF"
