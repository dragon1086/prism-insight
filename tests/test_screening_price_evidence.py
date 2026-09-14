"""Price evidence is descriptive, never a new screening gate."""
import ast
from pathlib import Path

import pandas as pd
import pytest

from prism_core.screening_price_evidence import build_screening_price_evidence

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("high,status,ratio", [
    (101, "OBSERVED_HIGH_ABOVE_REFERENCE", .2),
    (100, "NO_OBSERVED_HIGH_ABOVE_REFERENCE", None),
    (95, "NO_OBSERVED_HIGH_ABOVE_REFERENCE", None),
    (125, "OBSERVED_HIGH_ABOVE_REFERENCE", 5),
])
def test_headroom_is_not_a_target_or_breakout_gate(high, status, ratio):
    out = build_screening_price_evidence(100, [high] * 3, .05, "20260914")
    assert out["status"] == status
    assert out["headroom_to_assumed_risk_ratio"] == (pytest.approx(ratio) if ratio is not None else None)
    assert out["scenario_risk_reward_ratio"] is None
    assert out["setup_type"] == "UNCLASSIFIED"
    assert out["bar_finality"] == "UNKNOWN"


@pytest.mark.parametrize("highs", [[], [101], [101, 102], [101, None, 102],
                                      [101, float("inf"), 102], [101, -1, 102]])
def test_missing_is_not_good_or_bad(highs):
    out = build_screening_price_evidence(100, highs, .05, "20260914")
    assert out["status"] == "MISSING"
    assert out["headroom_to_assumed_risk_ratio"] is None


@pytest.mark.parametrize("price,width", [(0, .05), (float("nan"), .05),
                                         (100, 0), (100, 1), (100, float("inf"))])
def test_bad_inputs_are_json_safe(price, width):
    import json
    out = build_screening_price_evidence(price, [101] * 3, width, "20260914")
    assert out["status"] == "MISSING"
    json.dumps(out, allow_nan=False)


def test_extreme_finite_inputs_do_not_emit_infinity():
    import json
    out = build_screening_price_evidence(1, [1e308] * 3, .9, "20260914")
    assert out["status"] == "MISSING"
    json.dumps(out, allow_nan=False)


@pytest.mark.parametrize("width", [.05, .07, .08])
def test_fixed_width_is_disclosed_not_claimed_as_support(width):
    out = build_screening_price_evidence(100, [110] * 3, width, "20260914")
    assert out["headroom_to_assumed_risk_ratio"] == pytest.approx(.1 / width)
    assert out["stop_basis"] == "LEGACY_TRIGGER_FIXED_WIDTH_NOT_STRUCTURAL_SUPPORT"


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    subset = ast.Module(body=[n for n in tree.body
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and n.name in names], type_ignores=[])
    exec(compile(subset, str(path), "exec"), namespace)  # noqa: S102 - trusted repository functions
    return namespace


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("high", [101, 95, 125, None])
def test_real_screening_functions_do_not_invent_scenario_and_reuse_one_fetch(market, high):
    from unittest.mock import Mock
    path = ROOT / ("trigger_batch.py" if market == "KR" else "prism-us/us_trigger_batch.py")
    frame = pd.DataFrame({"High": [high] * 3}) if high else pd.DataFrame()
    fetch = Mock(return_value=frame)
    ns = load_functions(path, {"calculate_agent_fit_metrics", "score_candidates_by_agent_criteria"}, {
        "pd": pd, "logger": Mock(), "get_multi_day_ohlcv": fetch,
        "TRIGGER_CRITERIA": {"default": {"rr_target": 1.2, "sl_max": .05}},
        "build_screening_price_evidence": build_screening_price_evidence,
    })
    result = ns["score_candidates_by_agent_criteria"](
        pd.DataFrame({"Close": [100.]}, index=["TEST"]), "20260914")
    columns = (["agent_fit_score", "risk_reward_ratio", "target_price", "stop_loss_price", "stop_loss_pct"]
               if market == "KR" else
               ["AgentFitScore", "RiskRewardRatio", "TargetPrice", "StopLossPrice", "StopLossPct"])
    for column in columns:
        assert result.loc["TEST", column] is None
    assert fetch.call_count == 1
    evidence = result.loc["TEST", "screening_price_evidence"]
    assert evidence["observed_window_high"] == high
    assert evidence["schema_version"] == 2
    assert evidence["screening_score_basis"] == "NO_SYNTHETIC_RR"
    assert "legacy_score_basis" not in evidence


