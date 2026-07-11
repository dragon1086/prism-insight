"""
Tests for tools/feature_status.py — mock-only, no real crontab or .env reads.

Cross-reference: docs/FEATURE_FLAGS.md (intended state registry)
"""
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import tools.feature_status as fs  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _results_by_id(results):
    return {r["id"]: r for r in results}


CRON_WITH_ALL = """
# system crontab
*/10 * * * * /usr/bin/python tools/loop_a_hardstop.py
0 18 * * * /usr/bin/python tools/loop_b_trend_exit.py
*/5 * * * * /usr/bin/python tools/loop_c_fill_chaser.py
"""

CRON_LOOP_A_ONLY = """
*/10 * * * * /usr/bin/python tools/loop_a_hardstop.py
# 0 18 * * * /usr/bin/python tools/loop_b_trend_exit.py   (commented out)
"""

# Crontab with PRISM_OPENAI_AUTH_MODE set inline on a batch line (no .env)
CRON_WITH_OAUTH_INLINE = """
# system crontab
*/10 * * * * /usr/bin/python tools/loop_a_hardstop.py
30 09 * * 1-5 cd /opt/prism && PRISM_OPENAI_AUTH_MODE=chatgpt_oauth /usr/bin/python stock_analysis_orchestrator.py
"""

# Crontab with OAuth inline but line is commented out — must NOT count
CRON_OAUTH_COMMENTED = """
# 30 09 * * 1-5 PRISM_OPENAI_AUTH_MODE=chatgpt_oauth /usr/bin/python stock_analysis_orchestrator.py
*/10 * * * * /usr/bin/python tools/loop_a_hardstop.py
"""

# New descriptive script names — the loops were renamed
# (loop_a_hardstop->hardstop_seller, loop_b_trend_exit->trend_exit_seller,
# loop_c_fill_chaser->fill_chaser). A cron line with EITHER the old or the new
# filename must count as scheduled.
CRON_WITH_NEW_NAMES = """
# system crontab (post-rename)
*/10 * * * * /usr/bin/python tools/hardstop_seller.py
0 18 * * * /usr/bin/python tools/trend_exit_seller.py
*/5 * * * * /usr/bin/python tools/fill_chaser.py
"""

CRON_EMPTY = ""


# ── OAuth LLM backend ─────────────────────────────────────────────────────────

def test_oauth_llm_live():
    env = {"PRISM_OPENAI_AUTH_MODE": "chatgpt_oauth"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["oauth_llm"]["state"] == "LIVE"
    assert "chatgpt_oauth" in r["oauth_llm"]["evidence"]


def test_oauth_llm_off_api_key():
    env = {"PRISM_OPENAI_AUTH_MODE": "api_key"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["oauth_llm"]["state"] == "OFF"


def test_oauth_llm_off_unset():
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_EMPTY))
    assert r["oauth_llm"]["state"] == "OFF"


def test_oauth_llm_live_from_crontab_inline():
    """PRISM_OPENAI_AUTH_MODE absent from env but present inline in crontab → LIVE."""
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_WITH_OAUTH_INLINE))
    assert r["oauth_llm"]["state"] == "LIVE"
    assert "crontab inline" in r["oauth_llm"]["evidence"]


def test_oauth_llm_not_live_when_commented_in_crontab():
    """Commented-out crontab line must not count as OAuth evidence → OFF."""
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_OAUTH_COMMENTED))
    assert r["oauth_llm"]["state"] == "OFF"


# ── Loop A ────────────────────────────────────────────────────────────────────

def test_loop_a_live_env_and_cron():
    env = {"LOOP_A_LIVE": "true"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_LOOP_A_ONLY))
    assert r["loop_a"]["state"] == "LIVE"
    assert "cron=있음" in r["loop_a"]["evidence"]


def test_loop_a_misscheduled_env_live_no_cron():
    env = {"LOOP_A_LIVE": "true"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["loop_a"]["state"] == "미스케줄"


def test_loop_a_shadow_cron_but_no_live_flag():
    env = {}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_LOOP_A_ONLY))
    assert r["loop_a"]["state"] == "SHADOW"


