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
        # Reverse only reviewed analyst/finality additions, then retain the
        # original legacy-rule and JSON hashes below. Literal snapshots ensure
        # an unrelated future appendix change cannot silently bypass this guard.
        analyst_appendix = '''
## 애널리스트 자료의 해석 경계
- 보고서의 자료 확인 범위는 수집 상태입니다. 선택적 컨센서스의 미확보·오류·발표 시각 미확인을
  긍정·부정 근거, 자동 가감점 또는 별도 매수·매도 게이트로 사용하지 마세요.
- 예상 EPS(forecast EPS)는 실제 실적이 아니며 F1의 최근 분기 실제 영업이익 근거를 대신하지 않습니다.
- 선택적 컨센서스 누락과 필수 재무 근거 부족은 다릅니다. 미충족·미검증 F1–F4를 통과로 꾸미지 마세요.
- 매수 당시 보고서·저장 시나리오의 전망은 과거 전망입니다. 현재 SELL 전망으로 승격하지 마세요.
- 수집 시각은 전망 발표 시각이 아닙니다. 기간·단위·실적/전망을 구분하고 없는 peer 평균을 만들지 마세요.
- 회사 가이던스와 애널리스트 컨센서스, 전체 성장과 유기적 성장·인수 효과를 구분하세요.
  원문에 없는 성장 구성을 계산하거나 같은 것으로 간주하지 마세요.
- 기존 F1–F4, 점수, 손익비, 포지션 한도, 손절 규칙을 유지하세요.
''' if language == 'ko' else '''
## Analyst Evidence Interpretation
- The report's collection scope describes collection only. Missing/error/unverified publication time
  for optional consensus is neither positive nor negative evidence, an automatic score adjustment,
  nor a separate buy/sell gate.
- Expected forecast EPS is not actual earnings and cannot replace F1's recent actual operating-profit evidence.
- This applies only to optional consensus; do not fabricate a pass for unmet or unverified required F1–F4 fundamentals.
- Forecasts in the historical purchase report or stored scenario are historical, not current SELL forecasts.
- Capture time is not forecast publication time. Preserve periods, units and actual/forecast distinctions;
  do not invent peer averages. Preserve existing F1–F4, scores, R/R, position limits and stop rules.
- Company guidance is not analyst consensus. Distinguish total, organic and acquisition-driven growth
  when disclosed; do not fabricate a growth decomposition missing from the source.
'''
        assert prompt.count(analyst_appendix) == 1
        assert prompt.endswith(analyst_appendix)
        legacy_prompt = prompt[:-len(analyst_appendix)]
        # Preserve the previously reviewed flow and ownership wording allowance.
        from prism_core.flow_evidence import us_flow_interpretation_contract
        appendix = us_flow_interpretation_contract(language)
        assert legacy_prompt.count(appendix) == 1
        assert legacy_prompt.endswith(appendix)
        legacy_prompt = legacy_prompt[:-len(appendix)]
        replacements = (
            [("이 값은 거래량 동반 가격 하락의 누적 경고이며, 기관 매도를 직접 관측한 값은 아닙니다.",
              "이 값이 높을수록 기관 분배가 진행 중이라는 천장 경고입니다.")]
            if language == "ko" else
            [("Distribution days (price-volume proxies with ≥ -0.2% close on rising volume) are",
              "Distribution days (institutional selling sessions with ≥ -0.2% close on rising volume) are"),
             ("A higher count of distribution days warns of repeated price-volume weakness, not confirmed institutional selling.",
              "The HIGHER this count of distribution days, the more institutional selling is underway.")]
        )
        replacements += (
            [('''- **장 후반도 실제 마감 전에는 미완성 관측값**입니다. 현재 호가는 기존 진입·수량 산정에
  사용할 수 있으며 장중 트리거를 일괄 거절하지 마세요. 확정 종가·완성 일봉과 구분하세요.
- 정규장·조기폐장의 실제 세션 종료와 자료 수집 시각·완결성을 함께 확인하세요. 마감 전 수집한
  캐시를 마감 후에 읽었다고 확정봉으로 승격하지 마세요. 확정봉 판단은 실제 완료 관측에만 근거하세요.''',
              '''- **장 후반 (마감 1시간 전 이후)**: 당일 데이터가 사실상 확정. 모든 기술적 지표를 사용해도 됩니다.
- 분석이 미국 시장 마감 후(아침 KST)에 실행되는 경우 직전 거래일 종가 기준으로 판단하십시오.''')]
            if language == 'ko' else
            [('''- **The closing hour is still in-progress before actual close.** Current quotes may support existing
  entry/sizing rules; do not blanket-reject intraday triggers. They are not confirmed closes or completed daily bars.
- Check the actual regular/early close plus capture time and data finality. A cache captured before close
  does not become final when read after close. Completed-bar judgments require actually completed observations.''',
              '''- **Closing hour onward**: today's data is settled. All technical indicators are usable.
- When the analysis runs after US market close (KST morning), use the most recent settled session.''')]
        )
        for current, previous in replacements:
            assert legacy_prompt.count(current) == 1
            legacy_prompt = legacy_prompt.replace(current, previous)
        expected = {
            # 2026-09-18 reviewed target-first/industry-scope/ownership wording repair.
            # JSON output and unrelated execution rules retain their original hashes.
            "ko": ("bc1a2d324c9ce62a4e874968d72eeb0b11bb7d69a6208b26231c394c3605aaba",
                   "515130759f31ca1282749d6d3b2d10bc9704c69f86fc84484dd1a26c332ee646"),
            "en": ("e3ece04d6df33dd1e6a292a105c3cc94b438a33a692b033758d820ab08261cea",
                   "c73c9066e6b9a043d70102cf1912fc6f1608fc544d6ef96e1555322e0aa6e031"),
        }
        tool_heading = "## 도구 사용" if language == "ko" else "## Tool Usage"
        json_heading = "## JSON 응답 형식" if language == "ko" else "## JSON Response Format"
        assert hashlib.sha256(legacy_prompt.split(tool_heading)[0].encode()).hexdigest() == expected[language][0]
        assert hashlib.sha256(legacy_prompt[legacy_prompt.index(json_heading):].encode()).hexdigest() == expected[language][1]
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
