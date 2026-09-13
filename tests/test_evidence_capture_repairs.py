"""Evidence honesty/capture contracts; no models, orders or network calls."""
import ast
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from observability import entry_quality

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("language", ["ko", "en"])
def test_buy_prompt_reconciles_evidence_without_new_gate(market, language):
    path = ROOT / ("cores/agents/trading_agents.py" if market == "KR" else
                   "prism-us/cores/agents/trading_agents.py")
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    from prism_core.sector_names import KR_SECTOR_NAMES
    namespace = {"Agent": SimpleNamespace, "KR_SECTOR_NAMES": KR_SECTOR_NAMES,
                 "buy_scenario_prompt_contract": lambda _: ""}
    exec(compile(tree, str(path), "exec"), namespace)
    factory = "create_trading_scenario_agent" if market == "KR" else "create_us_trading_scenario_agent"
    prompt = namespace[factory](language).instruction
    if market == "US":
        expected = {
            "ko": ("d4a846c76a9f3651e98c0e91c68ef883079f64729406ac0ab51697a7f2d3b750",
                   "515130759f31ca1282749d6d3b2d10bc9704c69f86fc84484dd1a26c332ee646"),
            "en": ("d9485c9b269fa10acccca88a8f13e562f03f94209631677c0edd0e7d3a684ac5",
                   "c73c9066e6b9a043d70102cf1912fc6f1608fc544d6ef96e1555322e0aa6e031"),
        }
        tool_heading = "## 도구 사용" if language == "ko" else "## Tool Usage"
        json_heading = "## JSON 응답 형식" if language == "ko" else "## JSON Response Format"
        assert hashlib.sha256(prompt.split(tool_heading)[0].encode()).hexdigest() == expected[language][0]
        assert hashlib.sha256(prompt[prompt.index(json_heading):].encode()).hexdigest() == expected[language][1]
    for marker in ("EVIDENCE_RECONCILIATION", "INCOMPARABLE", "F4_business_clarity", "rationale"):
        assert marker in prompt
    for marker in (("시장 지배력", "새로운 진입 게이트", "반대 근거", "통과 근거") if language == "ko" else
                   ("market dominance", "new entry gate", "contrary evidence", "pass evidence")):
        assert marker in prompt


def test_kr_prior_uses_kr_tables_not_us(monkeypatch):
    calls = []
    monkeypatch.setattr(entry_quality, "_load_performance_feedback_module", lambda: SimpleNamespace(
        get_trigger_feedback=lambda cursor, market, trigger: calls.append((market, trigger)) or {}))
    result = entry_quality.build_entry_quality_context(
        market="KR", scenario={}, current_price=100, trigger_type="Volume Surge")
    assert calls == [("KR", "Volume Surge")]
    assert result["source"] == "existing_kr_scenario_and_local_feedback"
    assert result["extractor_version"] == "kr-local-facts-v1"
    assert result["trigger_prior"]["status"] == "MISSING"
    assert result["setup_quality"]["daily"]["status"] == "MISSING"
    assert result["event_risk"]["status"] == "MISSING"


def test_us_capture_metadata_is_backward_compatible():
    result = entry_quality.build_entry_quality_context(scenario={}, current_price=100)
    assert result["source"] == "existing_us_scenario_and_local_feedback"
    assert result["extractor_version"] == "us-local-facts-v1"


def _kr_capture_namespace():
    tree = ast.parse((ROOT / "stock_tracking_agent.py").read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "_capture_entry_quality_context")
    namespace = {"entry_quality_capture_enabled": entry_quality.capture_enabled,
                 "build_entry_quality_context": entry_quality.build_entry_quality_context,
                 "logger": SimpleNamespace(warning=lambda *args: None)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "capture", "exec"), namespace)
    return namespace


def test_kr_capture_fail_open_and_kill_switch(monkeypatch):
    namespace = _kr_capture_namespace()
    calls = []
    def build(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("capture failure")
    namespace["build_entry_quality_context"] = build
    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "0")
    kwargs = dict(cursor=None, scenario={}, current_price=100, trigger_type="test")
    assert namespace["_capture_entry_quality_context"](**kwargs) is None
    assert calls == []
    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "1")
    assert namespace["_capture_entry_quality_context"](**kwargs) is None
    assert calls[0]["market"] == "KR"


def test_kr_capture_emits_real_local_facts_and_keeps_missing_unknown(monkeypatch, tmp_path):
    from observability.trading_context import emit_trading_context

    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "1")
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    connection = sqlite3.connect(":memory:")
    cursor = connection.cursor()
    cursor.execute("CREATE TABLE analysis_performance_tracker (trigger_type TEXT, was_traded INTEGER, "
                   "tracked_7d_return REAL, tracked_14d_return REAL, tracked_30d_return REAL)")
    cursor.execute("INSERT INTO analysis_performance_tracker VALUES ('Volume Surge', 0, .01, .02, .03)")
    scenario = {"entry_checklist_passed": 5, "trading_scenarios": {"key_levels": {"primary_resistance": 110}}}
    original = json.dumps(scenario, sort_keys=True)
    context = _kr_capture_namespace()["_capture_entry_quality_context"](
        cursor=cursor, scenario=scenario, current_price=100, trigger_type="Volume Surge")
    assert context["trigger_prior"]["candidate"]["n"] == 1
    assert context["trigger_prior"]["actual"] is None
    assert context["setup_quality"]["entry_position"]["distances_from_entry_pct"] == {
        "primary_resistance_distance_pct": 10.0}
    assert context["setup_quality"]["structured_checks"]["momentum_signal_count"] is None
    assert context["status"] == "MISSING"
    assert json.dumps(scenario, sort_keys=True) == original
    emit_trading_context("candidate.evaluated", market="KR", ticker="012630",
                         decision_id="isolated-decision", scenario=scenario, entry_quality_context=context)
    event = json.loads(spool.read_text().splitlines()[0])
    assert event["attributes"]["entry_quality_context"]["trigger_prior"]["candidate"]["n"] == 1
    connection.close()


def test_both_kr_candidate_paths_attach_context():
    tree = ast.parse((ROOT / "stock_tracking_agent.py").read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and any(isinstance(arg, ast.Constant) and arg.value == "candidate.evaluated"
                     for arg in node.args)]
    assert len(calls) == 2
    for call in calls:
        context = next((kw.value for kw in call.keywords if kw.arg == "entry_quality_context"), None)
        assert isinstance(context, ast.Call)
        assert context.func.id == "_capture_entry_quality_context"


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_kr_capture_callsites_allow_agent_without_cursor(monkeypatch, enabled):
    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", enabled)
    tree = ast.parse((ROOT / "stock_tracking_agent.py").read_text())
    contexts = [kw.value for node in ast.walk(tree) if isinstance(node, ast.Call)
                for kw in node.keywords if kw.arg == "entry_quality_context"]
    assert len(contexts) == 2
    namespace = _kr_capture_namespace()
    namespace.update(self=SimpleNamespace(), scenario={}, current_price=100,
                     trigger_type=None, ticker="012630")
    for context in contexts:
        result = eval(compile(ast.Expression(body=context), "capture_callsite", "eval"), namespace)
        if enabled == "0":
            assert result is None
        else:
            assert result["status"] == "MISSING"
            assert result["trigger_prior"]["reason_code"] == "TRIGGER_TYPE_MISSING"
