"""Point-in-time capture never changes inputs or invents complete calendars."""
import hashlib

import pandas as pd
import pytest

from observability.trend_research import (
    build_snapshot,
    capture_enabled,
    features_from_events,
)
from prism_core.trend_quality_research import digest


def frame(end="2026-09-09", periods=80):
    return pd.DataFrame({"Open": 100., "High": 102., "Low": 99., "Close": 101.,
                         "private_extra": "do not capture"}, index=pd.bdate_range(end=end, periods=periods))


def capture(stock, benchmark=None, **kwargs):
    return build_snapshot(stock, stock if benchmark is None else benchmark, market="KR",
                          source="cached_feed", observed_at="2026-09-09T08:00:00Z", **kwargs)


def test_bounded_whitelist_hash_immutable():
    original = frame()
    before = original.copy(deep=True)
    result = capture(original)
    assert result["status"] == "OK"
    assert len(result["bars"]) == 60
    assert result["source_hash"] == digest(result["bars"])
    assert "private_extra" not in str(result)
    pd.testing.assert_frame_equal(original, before)
    assert result["gap_reference"] == "benchmark_observed_sessions_not_official_calendar"


def test_prefix_future_and_incomplete_bar_removed():
    full = frame(end="2026-09-18", periods=100)
    before_close = "2026-09-09T06:29:59Z"
    a = build_snapshot(full, full, market="KR", source="x", observed_at=before_close)
    prefix = full.loc[:"2026-09-08"]
    b = build_snapshot(prefix, prefix, market="KR", source="x", observed_at=before_close)
    assert a == b
    assert a["bars"][-1]["close_at"].startswith("2026-09-08")


@pytest.mark.parametrize("day,utc_close", [("2026-03-09", "20:00:00Z"), ("2026-11-02", "21:00:00Z")])
def test_us_dst_session_labels(day, utc_close):
    data = frame(end=day)
    result = build_snapshot(data, data, market="US", source="x", observed_at=f"{day}T{utc_close}")
    assert result["bars"][-1]["close_at"].startswith(day + "T16:00")
    assert len(result["bars"]) == 60


def test_early_close_conservatively_deferred():
    data = frame(end="2026-11-27")
    result = build_snapshot(data, data, market="US", source="x", observed_at="2026-11-27T19:00:00Z")
    assert not result["bars"][-1]["close_at"].startswith("2026-11-27")


@pytest.mark.parametrize("kind", ["reversed", "duplicate", "bad_ohlc", "nan"])
def test_reject_invalid_frame(kind):
    data = frame()
    if kind == "reversed":
        data = data.iloc[::-1]
    elif kind == "duplicate":
        data = pd.concat([data, data.iloc[-1:]])
    else:
        data.loc[data.index[-1], "Close"] = 200 if kind == "bad_ohlc" else float("nan")
    result = capture(data)
    assert result["status"] == "MISSING"
    assert result["bars"] == []


def test_missing_reference_and_stock_gap():
    data = frame()
    assert "BENCHMARK_REFERENCE_MISSING" in capture(data, data.iloc[:0])["data_quality_flags"]
    assert "OBSERVED_SESSION_GAP" in capture(data.drop(data.index[-5]), data)["data_quality_flags"]
    # A missing day in both feeds cannot be labeled an exchange-calendar validation.
    both = data.drop(data.index[-5])
    assert capture(both)["gap_reference"] == "benchmark_observed_sessions_not_official_calendar"


def event_fixture():
    ref = hashlib.sha256(b"decision1").hexdigest()[:16]
    packet = {"analysis_rows": [{"decision_ref": ref}]}
    event = {"event_type": "candidate.evaluated", "decision_id": "decision1", "market": "KR",
             "timestamp": "2026-09-09T08:00:01Z", "policy_version": "policy1",
             "attributes": {"trigger_mode": "afternoon", "regime": "sideways", "research_context": capture(frame())}}
    return packet, event


def test_exact_join_and_metadata():
    packet, event = event_fixture()
    row = features_from_events(packet, [event])["rows"][0]
    assert row["status"] == "OK"
    assert row["session"] == "afternoon"
    assert row["session_date"] == "2026-09-09"
    assert row["policy_version"] == "policy1"
    event["decision_id"] = "different"
    assert features_from_events(packet, [event])["rows"][0]["status"] == "MISSING"


def test_future_observation_and_missing_mode():
    packet, event = event_fixture()
    event["attributes"]["research_context"]["observed_at"] = "2026-09-09T08:00:02Z"
    del event["attributes"]["trigger_mode"]
    row = features_from_events(packet, [event])["rows"][0]
    assert "OBSERVATION_AFTER_DECISION" in row["data_quality_flags"]
    assert "STRATIFICATION_METADATA_MISSING" in row["data_quality_flags"]


def test_duplicate_events_rejected_and_capture_can_be_disabled(monkeypatch):
    packet, event = event_fixture()
    assert features_from_events(packet, [event, event])["rows"][0]["status"] == "MISSING"
    monkeypatch.delenv("TREND_RESEARCH_CAPTURE_ENABLED", raising=False)
    assert capture_enabled()
    monkeypatch.setenv("TREND_RESEARCH_CAPTURE_ENABLED", "false")
    assert not capture_enabled()
