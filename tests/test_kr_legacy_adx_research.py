from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import pytest

from prism_core import kr_legacy_adx_research as core
from tools import run_kr_legacy_adx_study as tool


def fixture():
    days = [(datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(100)]
    schedule = {d: {"open": d + "T09:00:00+09:00", "close": d + "T15:30:00+09:00"} for d in days}
    bars = [{"session_date": d, "bar_close_at": schedule[d]["close"], "high": 102 + i,
             "low": 98 + i, "close": 100 + i, "stock_splits": 0, "dividends": 0}
            for i, d in enumerate(days)]
    record = {"source_record_ref": "record-a", "legacy_book_ref": "book-a", "market": "KR",
              "book_scope_status": "PSEUDONYMOUS_LEGACY_SCOPE", "ticker": "005930",
              "trigger_type": core.TRIGGERS[0], "recorded_entry_at": days[70] + "T07:30:00Z",
              "recorded_exit_at": days[80] + "T07:30:00Z", "recorded_return_pct": 3}
    dataset = {"bars": bars, "source_sha256": "fixture", "retrieved_at": "2026-09-09T18:00:00Z"}
    return record, {"status": "OK", "dataset": dataset}, schedule


def test_entry_day_excluded_and_exact_60_bars():
    record, mapping, schedule = fixture()
    f = core.feature(record, mapping, schedule)
    assert f["status"] == "OK"
    assert f["bar_count"] == 60
    assert f["last_session"] < core.session_date(record["recorded_entry_at"])
    assert f["original_decision_at"] is None
    mapping["dataset"]["bars"][70]["close"] = 999999
    assert core.feature(record, mapping, schedule)["input_hash"] == f["input_hash"]


@pytest.mark.parametrize("where,reason", [(30, "SPLIT_IN_FEATURE_WINDOW"),
                                         (75, "SPLIT_DURING_HOLD_RECORDED_RETURN_UNRESOLVED")])
def test_splits_excluded_before_outcome_metrics(where, reason):
    record, mapping, schedule = fixture()
    mapping["dataset"]["bars"][where]["stock_splits"] = 2
    assert reason in core.feature(record, mapping, schedule)["reasons"]


def test_post_exit_uniform_scale_preserves_rules():
    record, mapping, schedule = fixture()
    before = core.feature(record, mapping, schedule)
    for bar in mapping["dataset"]["bars"]:
        for field in ("high", "low", "close"):
            bar[field] *= 0.5
    mapping["dataset"]["bars"][90]["stock_splits"] = 2
    after = core.feature(record, mapping, schedule)
    assert after["status"] == "OK"
    assert after["post_exit_split_events"] == 1
    for trial, _ in core.TRIALS:
        assert core.passes(trial, before) == core.passes(trial, after)
    for field in ("adx14", "plus_di14", "minus_di14", "er20"):
        assert before[field] == pytest.approx(after[field])


@pytest.mark.parametrize("index,reason", [(20, "MISSING_DUPLICATE_OR_STALE_SESSION"),
                                         (75, "HOLDING_SESSION_ACTION_COVERAGE_MISSING")])
def test_missing_sessions_never_assumed_no_action(index, reason):
    record, mapping, schedule = fixture()
    mapping["dataset"]["bars"].pop(index)
    assert reason in core.feature(record, mapping, schedule)["reasons"]


def test_ambiguous_suffix_and_no_data_excluded():
    def data(ticker):
        return {"request": {"ticker": ticker}, "bars": [{}], "exclusions": {}}
    assert tool.resolve_mapping("005930", [data("005930.KS"), data("005930.KQ")])["status"] == "AMBIGUOUS_SUFFIX"
    assert tool.resolve_mapping("005930", [])["status"] == "PRICE_DATA_MISSING"
    assert tool.resolve_mapping("005930", [data("005930.KS")])["provider_symbol"] == "005930.KS"
    assert tool.resolve_mapping("005930", [data("000001.KS")])["status"] == "PRICE_DATA_MISSING"


def test_mapping_preserves_raw_and_excludes_invalid_calendar_price():
    schedule = {"2026-01-05": {"open": "2026-01-05T09:00:00+09:00", "close": "2026-01-05T15:30:00+09:00"}}
    raw = {"provider_timestamp": "2026-01-05T00:00:00+09:00", "open": 10, "high": 11,
           "low": 9, "close": 10, "volume": 3, "stock_splits": 0, "dividends": 0}
    response = {"raw_rows": [raw, raw], "retrieved_at": "2026-09-09T18:00:00Z"}
    data = tool.normalize({"ticker": "005930.KS"}, response, schedule)
    assert data["raw_rows"] == response["raw_rows"]
    assert data["source_sha256"] == core.digest(response)
    assert data["exclusions"]["DUPLICATE_SESSION_DATE"] == 1
    assert tool.resolve_mapping("005930", [data])["status"] == "PRICE_DATA_MISSING"


def source_fixture(records):
    result = {"analysis_contract_version": "historical-closed-record-evidence-v1",
              "data_cutoff": core.CUTOFF, "canonical_strategy_book_verified": False,
              "records": records, "logical_source_sha256": "fixture"}
    result["artifact_sha256"] = core.digest(result)
    return result


def test_books_remain_separate_and_no_invented_decision_id():
    record, mapping, schedule = fixture()
    other = {**record, "legacy_book_ref": "book-b", "source_record_ref": "record-b"}
    source = source_fixture([record, other])
    collection = {"source_artifact_sha256": source["artifact_sha256"], "registration_sha256": "fixture",
                  "schedule": schedule, "mapping": {"005930": mapping}}
    collection["artifact_sha256"] = core.digest(collection)
    result = core.build_study(source, collection)
    assert len(result["books"]) == 2
    assert all(b["eligible_record_count"] == 1 for b in result["books"].values())
    assert "decision_ref" not in json.dumps(result)
    assert result["broker_fill_filter_applied"] is False
    assert result["canonical_strategy_book_verified"] is False
    assert result == core.build_study(source, collection)


def test_fixed_pairing_winner_loss_removal_and_small_sample_null():
    record, mapping, schedule = fixture()
    f = core.feature(record, mapping, schedule)
    f["adx14"] = 10
    base = {"feature": f, "entry_session_date": "2026-01-01"}
    rows = [{**base, "source_record_ref": "win", "recorded_return_pct": 20},
            {**base, "source_record_ref": "loss", "recorded_return_pct": -10}]
    m = core.metrics(rows, "H1")
    assert m["paired_no_entry_zero_delta_pp"] == -5
    assert m["delta_without_highest_winner_pp"] == 10
    assert m["winner_preservation_rate"] == 0
    assert m["loss_removal_rate"] == 1
    assert m["bonferroni_date_cluster_interval_pp"] is None
    assert core.metrics(rows, "baseline")["paired_no_entry_zero_delta_pp"] == 0


def test_sufficient_date_bootstrap_deterministic():
    record, mapping, schedule = fixture()
    f = core.feature(record, mapping, schedule)
    f["adx14"] = 10
    rows = [{"source_record_ref": str(i), "recorded_return_pct": -10,
             "feature": f, "entry_session_date": str(i)} for i in range(30)]
    assert core.metrics(rows, "H1")["bonferroni_date_cluster_interval_pp"] == [10, 10]


def test_source_hash_and_market_fail_closed():
    record, _, _ = fixture()
    source = source_fixture([record])
    core.validate_source(source)
    source["records"][0]["recorded_return_pct"] += 1
    with pytest.raises(ValueError, match="hash"):
        core.validate_source(source)


def test_requests_only_registered_triggers_same_base_codes():
    record, _, _ = fixture()
    records = [record, {**record, "trigger_type": None, "ticker": "000001"},
               {**record, "ticker": "BAD"}]
    requests = tool.requests_for({"records": records})
    assert [r["ticker"] for r in requests] == ["005930.KS", "005930.KQ"]
    assert all(r["market"] == "KR" and r["end"] == core.CUTOFF for r in requests)


def test_collector_refuses_existing_outputs(tmp_path):
    path = tmp_path / "raw.json"
    tool.write_new(path, {"x": 1})
    with pytest.raises(FileExistsError):
        tool.write_new(path, {"x": 2})
    assert json.loads(path.read_text()) == {"x": 1}


def test_split_boundary_frozen_from_original_dates_before_feature_exclusion():
    record, mapping, schedule = fixture()
    days = sorted(schedule)
    records = [{**record, "source_record_ref": str(i),
                "recorded_entry_at": days[i] + "T07:30:00Z",
                "recorded_exit_at": days[min(i + 1, 99)] + "T07:30:00Z"}
               for i in range(70, 90)]
    source = source_fixture(records)
    collection = {"source_artifact_sha256": source["artifact_sha256"], "registration_sha256": "fixture",
                  "schedule": schedule, "mapping": {"005930": mapping}}
    collection["artifact_sha256"] = core.digest(collection)
    first = core.build_study(source, collection)["books"]["book-a"]
    collection["mapping"]["005930"]["dataset"]["bars"].pop(85)
    collection["artifact_sha256"] = core.digest({k: v for k, v in collection.items() if k != "artifact_sha256"})
    second = core.build_study(source, collection)["books"]["book-a"]
    assert first["test_boundary"] == second["test_boundary"] == days[84]
    assert second["eligible_record_count"] < first["eligible_record_count"]
    assert second["partitions"]["train_purged"]["baseline"]["closed_legacy_record_count"] == 0
    assert second["independent_holdout"] is False