def test_loop_a_off_kill_switch():
    env = {"LOOP_A_LIVE": "true", "LOOP_A_ENABLED": "false"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_WITH_ALL))
    assert r["loop_a"]["state"] == "OFF"
    assert "킬스위치" in r["loop_a"]["evidence"]


def test_loop_a_off_no_env_no_cron():
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_EMPTY))
    assert r["loop_a"]["state"] == "OFF"


# ── Loop B ────────────────────────────────────────────────────────────────────

def test_loop_b_misscheduled_no_cron():
    """Default registry state: cron 없음 → 미스케줄 regardless of env."""
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_EMPTY))
    assert r["loop_b"]["state"] == "미스케줄"


def test_loop_b_live_when_env_and_cron():
    env = {"LOOP_B_LIVE": "true"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_WITH_ALL))
    assert r["loop_b"]["state"] == "LIVE"


def test_loop_b_shadow_cron_no_live_flag():
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_WITH_ALL))
    assert r["loop_b"]["state"] == "SHADOW"


def test_loop_b_off_disabled():
    env = {"LOOP_B_ENABLED": "false"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_WITH_ALL))
    assert r["loop_b"]["state"] == "OFF"


# ── Loop C ────────────────────────────────────────────────────────────────────

def test_loop_c_misscheduled_no_cron():
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_EMPTY))
    assert r["loop_c"]["state"] == "미스케줄"


def test_loop_c_live_when_env_and_cron():
    env = {"LOOP_C_LIVE": "true"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_WITH_ALL))
    assert r["loop_c"]["state"] == "LIVE"


def test_loop_c_shadow_cron_no_live_flag():
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_WITH_ALL))
    assert r["loop_c"]["state"] == "SHADOW"


def test_loop_c_off_disabled():
    env = {"LOOP_C_ENABLED": "false"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_WITH_ALL))
    assert r["loop_c"]["state"] == "OFF"


# ── Vision pipeline (S1/S2) ───────────────────────────────────────────────────

def test_vision_pipeline_live_on_no_shadow():
    env = {"PRISM_FEATURE_VISION": "on"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["vision_pipeline"]["state"] == "LIVE"


def test_vision_pipeline_shadow_when_shadow_true():
    env = {"PRISM_FEATURE_VISION": "on", "PRISM_VISION_SHADOW": "true"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["vision_pipeline"]["state"] == "SHADOW"


def test_vision_pipeline_off_unset():
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_EMPTY))
    assert r["vision_pipeline"]["state"] == "OFF"


# ── Vision buy QA (S3/S3.5) ───────────────────────────────────────────────────

def test_vision_buy_qa_shadow_on_plus_shadow_true():
    env = {"PRISM_FEATURE_VISION": "on", "PRISM_VISION_SHADOW": "true"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["vision_buy_qa"]["state"] == "SHADOW"


def test_vision_buy_qa_live_on_no_shadow():
    env = {"PRISM_FEATURE_VISION": "on"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["vision_buy_qa"]["state"] == "LIVE"


def test_vision_buy_qa_off_vision_unset():
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_EMPTY))
    assert r["vision_buy_qa"]["state"] == "OFF"


# ── Vision publish (S6) ───────────────────────────────────────────────────────

def test_vision_publish_off_when_insight_image_unset(monkeypatch):
    """S6 broadcast OFF when PRISM_FEATURE_INSIGHT_IMAGE is unset, even if vision available."""
    monkeypatch.setattr(fs, "_vision_available", lambda env: True)
    r = _results_by_id(fs.evaluate_all(env={"PRISM_FEATURE_VISION": "on"}, crontab=CRON_EMPTY))
    assert r["vision_publish"]["state"] == "OFF"
    assert "PRISM_FEATURE_INSIGHT_IMAGE" in r["vision_publish"]["evidence"]