@pytest.mark.parametrize("market", ["KR", "US"])
def test_rank_weights_remove_unearned_agent_component(market):
    path = ROOT / ("trigger_batch.py" if market == "KR" else "prism-us/us_trigger_batch.py")
    tree = ast.parse(path.read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "REGIME_SCORE_WEIGHTS" for t in n.targets))
    weights = eval(compile(ast.Expression(assignment.value), str(path), "eval"))
    old = [(0.20, 0.30, 0.15), (0.25, 0.20, 0.20), (0.20, 0.15, 0.30),
           (0.15, 0.15, 0.35), (0.15, 0.15, 0.35)]
    from itertools import product
    for (_, new_weights), prior in zip(weights.items(), old):
        assert len(new_weights) == 3
        assert sum(new_weights) == pytest.approx(1)
        candidates = list(product([0., .5, 1.], repeat=3))
        old_scores = [sum(x * w for x, w in zip(c, prior)) + .35 for c in candidates]
        new_scores = [sum(x * w for x, w in zip(c, new_weights)) for c in candidates]
        assert new_scores == pytest.approx([(s - .35) / .65 for s in old_scores])
        for i in range(len(candidates)):
            for j in range(i):
                if abs(old_scores[i] - old_scores[j]) > 1e-12:
                    assert (old_scores[i] > old_scores[j]) == (new_scores[i] > new_scores[j])


@pytest.mark.parametrize("market", ["KR", "US"])
def test_topdown_removes_confidence_bonus_on_fake_constant(market):
    path = ROOT / ("trigger_batch.py" if market == "KR" else "prism-us/us_trigger_batch.py")
    build = load_functions(path, {"_build_topdown_pool"}, {})["_build_topdown_pool"]
    sectors = {"A": "Alpha", "B": "Beta"}
    context = {"sector_map": sectors, "leading_sectors": [
        {"sector": "Alpha", "confidence": .9}, {"sector": "Beta", "confidence": .5}]}
    extra = {"sector_map": sectors} if market == "US" else {}
    old = pd.DataFrame({"score": [.40 + .35, .46 + .35]}, index=["A", "B"])
    new = pd.DataFrame({"score": [.40 / .65, .46 / .65]}, index=["A", "B"])
    before = build({"trigger": old}, context, "score", **extra)
    after = build({"trigger": new}, context, "score", **extra)
    assert [row[0] for row in before] == ["A", "B"]
    assert [row[0] for row in after] == ["B", "A"]
    assert after[0][2] == pytest.approx(.46 / .65 * 1.15)


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("blended", [False, True])
def test_batch_export_uses_json_null_and_truthful_score_version(market, blended):
    import json
    path = ROOT / ("trigger_batch.py" if market == "KR" else "prism-us/us_trigger_batch.py")
    tree = ast.parse(path.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_batch")
    agent = "agent_fit_score" if market == "KR" else "AgentFitScore"
    final = "final_score" if market == "KR" else "FinalScore"
    export = next(n for n in ast.walk(fn) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == f"'{agent}' in stocks_df.columns")
    version = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   and any(ast.unparse(t) == "stock_info['screening_score_version']" for t in n.targets))
    frame = pd.DataFrame({agent: [None], **({final: [.8]} if blended else {})}, index=["TEST"])
    namespace = {"stocks_df": frame, "stock_info": {}}
    exec(compile(ast.Module(body=[export, version], type_ignores=[]), str(path), "exec"), namespace)
    data = json.loads(json.dumps(namespace["stock_info"], allow_nan=False))
    assert data["screening_score_version"] == (
        "momentum_rs_extension_v2" if blended else "trigger_native_unblended")
    for field in ("target_price", "stop_loss_price", "stop_loss_pct", "risk_reward_ratio", "agent_fit_score"):
        assert data[field] is None


@pytest.mark.parametrize("path", ["stock_tracking_agent.py", "stock_tracking_enhanced_agent.py",
                                  "prism-us/us_stock_tracking_agent.py"])
def test_watchlist_does_not_substitute_trigger_rr(path):
    tree = ast.parse((ROOT / path).read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                  and n.name in {"_save_watchlist_item", "_save_watchlist_item_legacy"})
    assignments = [n for n in ast.walk(method) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "risk_reward_ratio" for t in n.targets)]
    assert len(assignments) == 1
    expression = ast.Expression(assignments[0].value)
    code = compile(expression, path, "eval")
    assert eval(code, {"scenario": {"risk_reward_ratio": .4},
                       "trigger_info": {"risk_reward_ratio": 3}}) == .4
    assert eval(code, {"scenario": {}, "trigger_info": {"risk_reward_ratio": 3}}) is None


@pytest.mark.parametrize("ratio,text", [(None, "N/A"), (0, "0.00"), (.4, "0.40")])
def test_performance_report_missing_ratio_is_not_zero(ratio, text):
    from performance_analysis_report import PerformanceAnalyzer
    analyzer = PerformanceAnalyzer.__new__(PerformanceAnalyzer)
    assert analyzer._fmt_ratio(ratio) == text
