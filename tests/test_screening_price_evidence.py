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
def test_real_screening_functions_preserve_score_and_reuse_one_fetch(market, high):
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
    prefix = "agent_fit_score" if market == "KR" else "AgentFitScore"
    assert result.loc["TEST", prefix] == 1
    assert fetch.call_count == 1
    evidence = result.loc["TEST", "screening_price_evidence"]
    assert evidence["observed_window_high"] == high
    assert evidence["legacy_score_basis"] == "MIN_15PCT_TARGET_FIXED_STOP_NOT_BUY_RR"


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