def test_vision_publish_off_when_vision_unavailable(monkeypatch):
    """S6 broadcast OFF when image flag is on but vision is not available (no key / vision off)."""
    monkeypatch.setattr(fs, "_vision_available", lambda env: False)
    env = {"PRISM_FEATURE_INSIGHT_IMAGE": "on"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["vision_publish"]["state"] == "OFF"
    assert "미가용" in r["vision_publish"]["evidence"]


def test_vision_publish_live_when_image_flag_and_vision_available(monkeypatch):
    """S6 broadcast LIVE only when PRISM_FEATURE_INSIGHT_IMAGE=on AND vision available."""
    monkeypatch.setattr(fs, "_vision_available", lambda env: True)
    env = {"PRISM_FEATURE_INSIGHT_IMAGE": "on"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_EMPTY))
    assert r["vision_publish"]["state"] == "LIVE"
    # No stale "미구현" claim.
    assert "미구현" not in r["vision_publish"]["evidence"]


# ── --json output ─────────────────────────────────────────────────────────────

def test_json_output_contains_all_features(capsys):
    env = {
        "PRISM_OPENAI_AUTH_MODE": "chatgpt_oauth",
        "LOOP_A_LIVE": "true",
        "PRISM_FEATURE_VISION": "on",
        "PRISM_VISION_SHADOW": "true",
    }
    results = fs.evaluate_all(env=env, crontab=CRON_LOOP_A_ONLY)
    # Simulate --json path
    out = {r["id"]: {"state": r["state"], "evidence": r["evidence"]} for r in results}
    data = json.loads(json.dumps(out, ensure_ascii=False))

    expected_ids = {"oauth_llm", "loop_a", "loop_b", "loop_c",
                    "vision_pipeline", "vision_buy_qa", "vision_publish"}
    assert expected_ids == set(data.keys())
    assert data["oauth_llm"]["state"] == "LIVE"
    assert data["loop_a"]["state"] == "LIVE"
    assert data["vision_pipeline"]["state"] == "SHADOW"


# ── Robustness: missing env / bad crontab ─────────────────────────────────────

def test_empty_env_and_crontab_does_not_raise():
    """evaluate_all must never raise even with completely empty inputs."""
    results = fs.evaluate_all(env={}, crontab="")
    assert len(results) == 7  # one entry per feature


def test_cron_commented_line_not_counted():
    cron = "# */10 * * * * /usr/bin/python tools/loop_a_hardstop.py\n"
    assert not fs._cron_has_script(cron, "loop_a_hardstop.py")


def test_cron_active_line_counted():
    cron = "*/10 * * * * /usr/bin/python tools/loop_a_hardstop.py\n"
    assert fs._cron_has_script(cron, "loop_a_hardstop.py")


# ── Rename compat: new descriptive script names must also count as scheduled ────

def test_loops_detected_with_new_script_names():
    """A crontab using the renamed scripts (hardstop_seller / trend_exit_seller /
    fill_chaser) must resolve LIVE exactly like the old loop_* names."""
    env = {"HARDSTOP_LIVE": "true", "TREND_EXIT_LIVE": "true", "FILL_CHASER_LIVE": "true"}
    r = _results_by_id(fs.evaluate_all(env=env, crontab=CRON_WITH_NEW_NAMES))
    assert r["loop_a"]["state"] == "LIVE"
    assert r["loop_b"]["state"] == "LIVE"
    assert r["loop_c"]["state"] == "LIVE"


def test_cron_new_filename_counted_for_each_loop():
    assert fs._cron_has_script(CRON_WITH_NEW_NAMES, "hardstop_seller.py")
    assert fs._cron_has_script(CRON_WITH_NEW_NAMES, "trend_exit_seller.py")
    assert fs._cron_has_script(CRON_WITH_NEW_NAMES, "fill_chaser.py")


def test_labels_are_descriptive_first_with_legacy_marker():
    """Display labels lead with the descriptive name and keep a (구 Loop X) marker."""
    r = _results_by_id(fs.evaluate_all(env={}, crontab=CRON_EMPTY))
    assert r["loop_a"]["label"].startswith("Hardstop") and "구 Loop A" in r["loop_a"]["label"]
    assert r["loop_b"]["label"].startswith("Trend-exit") and "구 Loop B" in r["loop_b"]["label"]
    assert r["loop_c"]["label"].startswith("Fill-chaser") and "구 Loop C" in r["loop_c"]["label"]
