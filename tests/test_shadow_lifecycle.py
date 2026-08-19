from datetime import date

from cores.shadow_lifecycle import apply_expiry, feature_mode, set_feature_mode


def test_shadow_expires_to_off_without_cron(tmp_path):
    state = tmp_path / "shadow.json"
    assert feature_mode("fill_chaser", now=date(2026, 9, 18), path=state) == "shadow"
    assert feature_mode("fill_chaser", now=date(2026, 9, 19), path=state) == "off"


def test_manual_live_survives_expiry(tmp_path):
    state = tmp_path / "shadow.json"
    set_feature_mode("fill_chaser", "live", reason="validated KIS canary", path=state)
    assert feature_mode("fill_chaser", now=date(2027, 1, 1), path=state) == "live"


def test_apply_expiry_materializes_off_and_reports_change(tmp_path):
    state = tmp_path / "shadow.json"
    result = apply_expiry(now=date(2026, 9, 19), path=state)
    assert "fill_chaser" in result["changed"]
    assert feature_mode("fill_chaser", now=date(2026, 9, 19), path=state) == "off"
