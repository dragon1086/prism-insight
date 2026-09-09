from copy import deepcopy
import json
from pathlib import Path
import pytest

from prism_core import kr_legacy_adx_calendar_v3 as core
from tools import collect_trend_replay_data as us
from tools.run_kr_legacy_adx_calendar_v3 import korean_report
from test_kr_legacy_adx_identity_v2 import dataset
from test_kr_legacy_adx_research import source_fixture


def inputs():
    record, data, schedule = dataset()
    for day in core.HOLIDAYS:
        schedule[day] = {"open": day + "T09:00:00+09:00", "close": day + "T15:30:00+09:00"}
    source = source_fixture([record])
    collection = {"kind": "KR_LEGACY_RECONSTRUCTED_PUBLIC_DAILY_IDENTITY_V2",
                  "source_artifact_sha256": source["artifact_sha256"], "registration_sha256": "v2",
                  "quarantined_v1_collection_sha256": "v1", "calendar_version": "fixture",
                  "schedule": schedule, "mapping": {"005930": core.v2.resolve_mapping("005930", [data])},
                  "datasets": [data]}
    collection["artifact_sha256"] = core.v2.v1.digest(collection)
    facts = json.loads((Path(__file__).resolve().parents[1] / "docs/entry-quality-experiments/kr-calendar-facts-v3-20260910.json").read_text())
    return source, collection, facts


def test_only_two_verified_holidays_removed_raw_identity_and_input_unchanged():
    _, collection, facts = inputs()
    before = deepcopy(collection)
    corrected = core.calendar_overlay(collection, facts, "v3")
    assert collection == before
    assert set(collection["schedule"]) - set(corrected["schedule"]) == set(core.HOLIDAYS)
    assert "2026-03-27" in corrected["schedule"]
    assert corrected["datasets"] == collection["datasets"]
    assert corrected["mapping"] == collection["mapping"]
    assert corrected["calendar_overlay"]["runtime_calendar_changed"] is False
    assert corrected["parent_identity_v2_artifact_sha256"] == collection["artifact_sha256"]
    facts["facts"].append({"session_date": "2026-03-27"})
    with pytest.raises(ValueError, match="unapproved"):
        core.calendar_overlay(collection, facts, "bad")


def test_ordinary_bad_ohlc_still_excluded_and_no_price_repair():
    source, collection, facts = inputs()
    record = source["records"][0]
    record["recorded_entry_at"] = "2026-04-06T01:00:00Z"
    record["recorded_exit_at"] = "2026-04-07T01:00:00Z"
    data = collection["mapping"]["005930"]["dataset"]
    bad = next(b for b in data["bars"] if b["session_date"] == "2026-03-27")
    bad["close"] = 0
    source["artifact_sha256"] = core.v2.v1.digest({k: v for k, v in source.items() if k != "artifact_sha256"})
    collection["source_artifact_sha256"] = source["artifact_sha256"]
    collection["artifact_sha256"] = core.v2.v1.digest({k: v for k, v in collection.items() if k != "artifact_sha256"})
    corrected = core.calendar_overlay(collection, facts, "v3")
    result = core.build_study(source, corrected)
    assert result["rows"][0]["feature"]["status"] == "MISSING"
    assert "INVALID_PRICE_SERIES" in result["rows"][0]["feature"]["reasons"]
    assert bad["close"] == 0


def test_holiday_record_entry_not_rolled_to_next_session():
    source, collection, facts = inputs()
    record = {**source["records"][0], "recorded_entry_at": "2026-06-03T01:00:00Z",
              "recorded_exit_at": "2026-06-04T01:00:00Z"}
    corrected = core.calendar_overlay(collection, facts, "v3")
    f = core.v2.feature(record, corrected["mapping"]["005930"], corrected["schedule"])
    assert f["reasons"] == ["ENTRY_DATE_NOT_REGULAR_SESSION"]
    assert record["recorded_entry_at"] == "2026-06-03T01:00:00Z"


def test_us_normalization_frozen_across_research_overlay():
    _, collection, facts = inputs()
    request = {"market": "US", "ticker": "AAPL", "interval": "1d",
               "start": "2026-01-01T00:00:00Z", "end": "2026-01-08T00:00:00Z"}
    response = {"exchange": "NMS", "retrieved_at": "2026-09-09T18:00:00Z",
                "raw_rows": [{"provider_timestamp": "2026-01-05T00:00:00-05:00",
                              "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}]}
    before = us.normalize(request, response)
    core.calendar_overlay(collection, facts, "v3")
    assert us.normalize(request, response) == before
    assert before["calendar"] == "NASDAQ"


def test_family12_not_narrower_and_output_provenance():
    source, collection, facts = inputs()
    corrected = core.calendar_overlay(collection, facts, "v3")
    result = core.build_study(source, corrected)
    assert result["registry"]["multiple_testing_family"] == 12
    assert result["books"]["book-a"]["excluded_winner_coverage"]["original_scoped_winner_count"] == 1
    report = korean_report(result)
    assert "99.5833%" in report and "99.375%" not in report
    assert "2026-03-27" in report and "23996" in report and "24145" in report
    f = result["rows"][0]["feature"]
    f["adx14"] = 10
    rows = [{"source_record_ref": str(i), "recorded_return_pct": i - 10,
             "feature": f, "entry_session_date": str(i)} for i in range(40)]
    old = core.v2.metrics(rows, "H1")["bonferroni_date_cluster_interval_pp"]
    new = core.interval12(rows, "H1")
    assert new[0] <= old[0] <= old[1] <= new[1]
