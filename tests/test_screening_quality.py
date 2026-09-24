import json

import pandas as pd
import pytest

from prism_core.screening_quality import build_screening_quality_context as build


def history(closes):
    return pd.DataFrame({"Close": closes}, index=pd.bdate_range("2026-08-03", periods=len(closes)))


def observe(closes, **kwargs):
    return build(history(closes), "2026-09-15", **kwargs)


def test_smooth_noisy_and_downward_direction():
    smooth = observe(range(100, 121))
    noisy = observe([100 + i + (3 if i % 2 else 0) for i in range(21)])
    down = observe(range(120, 99, -1))
    assert smooth["directional_efficiency_20"] == pytest.approx(1)
    assert 0 < noisy["directional_efficiency_20"] < 1
    assert down["directional_efficiency_20"] == pytest.approx(-1)
    assert down["one_day_gain_contribution_fraction"] is None
    assert smooth["scoring_applied"] is False
    assert smooth["compatibility_status"] == "NEEDS_SCENARIO"


def test_flat_and_single_gap_are_not_quality_approval():
    flat = observe([100] * 21)
    gap = observe([100] * 10 + [120] * 11)
    assert flat["status"] == "FLAT"
    assert flat["directional_efficiency_20"] is None
    assert gap["directional_efficiency_20"] == pytest.approx(1)
    assert gap["one_day_gain_contribution_fraction"] == 1
    assert gap["compatibility_status"] == "NEEDS_SCENARIO"


def test_current_and_future_are_excluded_even_if_invalid():
    frame = history(range(100, 121))
    base = build(frame, "2026-09-01")
    frame.loc[pd.Timestamp("2026-09-01")] = float("nan")
    frame.loc[pd.Timestamp("2026-09-02")] = 99999
    result = build(frame, "2026-09-01")
    assert result["status"] == "OK"
    assert result["directional_efficiency_20"] == base["directional_efficiency_20"]
    assert result["completed_row_count"] == 21
    assert result["excluded_current_row_count"] == 1
    assert result["excluded_future_row_count"] == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1, None, "bad", True])
def test_invalid_completed_row_is_not_dropped(bad):
    closes = list(range(100, 123))
    closes[3] = bad
    result = observe(closes)
    assert result["status"] == "MISSING"
    assert result["reason"] == "INVALID_COMPLETED_CLOSE"
    json.dumps(result, allow_nan=False)


def test_short_history():
    assert observe(range(100, 120))["reason"] == "INSUFFICIENT_COMPLETED_HISTORY"


@pytest.mark.parametrize("mode,reason", [("duplicate", "DUPLICATE_SESSION"), ("reverse", "UNSORTED_SESSIONS"), ("numeric", "INVALID_HISTORY_DATES"), ("intraday", "INVALID_HISTORY_DATES")])
def test_ambiguous_dates_fail_closed(mode, reason):
    frame = history(range(100, 121))
    if mode == "duplicate":
        frame.index = [frame.index[0]] + list(frame.index[:-1])
    elif mode == "reverse":
        frame = frame.iloc[::-1]
    elif mode == "numeric":
        frame.index = range(21)
    else:
        frame.index = frame.index + pd.Timedelta(hours=1)
    assert build(frame, "2026-09-15")["reason"] == reason


def test_freshness_needs_expected_session_and_never_implies_calendar_validation():
    assert observe(range(21, 42))["freshness_status"] == "UNKNOWN"
    matched = observe(range(21, 42), expected_completed_session="2026-08-31")
    stale = observe(range(21, 42), expected_completed_session="2026-09-14")
    assert matched["freshness_status"] == "MATCH"
    assert matched["calendar_continuity_status"] == "UNKNOWN"
    assert stale["status"] == "MISSING"
    assert stale["reason"] == "EXPECTED_SESSION_MISMATCH"


def test_split_like_input_does_not_claim_adjustment_confirmation():
    result = observe([100] * 10 + [50] * 11)
    assert result["adjustment_basis"] == "UNCONFIRMED"
    assert result["directional_efficiency_20"] < 0
    assert result["compatibility_status"] == "NEEDS_SCENARIO"


def test_hash_is_deterministic_and_sensitive_to_evidence():
    first = observe(range(100, 121))
    assert first == observe(range(100, 121))
    assert first["input_hash"] != observe(range(101, 122))["input_hash"]
    assert first["input_hash"] != observe(range(100, 121), adjustment_basis="ADJUSTED")["input_hash"]
    assert len(first["input_hash"]) == 64


def test_last_21_closes_only_and_finite_extreme_prices():
    assert observe([200, 10] + list(range(100, 121)))["directional_efficiency_20"] == pytest.approx(1)
    result = observe([1e308, 1] * 10 + [1e308])
    assert result["status"] == "OK"
    json.dumps(result, allow_nan=False)


def test_timezone_labels_preserve_session_date():
    frame = history(range(100, 121))
    frame.index = frame.index.tz_localize("America/New_York")
    assert build(frame, "2026-09-01")["last_completed_session"] == "2026-08-31"
