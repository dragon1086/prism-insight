"""No-network descriptive snapshot rankings, including unknown data contracts."""
import numpy as np
import pandas as pd
import pytest

from prism_core.snapshot_price_leaders import (
    build_snapshot_price_leaders,
    optional_snapshot_price_leaders,
)


def build(current, prior=None, sectors=None, observed="20260916", prior_date="20260915"):
    snapshot = pd.DataFrame({"Close": current})
    snapshot.attrs["observed_date"] = observed
    previous = pd.DataFrame({"Close": prior if prior is not None else dict.fromkeys(current, 100)})
    return build_snapshot_price_leaders(
        snapshot, previous, sectors if sectors is not None else dict.fromkeys(current, "IT"),
        prior_date=prior_date, source="KIS_FIXTURE", trigger_mode="morning")


def test_returns_ranks_ties_and_descriptive_scope():
    packet = build({"B": 120, "A": 120, "C": 110, "D": 110, "E": 90})
    group = packet["groups"]["IT"]
    assert [row["ticker"] for row in group["leaders"]] == ["A", "B", "C"]
    assert [row["rank"] for row in group["leaders"]] == [1, 1, 3]
    assert group["cutoff_ties_omitted"] == 1
    assert group["leaders"][0]["return_pct"] == 20
    assert group["leaders"][0]["tie_count"] == 2
    assert packet["status"] == "OK"
    assert packet["observed_date"] == "2026-09-16"
    assert packet["intraday"] is True
    assert packet["is_business_leadership"] is False
    assert packet["is_market_cap_rank"] is False


@pytest.mark.parametrize("date", [None, "", "invalid", "20260914", "20260915"])
def test_no_invented_observed_date(date):
    packet = build({"A": 100}, observed=date)
    assert packet["status"] == "UNKNOWN"
    assert packet["groups"] == {}


def test_partial_coverage_records_exclusive_missing_reasons():
    packet = build({"A": 100, "B": 200, "C": 300, "D": float("nan")},
                   prior={"A": 100, "D": 100}, sectors={"A": "IT", "C": "IT", "D": "IT"})
    assert packet["status"] == "PARTIAL"
    assert packet["coverage"] == {"requested_count": None, "observed_count": 4, "eligible_count": 1,
                                  "missing_classification_count": 1, "missing_previous_count": 1,
                                  "invalid_price_count": 1}


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), None, "bad", True, False, np.bool_(True)])
def test_invalid_previous_price_unknown(price):
    packet = build({"A": 100}, prior={"A": price})
    assert packet["status"] == "UNKNOWN"
    assert packet["coverage"]["invalid_price_count"] == 1


def test_missing_classification_unknown():
    assert build({"A": 100}, sectors={})["status"] == "UNKNOWN"


def test_opt_in_off_does_not_touch_inputs(monkeypatch):
    monkeypatch.delenv("PRISM_REPORT_INSIGHT_PREFETCH", raising=False)
    assert optional_snapshot_price_leaders(None, None, None, prior_date=None,
                                           source=None, trigger_mode=None) is None


def test_duplicate_index_unknown_and_inputs_unchanged():
    snapshot = pd.DataFrame({"Close": [100, 200]}, index=["A", "A"])
    snapshot.attrs["observed_date"] = "20260916"
    before = snapshot.copy(deep=True)
    result = build_snapshot_price_leaders(snapshot, snapshot.copy(), {"A": "IT"},
                                          prior_date="20260915", source="KIS", trigger_mode="afternoon")
    assert result["status"] == "UNKNOWN"
    pd.testing.assert_frame_equal(snapshot, before)


@pytest.mark.parametrize("flag", ["1", "true"])
def test_malformed_optional_metadata_fails_open_without_details(monkeypatch, flag):
    monkeypatch.setenv("PRISM_REPORT_INSIGHT_PREFETCH", flag)
    result = optional_snapshot_price_leaders(None, None, None, prior_date=None,
                                            source="/private/secret", trigger_mode="morning")
    assert result["status"] == "UNKNOWN"
    assert result["contract"] == "snapshot_price_leaders_v1"
    assert "private" not in str(result)


def test_universe_hash_stable_independent_of_row_order():
    assert build({"A": 100, "B": 200})["universe_sha256"] == build({"B": 200, "A": 100})["universe_sha256"]
    assert build({"A": 100})["universe_sha256"] != build({"A": 100, "B": 100})["universe_sha256"]


@pytest.mark.parametrize("source", [None, "unknown", "/private/secret", "bad source", "x" * 65])
def test_source_required(source):
    snapshot = pd.DataFrame({"Close": [100]}, index=["A"])
    snapshot.attrs["observed_date"] = "20260916"
    result = build_snapshot_price_leaders(snapshot, snapshot, {"A": "IT"},
                                         prior_date="20260915", source=source, trigger_mode="morning")
    assert result["reason"] == "missing_or_invalid_source"
