"""Hand-math and false-positive controls for the frozen research statistics."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import numpy as np
import pytest

from analysis.mixture_statistics import (
    CELLS, DAY_MS, bootstrap_max_error, evaluate_research_gate, summarise_nav,
    train_reference_scale,
)


def ms(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp() * 1000)


def summary(values, start="2023-01-01", **kwargs):
    start_ms = ms(start)
    rows = [{"timestamp_ms": start_ms + (i + 1) * DAY_MS, "nav": v} for i, v in enumerate(values)]
    return summarise_nav(rows, start_ms=start_ms, end_ms=start_ms + len(values) * DAY_MS,
                         mtm_mdd=kwargs.pop("mtm_mdd", 0.5), completed_campaigns=kwargs.pop("completed_campaigns", 2), **kwargs)


def test_first_day_fee_actual_elapsed_cagr_and_sample_volatility():
    result = summary([9900, 10100], turnover=1.2, campaign_pnls=[200, -100])
    assert result["status"] == "OK"
    assert result["daily_returns"] == pytest.approx([-0.01, 10100 / 9900 - 1])
    assert result["cagr"] == pytest.approx(1.01 ** (365.25 / 2) - 1)
    assert result["daily_volatility"] == pytest.approx(np.std([-0.01, 10100 / 9900 - 1], ddof=1))
    assert result["mtm_mdd"] == 0.5
    assert result["daily_close_mdd"] == pytest.approx(0.01)
    assert result["profit_factor"] == 2
    assert result["payoff_ratio"] == 2
    assert result["turnover"] == 1.2
    assert result["completed_campaigns"] == 2
    json.dumps(result, allow_nan=False)


def test_leap_year_and_continuous_yearly_ratios_without_rebasing():
    days = (ms("2026-01-01") - ms("2023-01-01")) // DAY_MS
    values = [11000] * 365 + [12100] * 366 + [13310] * 365
    result = summary(values, mtm_mdd=0)
    assert days == result["daily_count"] == 1096
    assert result["yearly_returns"] == pytest.approx({"2023": 0.1, "2024": 0.1, "2025": 0.1})
    assert result["cagr"] == pytest.approx(1.331 ** (365.25 / 1096) - 1)
    assert result["positive_month_fraction"] == 3 / 36
    assert result["worst_month"] == 0
    assert result["daily_returns"][365] == pytest.approx(0.1)


def test_zero_month_excluded_and_underwater_is_labeled_daily_close():
    result = summary([9900] * 30 + [10000] + [10000] * 28, underwater_days=30.5)
    assert result["monthly_returns"] == {"2023-01": 0, "2023-02": 0}
    assert result["positive_month_fraction"] == 0
    assert result["daily_close_underwater_days"] == 31
    assert result["underwater_days"] == 30.5


@pytest.mark.parametrize("values", [[0], [-1], [float("nan")], [float("inf")]])
def test_invalid_nav_is_never_silently_dropped(values):
    assert summary(values)["status"] == "INVALID_RESEARCH"


@pytest.mark.parametrize("timestamps", [[2, 1], [1, 1], [1, 3]])
def test_duplicate_unordered_missing_days(timestamps):
    start = ms("2023-01-01")
    rows = [{"timestamp_ms": start + n * DAY_MS, "nav": 10000} for n in timestamps]
    result = summarise_nav(rows, start_ms=start, end_ms=start + 3 * DAY_MS, mtm_mdd=0, completed_campaigns=0)
    assert result["status"] == "INVALID_RESEARCH"


def test_endpoint_and_last_ms_match_but_inception_row_is_rejected():
    start = ms("2023-01-01")
    args = dict(start_ms=start, end_ms=start + DAY_MS, mtm_mdd=0, completed_campaigns=0)
    endpoint = summarise_nav([{"timestamp_ms": start + DAY_MS, "nav": 10000}], **args)
    final_ms = summarise_nav([{"timestamp_ms": start + DAY_MS - 1, "nav": 10000}], **args)
    assert endpoint == final_ms
    assert summarise_nav([{"timestamp_ms": start, "nav": 10000}], **args)["status"] == "INVALID_RESEARCH"


def test_campaign_count_not_inferred_from_fills_and_invalid_inputs_fail_closed():
    assert summary([10000], completed_campaigns=0)["completed_campaigns"] == 0
    assert summary([10000], completed_campaigns=1, campaign_pnls=[1, 2])["status"] == "INVALID_RESEARCH"
    assert summary([10000], completed_campaigns=1.5)["status"] == "INVALID_RESEARCH"
    assert summary([9000], mtm_mdd=0.01)["status"] == "INVALID_RESEARCH"
    assert summary([10000], initial_nav=20000)["status"] == "INVALID_RESEARCH"
    assert summary([10000], campaign_pnls=[1, 2])["profit_factor"] is None


def test_top5_removed_log_returns_hand_math_and_bad_denominator():
    logs = np.array([0.01] * 10 + [-0.001])
    result = summary(10000 * np.exp(np.cumsum(logs)))
    assert result["top5_removed_return"] == pytest.approx(np.expm1(0.049))
    assert result["top5_share"] == pytest.approx(0.05 / 0.099)
    for values in ([10000] * 6, [9900] * 6):
        result = summary(values)
        assert result["top5_share"] is None
        assert result["concentration_status"] == "NONPOSITIVE_LOG_GROWTH"
        json.dumps(result, allow_nan=False)


def test_reference_scale_shared_minimum_and_invalid_vols():
    assert train_reference_scale({"OHLC": 0.2, "OLHC": 0.1}, {"OHLC": 0.1, "OLHC": 0.2})["scale"] == 0.5
    for value in (0, float("nan"), None, -1):
        assert train_reference_scale({"OHLC": value, "OLHC": 0.1}, {"OHLC": 0.1, "OLHC": 0.2})["status"] == "INSUFFICIENT"
    assert train_reference_scale({}, {})["status"] == "INSUFFICIENT"


def fixture_differences(n=70):
    timestamps = [ms("2023-12-01") + (i + 1) * DAY_MS for i in range(n)]
    a = np.linspace(-0.001, 0.003, n)
    return {p: {"a": a.tolist(), "b": (-a / 2).tolist()} for p in ("OHLC", "OLHC")}, timestamps


def test_bootstrap_centered_error_hand_calculation_within_year_indices():
    differences, times = fixture_differences()
    result = bootstrap_max_error(differences, times, draws=15, require_full_period=False)
    rng = np.random.default_rng(20260906)
    matrix = np.asarray([differences["OHLC"][c] for c in ("a", "b")]).T
    means, errors = matrix.mean(axis=0), []
    for _ in range(15):
        ids = []
        for start, size in ((0, 31), (31, 39)):
            starts = rng.integers(0, size, size=(size + 29) // 30)
            ids.extend((start + ((starts[:, None] + np.arange(30)) % size).ravel()[:size]).tolist())
        errors.append(np.max(matrix[ids].mean(axis=0) - means))
    q = np.quantile(errors, 0.95)
    assert result["paths"]["OHLC"]["q95_max_error"] == pytest.approx(q)
    assert list(result["paths"]["OHLC"]["lower_bounds"].values()) == pytest.approx(means - q)
    assert result["paths"]["OHLC"] == result["paths"]["OLHC"]


def test_bootstrap_seed_column_permutation_duplicate_column_and_zero_alpha():
    differences, times = fixture_differences()
    kwargs = dict(draws=50, require_full_period=False)
    first = bootstrap_max_error(differences, times, **kwargs)
    assert first == bootstrap_max_error(differences, times, **kwargs)
    permuted = {p: dict(reversed(list(cols.items()))) for p, cols in differences.items()}
    assert first == bootstrap_max_error(permuted, times, **kwargs)
    duplicated = {p: {**cols, "duplicate": cols["a"]} for p, cols in differences.items()}
    second = bootstrap_max_error(duplicated, times, **kwargs)
    for p in differences:
        assert first["paths"][p]["lower_bounds"]["a"] == second["paths"][p]["lower_bounds"]["a"]
    zero = {p: {"zero": [0] * len(times)} for p in differences}
    assert bootstrap_max_error(zero, times, **kwargs)["paths"]["OHLC"]["lower_bounds"]["zero"] == 0


def test_bootstrap_full_family_period_and_missing_column_are_insufficient():
    differences, times = fixture_differences()
    assert bootstrap_max_error(differences, times)["status"] == "INSUFFICIENT"
    del differences["OLHC"]["b"]
    assert bootstrap_max_error(differences, times, require_full_period=False)["status"] == "INSUFFICIENT"
    times = [ms("2023-01-01") + (i + 1) * DAY_MS for i in range(1096)]
    differences = {p: {str(j): [0.0] * 1096 for j in range(14)} for p in ("OHLC", "OLHC")}
    assert bootstrap_max_error(differences, times, draws=2)["status"] == "OK"
    differences["OHLC"]["0"][1] = float("nan")
    assert bootstrap_max_error(differences, times, draws=2)["status"] == "INSUFFICIENT"


def gate_fixture():
    metric = dict(status="OK", cagr=0.2, mtm_mdd=0.2, total_return=0.5,
                  positive_month_fraction=0.55, yearly_returns={"2023": 0.1, "2024": 0.1, "2025": 0.1},
                  completed_campaigns=60, top5_removed_return=0.1, top5_share=0.5)
    return dict(selected_id="frozen", base={p: deepcopy(metric) for p in ("OHLC", "OLHC")},
                matched_references={p: {r: dict(deepcopy(metric), cagr=0.19) for r in ("single", "BH")} for p in ("OHLC", "OLHC")},
                adjusted_lowers={p: {r: 0.0001 for r in ("single", "BH")} for p in ("OHLC", "OLHC")},
                stress={cell: dict(deepcopy(metric), mtm_mdd=0.25) for cell in CELLS},
                partial={cell: dict(deepcopy(metric), mtm_mdd=0.25) for cell in CELLS})


def test_exact_boundaries_pass_but_never_activate_and_input_immutable():
    args = gate_fixture()
    before = deepcopy(args)
    result = evaluate_research_gate(**args)
    assert result["status"] == "RESEARCH_CANDIDATE"
    assert result["profitability_status"] == "INSUFFICIENT"
    assert result["auto_activate"] is False
    assert result["forward_completed_campaigns"] == 0
    assert args == before


@pytest.mark.parametrize("key,value", [("cagr", 0.199999), ("mtm_mdd", 0.200001),
    ("positive_month_fraction", 0.549999), ("completed_campaigns", 59),
    ("top5_removed_return", 0), ("top5_share", 0.500001), ("top5_share", None)])
def test_each_base_threshold_failure_is_preserved(key, value):
    args = gate_fixture()
    args["base"]["OLHC"][key] = value
    assert evaluate_research_gate(**args)["status"] == "NO_QUALIFYING_CANDIDATE"


def test_path_failure_strict_ci_reference_tie_and_partial_failure():
    for mutation in (lambda a: a["adjusted_lowers"]["OLHC"].update(BH=0),
                     lambda a: a["matched_references"]["OHLC"]["single"].update(cagr=0.2),
                     lambda a: a["partial"]["c2/d30/OLHC"].update(total_return=0),
                     lambda a: a["stress"]["c2/d30/OLHC"].update(mtm_mdd=0.250001)):
        args = gate_fixture()
        mutation(args)
        assert evaluate_research_gate(**args)["status"] == "NO_QUALIFYING_CANDIDATE"


def test_two_positive_stress_years_and_no_oos_reselection():
    args = gate_fixture()
    args["stress"]["c1/d5/OHLC"]["yearly_returns"]["2023"] = -0.1
    assert evaluate_research_gate(**args)["status"] == "RESEARCH_CANDIDATE"
    args["stress"]["c1/d5/OHLC"]["yearly_returns"]["2024"] = 0
    assert evaluate_research_gate(**args)["status"] == "NO_QUALIFYING_CANDIDATE"
    args = gate_fixture()
    args["train_eligible"] = False
    result = evaluate_research_gate(**args)
    assert result["status"] == "NO_QUALIFYING_CANDIDATE"
    assert result["selected_id"] == "frozen"


@pytest.mark.parametrize("mutate", [lambda a: a["base"].pop("OLHC"),
    lambda a: a["partial"].pop("c1/d5/OHLC"), lambda a: a["stress"].pop("c1/d5/OHLC"),
    lambda a: a["matched_references"]["OHLC"].pop("BH"),
    lambda a: a["adjusted_lowers"]["OLHC"].update(BH=float("nan")),
    lambda a: a["base"]["OHLC"].update(cagr=float("nan")),
    lambda a: a["base"]["OHLC"]["yearly_returns"].pop("2024")])
def test_missing_or_nonfinite_evidence_is_invalid_not_a_valid_loss(mutate):
    args = gate_fixture()
    mutate(args)
    assert evaluate_research_gate(**args)["status"] == "INVALID_RESEARCH"
